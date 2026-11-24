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
from .classify import classify
from .retailers.base import Observation, Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
  retailer TEXT NOT NULL, store_id TEXT NOT NULL, name TEXT, address TEXT,
  city TEXT, state TEXT, zip TEXT, lat REAL, lon REAL, distance REAL,
  watched INTEGER DEFAULT 0,
  PRIMARY KEY (retailer, store_id));
CREATE TABLE IF NOT EXISTS items (
  retailer TEXT NOT NULL, item_id TEXT NOT NULL, upc TEXT, sku TEXT, model TEXT,
  name TEXT, brand TEXT, url TEXT, dept TEXT, category TEXT, ctype TEXT, first_seen REAL, last_seen REAL,
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
  sku TEXT, upc TEXT, name TEXT, price REAL, retail REAL, reported_at REAL, url TEXT,
  store_hint TEXT, fetched_at REAL, key TEXT UNIQUE, ctype TEXT, first_reported_at REAL,
  n_reports INTEGER, places TEXT, status TEXT);
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
        _migrate(conn)
        _local.conn = conn
    return conn


def _migrate(conn) -> None:
    """Additive column migrations for databases created by older versions."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reports)")}
    if "retail" not in cols:
        conn.execute("ALTER TABLE reports ADD COLUMN retail REAL")
    if "ctype" not in cols:
        conn.execute("ALTER TABLE reports ADD COLUMN ctype TEXT")
    if "first_reported_at" not in cols:
        conn.execute("ALTER TABLE reports ADD COLUMN first_reported_at REAL")
    # What the list itself says about its evidence: how many reports, where
    # (JSON {"states": {ST: n}, "cities": {ST: {city: n}}}), and its own status word.
    for col, kind in (("n_reports", "INTEGER"), ("places", "TEXT"), ("status", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE reports ADD COLUMN {col} {kind}")
    icols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    for col in ("category", "ctype"):
        if col not in icols:
            conn.execute(f"ALTER TABLE items ADD COLUMN {col} TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS items_ctype ON items(ctype)")
    if "max_original" not in icols:
        # The highest "original" price any check returned for the item. Kept on the item row
        # because MAX(original) over 2.7M observations cost 23 s per list load (2026-11-24).
        conn.execute("ALTER TABLE items ADD COLUMN max_original REAL")
        conn.execute("""UPDATE items SET max_original=(SELECT MAX(o.original) FROM observations o
                        WHERE o.retailer=items.retailer AND o.item_id=items.item_id)""")
    scols = {r[1] for r in conn.execute("PRAGMA table_info(stores)")}
    if "published" not in scols:
        # On the public site (publish.py): the metro set, chosen apart from what Alex watches.
        conn.execute("ALTER TABLE stores ADD COLUMN published INTEGER DEFAULT 0")
    conn.commit()


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
        ctype = classify(obs.name, obs.category, obs.retailer)
        c.execute(
            """INSERT INTO items(retailer,item_id,upc,sku,model,name,brand,url,dept,category,ctype,first_seen,last_seen,max_original)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(retailer,item_id) DO UPDATE SET
               upc=COALESCE(NULLIF(excluded.upc,''),items.upc), sku=COALESCE(NULLIF(excluded.sku,''),items.sku),
               model=COALESCE(NULLIF(excluded.model,''),items.model), name=COALESCE(NULLIF(excluded.name,''),items.name),
               brand=COALESCE(NULLIF(excluded.brand,''),items.brand), url=COALESCE(NULLIF(excluded.url,''),items.url),
               dept=COALESCE(NULLIF(excluded.dept,''),items.dept),
               category=COALESCE(NULLIF(excluded.category,''),items.category),
               ctype=CASE WHEN excluded.category<>'' OR items.ctype IS NULL OR items.ctype IN ('other','tools') THEN excluded.ctype ELSE items.ctype END,
               last_seen=excluded.last_seen,
               max_original=NULLIF(MAX(COALESCE(items.max_original,0), COALESCE(excluded.max_original,0)), 0)""",
            (obs.retailer, obs.item_id, obs.upc, obs.sku, obs.model, obs.name, obs.brand, obs.url,
             obs.dept, obs.category, ctype, obs.ts, obs.ts, obs.original))
        c.execute(
            """INSERT INTO observations(retailer,item_id,store_id,ts,price,original,clearance_price,
               promo,qty,in_stock,discontinued,online_status,store_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (obs.retailer, obs.item_id, obs.store_id, obs.ts, obs.price, obs.original,
             obs.clearance_price, obs.promo, obs.qty,
             None if obs.in_stock is None else int(obs.in_stock),
             None if obs.discontinued is None else int(obs.discontinued), obs.online_status, obs.store_status))


