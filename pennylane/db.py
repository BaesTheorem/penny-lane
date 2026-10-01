"""SQLite store for price history, community reports, predictions, alerts.

One row per (retailer, item, store, time) observation is the whole point:
the detector reads the markdown cadence from history, and history only
exists if every lookup is recorded. WAL mode so the web server and the
scanner can share the file.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import config
from .retailers.base import Observation, Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
  retailer TEXT NOT NULL, store_id TEXT NOT NULL, name TEXT, address TEXT,
  city TEXT, state TEXT, zip TEXT, lat REAL, lon REAL, distance REAL,
  watched INTEGER DEFAULT 0,
  PRIMARY KEY (retailer, store_id));
CREATE TABLE IF NOT EXISTS items (
  retailer TEXT NOT NULL, item_id TEXT NOT NULL, upc TEXT, sku TEXT, model TEXT,
  name TEXT, brand TEXT, url TEXT, dept TEXT, first_seen REAL, last_seen REAL,
  PRIMARY KEY (retailer, item_id));
CREATE INDEX IF NOT EXISTS items_upc ON items(upc);
CREATE INDEX IF NOT EXISTS items_sku ON items(retailer, sku);
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY, retailer TEXT NOT NULL, item_id TEXT NOT NULL,
  store_id TEXT NOT NULL, ts REAL NOT NULL, price REAL, original REAL,
  clearance_price REAL, promo TEXT, qty INTEGER, in_stock INTEGER,
  discontinued INTEGER, online_status TEXT, store_status TEXT);
CREATE INDEX IF NOT EXISTS obs_item ON observations(retailer, item_id, store_id, ts);
CREATE INDEX IF NOT EXISTS obs_ts ON observations(ts);
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY, source TEXT NOT NULL, retailer TEXT, item_id TEXT,
  sku TEXT, upc TEXT, name TEXT, price REAL, reported_at REAL, url TEXT,
  store_hint TEXT, fetched_at REAL, key TEXT UNIQUE);
CREATE INDEX IF NOT EXISTS reports_item ON reports(retailer, item_id);
CREATE INDEX IF NOT EXISTS reports_upc ON reports(upc);
CREATE TABLE IF NOT EXISTS predictions (
  retailer TEXT NOT NULL, item_id TEXT NOT NULL, store_id TEXT NOT NULL,
  computed_at REAL, score INTEGER, stage TEXT, next_step_at REAL,
  reasons TEXT, PRIMARY KEY (retailer, item_id, store_id));
CREATE TABLE IF NOT EXISTS watches (
  retailer TEXT NOT NULL, item_id TEXT NOT NULL, added_at REAL, note TEXT,
  PRIMARY KEY (retailer, item_id));
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY, ts REAL, kind TEXT, retailer TEXT, item_id TEXT,
  store_id TEXT, message TEXT, score INTEGER, seen INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, kind TEXT, started REAL, finished REAL, ok INTEGER,
  detail TEXT);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""

_local = threading.local()


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        _local.conn = conn
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---- writes -----------------------------------------------------------------

def upsert_store(s: Store, watched: bool | None = None) -> None:
    with tx() as c:
        c.execute(
            """INSERT INTO stores(retailer,store_id,name,address,city,state,zip,lat,lon,distance,watched)
               VALUES(?,?,?,?,?,?,?,?,?,?,COALESCE(?,0))
               ON CONFLICT(retailer,store_id) DO UPDATE SET name=excluded.name,
               address=excluded.address, city=excluded.city, state=excluded.state,
               zip=excluded.zip, lat=excluded.lat, lon=excluded.lon, distance=excluded.distance,
               watched=COALESCE(?, stores.watched)""",
            (s.retailer, s.store_id, s.name, s.address, s.city, s.state, s.zip, s.lat, s.lon,
             s.distance, None if watched is None else int(watched),
             None if watched is None else int(watched)))


def record(obs: Observation) -> None:
    """Record one observation and refresh the item's identity row."""
    with tx() as c:
        c.execute(
            """INSERT INTO items(retailer,item_id,upc,sku,model,name,brand,url,dept,first_seen,last_seen)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(retailer,item_id) DO UPDATE SET
               upc=COALESCE(NULLIF(excluded.upc,''),items.upc), sku=COALESCE(NULLIF(excluded.sku,''),items.sku),
               model=COALESCE(NULLIF(excluded.model,''),items.model), name=COALESCE(NULLIF(excluded.name,''),items.name),
               brand=COALESCE(NULLIF(excluded.brand,''),items.brand), url=COALESCE(NULLIF(excluded.url,''),items.url),
               dept=COALESCE(NULLIF(excluded.dept,''),items.dept), last_seen=excluded.last_seen""",
            (obs.retailer, obs.item_id, obs.upc, obs.sku, obs.model, obs.name, obs.brand, obs.url,
             obs.dept, obs.ts, obs.ts))
        c.execute(
            """INSERT INTO observations(retailer,item_id,store_id,ts,price,original,clearance_price,
               promo,qty,in_stock,discontinued,online_status,store_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (obs.retailer, obs.item_id, obs.store_id, obs.ts, obs.price, obs.original,
             obs.clearance_price, obs.promo, obs.qty,
             None if obs.in_stock is None else int(obs.in_stock),
             None if obs.discontinued is None else int(obs.discontinued), obs.online_status, obs.store_status))


def add_report(source: str, key: str, **f) -> bool:
    """Insert a community report; returns True when it is new."""
    with tx() as c:
        cur = c.execute(
            """INSERT OR IGNORE INTO reports(source,key,retailer,item_id,sku,upc,name,price,reported_at,
               url,store_hint,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (source, key, f.get("retailer"), f.get("item_id"), f.get("sku"), f.get("upc"),
             f.get("name"), f.get("price"), f.get("reported_at"), f.get("url"),
             f.get("store_hint"), time.time()))
        return cur.rowcount > 0


def put_prediction(retailer, item_id, store_id, score, stage, next_step_at, reasons) -> None:
    with tx() as c:
        c.execute(
            """INSERT INTO predictions(retailer,item_id,store_id,computed_at,score,stage,next_step_at,reasons)
               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(retailer,item_id,store_id) DO UPDATE SET
               computed_at=excluded.computed_at, score=excluded.score, stage=excluded.stage,
               next_step_at=excluded.next_step_at, reasons=excluded.reasons""",
            (retailer, item_id, store_id, time.time(), int(score), stage, next_step_at,
             json.dumps(reasons)))


def add_alert(kind, retailer, item_id, store_id, message, score) -> int:
    with tx() as c:
        cur = c.execute(
            "INSERT INTO alerts(ts,kind,retailer,item_id,store_id,message,score) VALUES(?,?,?,?,?,?,?)",
            (time.time(), kind, retailer, item_id, store_id, message, score))
        return cur.lastrowid or 0


def recent_alert_exists(kind, retailer, item_id, store_id, within_s=86400 * 3) -> bool:
    row = connect().execute(
        "SELECT 1 FROM alerts WHERE kind=? AND retailer=? AND item_id=? AND store_id=? AND ts>? LIMIT 1",
        (kind, retailer, item_id, store_id, time.time() - within_s)).fetchone()
    return row is not None


def add_watch(retailer, item_id, note="") -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO watches(retailer,item_id,added_at,note) VALUES(?,?,?,?)",
                  (retailer, item_id, time.time(), note))


