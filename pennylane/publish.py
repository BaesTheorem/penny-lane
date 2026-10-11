"""The public site: a static copy of the web UI on GitHub Pages, fed by one
JSON file this module exports after each scan.

The browser cannot check a shelf itself (Home Depot, Dollar General and
Lowe's all refuse cross-origin requests, probed 2026-10-11), so the Mac
checks a fixed metro store set and publishes the result. Each visitor's
location, stores and alert rules live in their own browser.

Privacy rule: the site must not show which stores Alex watches.
  * The store set centers on publish.center_zip, not on his zip.
  * Home Depot: only items checked the same way at every published store
    (community-reported and the hourly hot set) are exported; sweep-only
    finds, which exist only at his stores, stay home.
  * Lowe's: only the stores already checked (his, inside the metro circle).
  * Dollar General: per-store stock from one locator call per UPC; the
    register price comes from his stores but is published per item, with
    no store named.

Phone alerts: new pennies post to ntfy, one message per topic per run. Shelf
stores have a topic each (`<prefix>-<retailer>-<store>`); Dollar General
pennies are chain-wide, so DG has one (`<prefix>-dollargeneral`). The first
run only records what is already pennied, so subscribers are not flooded.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from . import classify, config, db, detect
from .retailers import registry
from .retailers.base import LaneBlocked, LaneRetry, geocode_zip, miles

log = logging.getLogger("pennylane.publish")
ROOT = config.ROOT
SITE = config.DATA_DIR / "site"
DISCOVER_EVERY_S = 7 * 86400
DG_STOCK_EVERY_S = 6 * 3600
# Topic layout changed 2026-10-11 (per-run digests, one DG topic): seed again silently.
NTFY_SEED = "ntfy_seeded_v2"
RECENT_S = 21 * 86400


def run(cfg: dict) -> dict:
    p = cfg["publish"]
    out: dict = {}
    if time.time() - float(db.kv_get("publish_discovered_at") or 0) > DISCOVER_EVERY_S or not db.published_stores():
        out["stores"] = discover(cfg)
    data = export(cfg)
    out["items"] = len(data["items"])
    out["stores_published"] = len(data["stores"])
    if p.get("ntfy", {}).get("enabled"):
        out["ntfy"] = ntfy(cfg, data)
    out["deploy"] = deploy(cfg, data)
    return out


# ---- the store set -------------------------------------------------------------

def discover(cfg: dict) -> dict:
    """Mark the metro stores published. New rows stay unwatched; an existing
    row keeps its watched flag and its distance from Alex's zip."""
    p = cfg["publish"]
    clat, clon = geocode_zip(p["center_zip"])
    home = _home(cfg)
    out = {}
    for key in ("homedepot", "lowes", "dollargeneral"):
        lane = registry().get(key)
        if not lane:
            continue
        radius = float(p["dg_radius_miles"] if key == "dollargeneral" else p["radius_miles"])
        try:
            if lane.takes_point:
                found = lane.stores_near(p["center_zip"], radius, lat=clat, lon=clon)
            else:
                found = lane.stores_near(p["center_zip"], radius)
        except (LaneBlocked, LaneRetry, NotImplementedError) as e:
            log.warning("%s metro stores: %s", key, e)
            out[key] = f"error: {e}"[:120]
            continue
        found = [s for s in found if s.distance is not None and s.distance <= radius]
        for s in found:
            if s.lat is None and s.zip:
                try:
                    s.lat, s.lon = geocode_zip(s.zip[:5])
                except LaneRetry:
                    pass
            dist = round(miles(home[0], home[1], s.lat, s.lon), 1) if home and s.lat is not None and s.lon is not None else None
            with db.tx() as c:
                c.execute("""INSERT OR IGNORE INTO stores(retailer,store_id,name,address,city,state,zip,lat,lon,distance,watched)
                             VALUES(?,?,?,?,?,?,?,?,?,?,0)""",
                          (s.retailer, s.store_id, s.name, s.address, s.city, s.state, s.zip, s.lat, s.lon, dist))
                c.execute("UPDATE stores SET lat=COALESCE(lat,?), lon=COALESCE(lon,?), address=COALESCE(NULLIF(address,''),?), "
                          "zip=COALESCE(NULLIF(zip,''),?) WHERE retailer=? AND store_id=?",
                          (s.lat, s.lon, s.address, s.zip, s.retailer, s.store_id))
        ids = {s.store_id for s in found}
        if key == "lowes":
            # Lowe's budget allows only the stores already checked.
            ids &= {s["store_id"] for s in db.watched_stores(key)}
        with db.tx() as c:
            c.execute("UPDATE stores SET published=0 WHERE retailer=?", (key,))
            for sid in ids:
                c.execute("UPDATE stores SET published=1 WHERE retailer=? AND store_id=?", (key, sid))
        out[key] = len(ids)
    db.kv_set("publish_discovered_at", time.time())
    return out