def add_report(source: str, key: str, **f) -> bool:
    """Insert a community report; returns True when it is new."""
    places = json.dumps(f["places"]) if f.get("places") else None
    with tx() as c:
        cur = c.execute(
            """INSERT OR IGNORE INTO reports(source,key,retailer,item_id,sku,upc,name,price,retail,reported_at,
               url,store_hint,fetched_at,ctype,first_reported_at,n_reports,places,status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (source, key, f.get("retailer"), f.get("item_id"), f.get("sku"), f.get("upc"),
             f.get("name"), f.get("price"), f.get("retail"), f.get("reported_at"), f.get("url"),
             f.get("store_hint"), time.time(), classify(f.get("name") or "", "", f.get("retailer") or ""),
             f.get("first_reported_at"), f.get("n_reports"), places, f.get("status") or None))
        if cur.rowcount == 0:
            # Existing row: refresh what moves (last seen, report counts, places) and fill what was missing.
            c.execute("""UPDATE reports SET reported_at=COALESCE(?, reported_at), retail=COALESCE(retail, ?),
                         first_reported_at=COALESCE(first_reported_at, ?), fetched_at=?,
                         store_hint=COALESCE(NULLIF(?, ''), store_hint), n_reports=COALESCE(?, n_reports),
                         places=COALESCE(?, places), status=COALESCE(?, status),
                         item_id=COALESCE(NULLIF(item_id, ''), ?) WHERE key=?""",
                      (f.get("reported_at"), f.get("retail"), f.get("first_reported_at"), time.time(),
                       f.get("store_hint") or "", f.get("n_reports"), places, f.get("status") or None,
                       f.get("item_id") or "", key))
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
    """A penny is the last markdown, so a penny alert (penny_confirmed,
    penny_likely, and penny_on_shelf from before 2026-11-24) never repeats."""
    since = 0 if kind.startswith("penny") else time.time() - within_s
    row = connect().execute(
        "SELECT 1 FROM alerts WHERE kind=? AND retailer=? AND item_id=? AND store_id=? AND ts>? LIMIT 1",
        (kind, retailer, item_id, store_id, since)).fetchone()
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


def published_stores(retailer=None) -> list[dict]:
    if retailer:
        return rows("SELECT * FROM stores WHERE published=1 AND retailer=? ORDER BY distance", (retailer,))
    return rows("SELECT * FROM stores WHERE published=1 ORDER BY retailer, distance")


def check_stores(retailer: str, with_published: bool) -> list[dict]:
    """Stores a scan checks: the watched ones, plus the published metro set
    for a lane the public site covers."""
    out = {s["store_id"]: s for s in watched_stores(retailer)}
    if with_published:
        for s in published_stores(retailer):
            out.setdefault(s["store_id"], s)
    return list(out.values())


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


def msrp(retailer, item_id, latest: dict | None = None) -> float | None:
    """Best known full price: the lane's original, else the original any other
    store reported (a store on clearance drops it), else the community retail.
    The current price counts only when it is not a clearance price."""
    if latest and latest.get("original"):
        return float(latest["original"])
    it = item(retailer, item_id) or {}
    if it.get("max_original"):
        return float(it["max_original"])
    row = one("""SELECT MAX(retail) m FROM reports WHERE retailer=? AND (item_id=? OR (?<>'' AND sku=?) OR (?<>'' AND upc=?))""",
              (retailer, item_id, it.get("sku") or "", it.get("sku") or "", it.get("upc") or "", it.get("upc") or ""))
    if row and row.get("m"):
        return float(row["m"])
    if latest and latest.get("price") and latest["price"] > 0.01 and not latest.get("clearance_price"):
        return float(latest["price"])
    return None


def hot_items(retailer, min_msrp: float, min_score: int) -> list[str]:
    """Item ids worth re-checking often: community-reported with a retail at or
    above the floor, plus anything already scoring above min_score anywhere."""
    ids = {r["item_id"] for r in rows(
        "SELECT DISTINCT item_id FROM reports WHERE retailer=? AND item_id<>'' AND COALESCE(retail,0)>=?",
        (retailer, min_msrp))}
    for r in rows("SELECT DISTINCT i.item_id FROM reports r JOIN items i ON i.retailer=r.retailer AND "
                  "((r.sku<>'' AND i.sku=r.sku) OR (r.upc<>'' AND i.upc=r.upc)) WHERE r.retailer=? AND COALESCE(r.retail,0)>=?",
                  (retailer, min_msrp)):
        ids.add(r["item_id"])
    # A penny state is the end of the ladder: it does not change by the hour, so
    # penny and likely pairs wait for the 4x-daily verify unless the MSRP floor has them.
    for r in rows("""SELECT DISTINCT item_id FROM predictions WHERE retailer=? AND score>=?
                     AND stage NOT IN ('gone','penny','likely')""",
                  (retailer, min_score)):
        ids.add(r["item_id"])
    if retailer == "dollargeneral":
        # DG lists carry no retail price, so the floor cannot pre-filter. Keep
        # the hourly cost down: UPCs never checked yet (first look), plus items
        # whose last look was a stocked penny or a full price at or above the
        # floor. Everything else waits for the 4x-daily scan.
        for r in rows("""SELECT DISTINCT r.upc FROM reports r WHERE r.retailer='dollargeneral' AND r.upc<>''
                         AND NOT EXISTS (SELECT 1 FROM observations o WHERE o.retailer='dollargeneral'
                                         AND o.item_id=LTRIM(r.upc,'0'))"""):
            ids.add(r["upc"].lstrip("0"))
        for r in rows("""SELECT item_id FROM observations o WHERE retailer='dollargeneral' AND id IN (
                           SELECT MAX(id) FROM observations WHERE retailer='dollargeneral' GROUP BY item_id, store_id)
                         AND ((price<=0.01 AND COALESCE(qty,0)>0) OR COALESCE(original,0)>=?)""", (min_msrp,)):
            ids.add(r["item_id"])
    return sorted(ids)


def reclassify_all() -> dict:
    """Recompute ctype for every item and report (after a rule change)."""
    n = 0
    with tx() as c:
        for r in c.execute("SELECT retailer, item_id, name, category FROM items").fetchall():
            c.execute("UPDATE items SET ctype=? WHERE retailer=? AND item_id=?",
                      (classify(r["name"] or "", r["category"] or "", r["retailer"]), r["retailer"], r["item_id"]))
            n += 1
        m = 0
        for r in c.execute("SELECT id, retailer, name FROM reports").fetchall():
            c.execute("UPDATE reports SET ctype=? WHERE id=?", (classify(r["name"] or "", "", r["retailer"] or ""), r["id"]))
            m += 1
    return {"items": n, "reports": m}


def penny_obs(retailer: str, o: dict) -> bool:
    """True when one observation shows the penny state.

    A lane that reads a store-level price shows $0.01 (Dollar General sets it
    from the catalog's penny flag). Home Depot's API never returns $0.01: on
    533 community-listed items it showed, at every store, status CLEARANCE with
    the clearance price removed and the count at 0 or absent (3,064 store pairs,
    against 150 on items no list had; measured 2026-11-24). That state is the
    penny signature there.
    """
    price = o.get("price")
    if price is not None and price <= 0.01:
        return True
    if retailer == "homedepot":
        return (o.get("store_status") == "CLEARANCE" and o.get("clearance_price") is None
                and not (o.get("qty") or 0) > 0 and o.get("in_stock") != 1)
    return False


def penny_window(retailer, item_id, store_id) -> dict:
    """When this store went to the penny state, as far as our own checks show:
    `since` is the first penny observation of the current penny run, `after`
    the last check before it that was not a penny (None if we never saw one)."""
    obs = rows("""SELECT ts, price, clearance_price, qty, in_stock, store_status FROM observations
                  WHERE retailer=? AND item_id=? AND store_id=? ORDER BY ts DESC""",
               (retailer, item_id, store_id))
    if not obs or not penny_obs(retailer, obs[0]):
        return {"since": None, "after": None}
    since, after = obs[0]["ts"], None
    for o in obs[1:]:
        if penny_obs(retailer, o):
            since = o["ts"]
        else:
            after = o["ts"]
            break
    return {"since": since, "after": after}


# PennyRecon rebuilds its file from PennyCentral's reports (same firstReportedAt to
# the millisecond, sourceCount 1), so the two count as one list.
LIST_FAMILY = {"pennyrecon": "pennycentral"}
_home: dict[str, tuple[float, dict]] = {}


def home_places(retailer: str) -> dict:
    """{state: {city, ...}} of the watched stores, to find list reports from here."""
    hit = _home.get(retailer)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    out: dict[str, set] = {}
    for r in rows("SELECT DISTINCT state, city FROM stores WHERE retailer=? AND watched=1", (retailer,)):
        if r["state"]:
            out.setdefault(r["state"].upper(), set()).add((r["city"] or "").strip().lower())
    _home[retailer] = (time.time(), out)
    return out


def community(retailer, item_id, window_s: float = 45 * 86400) -> dict:
    """What every community list says about one item, merged.

    `lists` counts independent lists (see LIST_FAMILY), `reports` is the largest
    report count any list gives, `states` the number of states with a report,
    `local` the reports from the watched stores' own states, `cities` the
    watched stores' own cities a list names. Empty dict when no list has it.
    """
    it = item(retailer, item_id) or {}
    reps = rows("SELECT * FROM reports WHERE retailer=? AND (item_id=? OR sku=? OR upc=?) AND fetched_at>?",
                (retailer, item_id, it.get("sku") or "-", it.get("upc") or "-", time.time() - window_s))
    if not reps:
        return {}
    states: dict[str, int] = {}
    cities: dict[str, dict] = {}
    n = 0
    for r in reps:
        n = max(n, r.get("n_reports") or 0)
        try:
            pl = json.loads(r.get("places") or "{}")
        except ValueError:
            pl = {}
        for st, c in (pl.get("states") or {}).items():
            states[st] = max(states.get(st, 0), int(c or 0))
        for st, cs in (pl.get("cities") or {}).items():
            if isinstance(cs, dict):
                cities.setdefault(st, {}).update(cs)
    home = home_places(retailer)
    sources = sorted({r["source"] for r in reps})
    return {
        "sources": sources, "lists": len({LIST_FAMILY.get(s, s) for s in sources}),
        "reports": max(n, sum(states.values())), "states": len(states),
        "local": {st: states[st] for st in home if states.get(st)},
        "cities": sorted(c for st, names in home.items() for c in (cities.get(st) or {}) if c.strip().lower() in names),
        "newest": max((r.get("reported_at") or r.get("fetched_at") or 0) for r in reps),
        "listed_confirmed": any((r.get("status") or "").lower() == "confirmed" for r in reps),
        "rows": reps,
    }


def first_reported(retailer, item_id) -> float | None:
    it = item(retailer, item_id) or {}
    row = one("""SELECT MIN(COALESCE(first_reported_at, reported_at)) m FROM reports WHERE retailer=?
                 AND (item_id=? OR (?<>'' AND sku=?) OR (?<>'' AND upc=?))""",
              (retailer, item_id, it.get("sku") or "", it.get("sku") or "", it.get("upc") or "", it.get("upc") or ""))
    return float(row["m"]) if row and row.get("m") else None