def remove_watch(retailer, item_id) -> None:
    with tx() as c:
        c.execute("DELETE FROM watches WHERE retailer=? AND item_id=?", (retailer, item_id))


def start_run(kind) -> int:
    with tx() as c:
        return c.execute("INSERT INTO runs(kind,started) VALUES(?,?)", (kind, time.time())).lastrowid or 0


def finish_run(run_id, ok, detail="") -> None:
    with tx() as c:
        c.execute("UPDATE runs SET finished=?, ok=?, detail=? WHERE id=?",
                  (time.time(), int(ok), detail, run_id))


def kv_get(k, default=None):
    row = connect().execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(row["v"]) if row else default


def kv_set(k, v) -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, json.dumps(v)))


# ---- reads ------------------------------------------------------------------

def rows(sql, params=()) -> list[dict]:
    return [dict(r) for r in connect().execute(sql, params).fetchall()]


def count(sql, params=()) -> int:
    r = connect().execute(sql, params).fetchone()
    return int(r[0]) if r else 0


def one(sql, params=()) -> dict | None:
    r = connect().execute(sql, params).fetchone()
    return dict(r) if r else None


def watched_stores(retailer=None) -> list[dict]:
    if retailer:
        return rows("SELECT * FROM stores WHERE watched=1 AND retailer=? ORDER BY distance", (retailer,))
    return rows("SELECT * FROM stores WHERE watched=1 ORDER BY retailer, distance")


def history(retailer, item_id, store_id=None, limit=500) -> list[dict]:
    if store_id:
        return rows("SELECT * FROM observations WHERE retailer=? AND item_id=? AND store_id=? ORDER BY ts DESC LIMIT ?",
                    (retailer, item_id, store_id, limit))
    return rows("SELECT * FROM observations WHERE retailer=? AND item_id=? ORDER BY ts DESC LIMIT ?",
                (retailer, item_id, limit))


def latest(retailer, item_id, store_id) -> dict | None:
    return one("SELECT * FROM observations WHERE retailer=? AND item_id=? AND store_id=? ORDER BY ts DESC LIMIT 1",
               (retailer, item_id, store_id))


def item(retailer, item_id) -> dict | None:
    return one("SELECT * FROM items WHERE retailer=? AND item_id=?", (retailer, item_id))


def items_by_upc(upc) -> list[dict]:
    u = upc.strip().lstrip("0")
    return rows("SELECT * FROM items WHERE LTRIM(upc,'0')=? OR upc=?", (u, upc))


def watched_items() -> list[dict]:
    return rows("""SELECT w.*, i.name, i.sku, i.upc FROM watches w
                   LEFT JOIN items i ON i.retailer=w.retailer AND i.item_id=w.item_id ORDER BY added_at DESC""")


def report_items(retailer=None, since_days=60) -> list[dict]:
    since = time.time() - since_days * 86400
    if retailer:
        return rows("SELECT * FROM reports WHERE retailer=? AND fetched_at>? ORDER BY reported_at DESC",
                    (retailer, since))
    return rows("SELECT * FROM reports WHERE fetched_at>? ORDER BY reported_at DESC", (since,))