def _home(cfg: dict) -> tuple[float, float] | None:
    loc = cfg.get("location") or {}
    if loc.get("lat") is not None:
        return float(loc["lat"]), float(loc["lon"])
    try:
        return geocode_zip(cfg["zip"])
    except LaneRetry:
        return None


# ---- export ----------------------------------------------------------------------

def export(cfg: dict) -> dict:
    p = cfg["publish"]
    stores = db.published_stores()
    by_lane: dict[str, list[dict]] = {}
    for s in stores:
        by_lane.setdefault(s["retailer"], []).append(s)
    items: dict[str, dict] = {}
    for key in ("homedepot", "lowes"):
        _export_shelf_lane(cfg, key, by_lane.get(key) or [], items, int(p.get("history_per_store", 12)))
    _export_dg(by_lane.get("dollargeneral") or [], items)
    # A lane with nothing to show (Lowe's: no community list covers it) stays off
    # the site, so a visitor's store picker does not fill with empty shelves.
    live = {it["retailer"] for it in items.values()}
    stores = [s for s in stores if s["retailer"] in live]
    from .server import _merge_reports, _report_ctype
    reports = []
    for r in _merge_reports(db.report_items(None, since_days=45)):
        reports.append({k: r.get(k) for k in ("retailer", "item_id", "sku", "upc", "name", "retail", "reported_at",
                                                "first_reported_at", "fetched_at", "source", "store_hint", "url")}
                       | {"ctype": _report_ctype(r)})
    counts = {}
    for it in items.values():
        counts[it["ctype"]] = counts.get(it["ctype"], 0) + 1
    sources = {k[len("source_last:"):]: db.kv_get(k) for k in
               (r["k"] for r in db.rows("SELECT k FROM kv WHERE k LIKE 'source_last:%'"))}
    clat, clon = geocode_zip(p["center_zip"])
    return {
        "meta": {"generated_at": time.time(), "center": {"zip": p["center_zip"], "lat": clat, "lon": clon},
                 "radius_miles": p["radius_miles"], "dg_radius_miles": p["dg_radius_miles"],
                 "observations": db.count("SELECT COUNT(*) FROM observations"),
                 "ntfy": p.get("ntfy") or {}},
        "stores": [{"retailer": s["retailer"], "store_id": s["store_id"], "name": s["name"], "address": s["address"],
                    "city": s["city"], "state": s["state"], "lat": s["lat"], "lon": s["lon"]} for s in stores],
        "types": [{"key": k, "label": v, "items": counts.get(k, 0)} for k, v in classify.TYPES],
        "sources": sources,
        "items": items,
        "reports": reports,
    }


def _resolved_reported(key: str) -> list[str]:
    from .scan import _resolve_report_ids
    lane = registry()[key]
    return _resolve_report_ids(lane, db.report_items(key, since_days=45))


def _export_shelf_lane(cfg: dict, key: str, stores: list[dict], items: dict, hist_n: int) -> None:
    """Items checked uniformly at every published store of this lane."""
    pl = cfg.get("pulse") or {}
    ids = set(_resolved_reported(key)) | set(db.hot_items(key, float(pl.get("min_msrp", 100)), int(pl.get("min_score", 50))))
    since = time.time() - RECENT_S
    for iid in sorted(ids):
        it = db.item(key, iid)
        if not it:
            continue
        per: dict[str, dict] = {}
        hist: list[list] = []
        for st in stores:
            o = db.latest(key, iid, st["store_id"])
            if not o or o["ts"] < since:
                continue
            sc = detect.score_item(key, iid, st["store_id"]) or {}
            if sc.get("stage") == "gone" or not sc:
                continue  # not on this shelf: most of the file, none of the use
            pw = db.penny_window(key, iid, st["store_id"])
            per[st["store_id"]] = {"price": o["price"], "original": o["original"], "clearance_price": o["clearance_price"],
                                   "qty": o["qty"], "store_status": o["store_status"], "ts": o["ts"],
                                   "score": sc.get("score"), "stage": sc.get("stage"), "reasons": sc.get("reasons", []),
                                   "penny": pw, "changed_at": _changed_at(key, iid, st["store_id"], o["price"])}
            for h in db.history(key, iid, st["store_id"], limit=hist_n):
                hist.append([st["store_id"], round(h["ts"]), h["price"], h["clearance_price"], h["qty"], h["store_status"]])
        if not per:
            continue
        latest_any = max(per.values(), key=lambda v: v["ts"])
        items[f"{key}/{iid}"] = _item_head(key, iid, it, latest_any) | {"stores": per, "history": hist}


def _export_dg(stores: list[dict], items: dict) -> None:
    """Dollar General: the register price is the chain's (from the stores we
    check, unnamed). Stock per published store comes from the locator, except
    for pennies, where the locator answers a placeholder."""
    if not stores:
        return
    pub = {s["store_id"] for s in stores}
    lane = registry()["dollargeneral"]
    checked = [s["store_id"] for s in db.watched_stores("dollargeneral")]
    since = time.time() - RECENT_S
    center = stores[0]
    for upc_id in _resolved_reported("dollargeneral"):
        it = db.item("dollargeneral", upc_id)
        if not it:
            continue
        lat_obs = [o for o in (db.latest("dollargeneral", upc_id, sid) for sid in checked) if o and o["ts"] >= since]
        if not lat_obs:
            continue
        newest = max(lat_obs, key=lambda o: o["ts"])
        penny = any(o["price"] is not None and o["price"] <= 0.01 for o in lat_obs)
        scores = [sc for sc in (detect.score_item("dollargeneral", upc_id, sid) for sid in checked) if sc]
        best = max(scores, key=lambda sc: sc["score"]) if scores else {}
        since_penny = min((db.penny_window("dollargeneral", upc_id, sid)["since"] or 9e12) for sid in checked)
        penny_since = {"since": since_penny if since_penny < 9e12 else None, "after": None}
        per = {}
        if penny:
            # A DG penny is chain-wide, and the locator's stock for it is a placeholder
            # (5 at every store), so every published store gets the penny, stock unknown.
            for sid in pub:
                per[sid] = {"price": 0.01, "original": newest["original"], "clearance_price": None, "qty": None,
                            "store_status": None, "ts": newest["ts"], "score": 90, "stage": "penny",
                            "reasons": ["register price is $0.01 at the stores we check",
                                        "stock unknown: Dollar General hides counts on penny items"],
                            "penny": penny_since, "changed_at": None, "chain_price": True}
        else:
            for sid, (qty, _status) in _dg_stock(lane, upc_id, center).items():
                if sid not in pub or not (qty or 0) > 0:
                    continue  # not on this shelf
                per[sid] = {"price": newest["price"], "original": newest["original"], "clearance_price": None,
                            "qty": qty, "store_status": None, "ts": newest["ts"], "score": best.get("score") or 0,
                            "stage": best.get("stage"), "reasons": [r for r in best.get("reasons", [])
                                                                     if "register price" not in r] + [f"locator stock {qty}"],
                            "penny": penny_since, "changed_at": None, "chain_price": True}
        if per:
            items[f"dollargeneral/{upc_id}"] = _item_head("dollargeneral", upc_id, it, newest) | {"stores": per, "history": []}


def _dg_stock(lane, upc_id: str, center: dict) -> dict[str, tuple[int | None, int | None]]:
    """{store_id: (qty, inventoryStatus)} near the metro center, cached 6 h per UPC."""
    k = f"dg_stock:{upc_id}"
    cached = db.kv_get(k) or {}
    if cached and time.time() - cached.get("at", 0) < DG_STOCK_EVERY_S:
        return {sid: tuple(v) for sid, v in cached["stores"].items()}
    try:
        rows = lane.stock_near(upc_id, center["lat"], center["lon"])
    except (LaneBlocked, LaneRetry) as e:
        log.warning("dg stock %s: %s", upc_id, e)
        return {sid: tuple(v) for sid, v in (cached.get("stores") or {}).items()}
    out = {str(s["storeNumber"]): [s.get("productQTY"), s.get("inventoryStatus")]
           for s in rows}
    db.kv_set(k, {"at": time.time(), "stores": out})
    time.sleep(lane.gap)
    return {sid: tuple(v) for sid, v in out.items()}


def _item_head(key: str, iid: str, it: dict, latest: dict) -> dict:
    return {"retailer": key, "item_id": iid, "name": it.get("name") or iid, "brand": it.get("brand") or "",
            "upc": it.get("upc") or "", "sku": it.get("sku") or "", "model": it.get("model") or "",
            "url": it.get("url") or "", "ctype": it.get("ctype") or "other",
            "msrp": db.msrp(key, iid, latest), "first_reported": db.first_reported(key, iid)}


def _changed_at(key: str, iid: str, sid: str, price) -> float | None:
    row = db.one("""SELECT MIN(x.ts) m FROM observations x WHERE x.retailer=? AND x.item_id=? AND x.store_id=?
                    AND x.ts > COALESCE((SELECT MAX(y.ts) FROM observations y WHERE y.retailer=? AND y.item_id=?
                    AND y.store_id=? AND y.price IS NOT ?), 0)""", (key, iid, sid, key, iid, sid, price))
    return row["m"] if row else None


# ---- phone alerts ------------------------------------------------------------------

def ntfy(cfg: dict, data: dict) -> dict:
    """One message per topic per run, never one per item. A shelf store has
    its own topic; Dollar General pennies are chain-wide, so DG has one topic.
    A (topic, item) alerts once per penny run: the key holds the run's start,
    so the same penny never repeats and a re-penny after a price rise does."""
    n = cfg["publish"]["ntfy"]
    seeded = bool(db.kv_get(NTFY_SEED))
    names = {(s["retailer"], s["store_id"]): s["name"] for s in data["stores"]}
    fresh: dict[str, dict] = {}  # topic -> {"title": str, "items": [item]}
    seeded_n = 0
    for it in data["items"].values():
        pennied = {sid: st for sid, st in it["stores"].items() if st["stage"] == "penny"}
        if not pennied:
            continue
        if it["retailer"] == "dollargeneral":
            targets = [(f"{n['prefix']}-dollargeneral", "Dollar General", next(iter(pennied.values())))]
        else:
            targets = [(f"{n['prefix']}-{it['retailer']}-{sid}", names.get((it["retailer"], sid), sid), st)
                       for sid, st in pennied.items()]
        for topic, where, st in targets:
            k = f"ntfy:{topic}:{it['item_id']}"
            run_start = str(round((st.get("penny") or {}).get("since") or 0))
            if db.kv_get(k) == run_start:
                continue
            db.kv_set(k, run_start)
            if not seeded:
                seeded_n += 1
                continue
            fresh.setdefault(topic, {"where": where, "items": []})["items"].append(it)
    if not seeded:
        db.kv_set(NTFY_SEED, time.time())
    sent = 0
    for topic, f in fresh.items():
        its = sorted(f["items"], key=lambda x: -(x.get("msrp") or 0))
        lines = [f"{x['name']}" + (f" (MSRP ${x['msrp']:,.2f})" if x.get("msrp") else "")
                 + (f" SKU {x['sku']}" if x.get("sku") else f" UPC {x['upc']}" if x.get("upc") else "") for x in its[:10]]
        if len(its) > 10:
            lines.append(f"and {len(its) - 10} more")
        title = f"{len(its)} new penn{'y' if len(its) == 1 else 'ies'} at {f['where']}"
        if _ntfy_post(n["server"], topic, title, "\n".join(lines), _site_url(cfg, its[0] if len(its) == 1 else None)):
            sent += 1
    return {"messages": sent, "items": sum(len(f["items"]) for f in fresh.values()), "seeded": seeded_n}


def _ntfy_post(server: str, topic: str, title: str, msg: str, click: str) -> bool:
    req = urllib.request.Request(f"{server.rstrip('/')}/{topic}", data=msg.encode(), method="POST",
                                 headers={"Title": title, "Tags": "moneybag", "Click": click})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except OSError as e:
        log.warning("ntfy %s: %s", topic, e)
        return False


def _site_url(cfg: dict, it: dict | None = None) -> str:
    base = _pages_base()
    return f"{base}#item/{it['retailer']}/{it['item_id']}" if it else base


def _pages_base() -> str:
    url = _git("remote", "get-url", "origin").strip()
    owner_repo = url.split("github.com")[-1].lstrip(":/").removesuffix(".git")
    owner, repo = owner_repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{repo}/"


# ---- deploy ------------------------------------------------------------------------

def build(data: dict) -> Path:
    """The web UI with root-relative paths made relative, the static data
    shim, and the data file."""
    web = ROOT / "web"
    SITE.mkdir(parents=True, exist_ok=True)
    for child in SITE.iterdir():
        if child.name != ".git":
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    shutil.copytree(web / "static", SITE / "static")
    html = (web / "index.html").read_text()
    html = html.replace('"/static/', '"static/').replace(
        '<script type="module" src="static/app.js"></script>',
        '<script>window.PENNY_STATIC = true;</script>\n<script src="static/static-api.js"></script>\n'
        '<script type="module" src="static/app.js"></script>')
    (SITE / "index.html").write_text(html)
    css = SITE / "static" / "app.css"
    css.write_text(css.read_text().replace('url("/static/vendor/', 'url("vendor/'))
    (SITE / "data").mkdir(exist_ok=True)
    (SITE / "data" / "site.json").write_text(json.dumps(data, separators=(",", ":")))
    (SITE / ".nojekyll").write_text("")
    return SITE


def deploy(cfg: dict, data: dict) -> str:
    """One amended commit on the Pages branch, force-pushed. Amending keeps
    the old blobs in the local object store, so a push sends only what changed
    (the data file), never the fonts again."""
    site = build(data)
    branch = cfg["publish"]["branch"]
    remote = _git("remote", "get-url", "origin").strip()
    g = ["git", "-C", str(site)]
    if not (site / ".git").exists():
        subprocess.run([*g, "init", "-q", "-b", branch], check=True)
    subprocess.run([*g, "add", "-A"], check=True)
    has_head = subprocess.run([*g, "rev-parse", "-q", "--verify", "HEAD"], capture_output=True).returncode == 0
    msg = f"Publish penny data {time.strftime('%Y-%m-%d %H:%M')}"
    subprocess.run([*g, "commit", "-q", "--allow-empty", *(["--amend"] if has_head else []), "-m", msg], check=True)
    r = subprocess.run([*g, "push", "-q", "-f", remote, f"HEAD:{branch}"], capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise RuntimeError(f"push failed: {r.stderr.strip()[:200]}")
    return "pushed"


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout
