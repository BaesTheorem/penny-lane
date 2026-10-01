"""Scheduled jobs. `python -m pennylane.scan <job>` or via bin/penny.

  stores   refresh store lists around the configured zip; mark watched
  sources  pull every community list into `reports`
  verify   look up every recent community-reported item at each watched
           store (the edge: pennied elsewhere AND on this shelf)
  sweep    walk the retailer's in-store clearance listing per watched store
  watch    refresh the user's watchlist at every watched store
  predict  score everything observed recently; raise alerts
  pulse    hourly: re-check the hot set (reported, MSRP >= floor, or scoring
           high) at every watched store, then predict; Discord DM on a penny
  all      sources, verify, watch, predict (sweep runs on --sweep)
"""

from __future__ import annotations

import logging
import sys
import time

from . import config, db, detect
from . import notify as notify_mod
from .retailers import registry
from .retailers.base import LaneBlocked, LaneRetry
from .sources import all_sources

log = logging.getLogger("pennylane.scan")
HARNESS = "/Users/alexhedtke/Documents/Exobrain harness"


def job_stores(cfg: dict) -> dict:
    out = {}
    for key, lane in registry().items():
        rconf = cfg["retailers"].get(key) or {}
        if not rconf.get("enabled", True):
            continue
        try:
            stores = lane.stores_near(cfg["zip"], cfg["radius_miles"])
        except (LaneBlocked, LaneRetry, NotImplementedError) as e:
            log.warning("%s stores: %s", key, e)
            continue
        wanted = set(str(s) for s in rconf.get("stores") or [])
        if not wanted:
            wanted = {s.store_id for s in sorted(stores, key=lambda s: s.distance or 1e9)[:3]}
        for s in stores:
            db.upsert_store(s, watched=s.store_id in wanted)
        out[key] = sorted(wanted)
    return out


def job_sources(cfg: dict) -> dict:
    counts = {}
    for src in all_sources():
        n = new = 0
        try:
            for rep in src.fetch():
                n += 1
                if db.add_report(rep.source, rep.key, **{k: v for k, v in rep.as_dict().items()
                                                         if k not in ("source", "key")}):
                    new += 1
        except Exception as e:  # noqa: BLE001 - one bad site must not stop the rest
            log.warning("source %s failed: %s", src.key, e)
            counts[src.key] = f"error: {e}"[:120]
            continue
        counts[src.key] = f"{n} seen, {new} new"
        db.kv_set(f"source_last:{src.key}", {"at": time.time(), "seen": n, "new": new})
    return counts


def _resolve_report_ids(lane, reports: list[dict]) -> list[str]:
    """Community rows carry SKU/UPC/internet number; the lane wants item ids."""
    ids = []
    for r in reports:
        if r.get("item_id"):
            ids.append(str(r["item_id"]))
            continue
        if r.get("sku"):
            hit = db.one("SELECT item_id FROM items WHERE retailer=? AND sku=?", (lane.key, r["sku"]))
            if hit:
                ids.append(hit["item_id"])
                continue
        if r.get("upc"):
            if lane.key == "dollargeneral":
                # DG item ids are UPCs: every listed UPC is directly checkable.
                ids.append(r["upc"].lstrip("0"))
                continue
            for it in db.items_by_upc(r["upc"]):
                if it["retailer"] == lane.key:
                    ids.append(it["item_id"])
                    break
    return sorted(set(ids))


def job_verify(cfg: dict, days: int = 45) -> dict:
    out = {}
    for key, lane in registry().items():
        stores = db.watched_stores(key)
        if not stores:
            continue
        reports = db.report_items(key, since_days=days)
        ids = _resolve_report_ids(lane, reports)
        if not ids:
            out[key] = "no resolvable reports"
            continue
        seen = 0
        for st in stores:
            try:
                for obs in lane.lookup_many(ids, st["store_id"]):
                    db.record(obs)
                    seen += 1
            except LaneBlocked as e:
                log.warning("%s blocked at %s: %s", key, st["store_id"], e)
                break
            except LaneRetry as e:
                log.warning("%s retry later at %s: %s", key, st["store_id"], e)
            time.sleep(lane.gap)
        out[key] = f"{len(ids)} reported items x {len(stores)} stores, {seen} observations"
    return out


def job_sweep(cfg: dict, max_pages: int | None = None) -> dict:
    out = {}
    for key, lane in registry().items():
        if not lane.can_sweep:
            continue
        pages = max_pages or cfg["sweep"].get(f"{key}_pages", 400)
        for st in db.watched_stores(key):
            n = 0
            try:
                for obs in lane.sweep(st["store_id"], max_pages=pages):
                    db.record(obs)
                    n += 1
            except LaneBlocked as e:
                log.warning("%s sweep blocked at %s after %d: %s", key, st["store_id"], n, e)
            except LaneRetry as e:
                log.warning("%s sweep retry at %s after %d: %s", key, st["store_id"], n, e)
            out[f"{key}:{st['store_id']}"] = n
            db.kv_set(f"sweep_last:{key}:{st['store_id']}", {"at": time.time(), "n": n})
    return out


def job_watch(cfg: dict) -> dict:
    out = {}
    by_lane: dict[str, list[str]] = {}
    for w in db.watched_items():
        by_lane.setdefault(w["retailer"], []).append(w["item_id"])
    for key, ids in by_lane.items():
        lane = registry().get(key)
        if not lane:
            continue
        n = 0
        for st in db.watched_stores(key):
            try:
                for obs in lane.lookup_many(ids, st["store_id"]):
                    db.record(obs)
                    n += 1
            except (LaneBlocked, LaneRetry) as e:
                log.warning("%s watch at %s: %s", key, st["store_id"], e)
        out[key] = n
    return out


def job_predict(cfg: dict, notify: bool = True) -> dict:
    out = {}
    min_score = int(cfg["notify"].get("min_score", 70))
    for st in db.watched_stores():
        ranked = detect.score_store(st["retailer"], st["store_id"])
        out[f"{st['retailer']}:{st['store_id']}"] = len(ranked)
        for r in ranked:
            kind = None
            if r["stage"] == "penny":
                kind = "penny_on_shelf"
            elif r["score"] >= min_score:
                kind = "imminent"
            if not kind or db.recent_alert_exists(kind, st["retailer"], r["item_id"], st["store_id"]):
                continue
            it = db.item(st["retailer"], r["item_id"]) or {}
            name = it.get("name") or r["item_id"]
            full = db.msrp(st["retailer"], r["item_id"], r["latest"])
            msg = (f"{name} at {st['name']} ({st['retailer']}): score {r['score']}, {r['stage']}"
                   + (f", MSRP ${full:,.2f}" if full else "") + ". " + "; ".join(r["reasons"][:4]))
            db.add_alert(kind, st["retailer"], r["item_id"], st["store_id"], msg, r["score"])
            if notify and cfg["notify"].get("enabled", True):
                notify_mod.banner("Penny on the shelf" if kind == "penny_on_shelf" else "Penny Lane: imminent",
                                  msg, it.get("url") or "", "reasons: " + " | ".join(r["reasons"]))
                floor = float((cfg["notify"].get("discord") or {}).get("min_msrp", 100))
                if kind == "penny_on_shelf" and full is not None and full >= floor:
                    lt = r["latest"]
                    notify_mod.discord(
                        f"**Penny on the shelf**: {name}\n{st['name']} ({_label(st['retailer'])}), "
                        f"MSRP ${full:,.2f}, register ${lt.get('price', 0):.2f}, qty {lt.get('qty', '?')}"
                        + (f"\nSKU {it['sku']}" if it.get("sku") else "") + (f" · UPC {it['upc']}" if it.get("upc") else "")
                        + (f"\n{it['url']}" if it.get("url") else ""), cfg)
    return out


def _label(key: str) -> str:
    lane = registry().get(key)
    return lane.label if lane else key


def job_pulse(cfg: dict) -> dict:
    """Hourly: re-check the hot set at every watched store, then score and
    alert. Lowe's and Walmart stay out (their request budgets cannot take it)."""
    out = {}
    p = cfg.get("pulse") or {}
    for key in p.get("lanes") or ["homedepot", "dollargeneral"]:
        lane = registry().get(key)
        stores = db.watched_stores(key)
        if not lane or not stores:
            continue
        ids = db.hot_items(key, float(p.get("min_msrp", 100)), int(p.get("min_score", 50)))
        n = 0
        for st in stores:
            try:
                for obs in lane.lookup_many(ids, st["store_id"]):
                    db.record(obs)
                    n += 1
            except LaneBlocked as e:
                log.warning("%s pulse blocked at %s: %s", key, st["store_id"], e)
                break
            except LaneRetry as e:
                log.warning("%s pulse retry at %s: %s", key, st["store_id"], e)
            time.sleep(lane.gap)
        out[key] = f"{len(ids)} hot items x {len(stores)} stores, {n} observations"
    out["predict"] = job_predict(cfg)
    return out


JOBS = {"stores": job_stores, "sources": job_sources, "verify": job_verify, "sweep": job_sweep,
        "watch": job_watch, "predict": job_predict, "pulse": job_pulse}


def run(job: str, **kw) -> dict:
    cfg = config.load()
    run_id = db.start_run(job)
    try:
        if job == "all":
            res = {"sources": job_sources(cfg), "verify": job_verify(cfg), "watch": job_watch(cfg)}
            if kw.get("sweep"):
                res["sweep"] = job_sweep(cfg)
            res["predict"] = job_predict(cfg)
        else:
            res = JOBS[job](cfg, **kw)
        db.finish_run(run_id, True, str(res)[:2000])
        return res
    except Exception as e:
        db.finish_run(run_id, False, str(e)[:2000])
        raise


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    argv = argv or sys.argv[1:]
    if not argv or argv[0] not in list(JOBS) + ["all"]:
        print(__doc__)
        return 2
    kw = {}
    if "--sweep" in argv:
        kw["sweep"] = True
    if "--no-notify" in argv:
        kw["notify"] = False
    if "--pages" in argv:
        kw["max_pages"] = int(argv[argv.index("--pages") + 1])
    res = run(argv[0], **{k: v for k, v in kw.items() if _accepts(argv[0], k)})
    for k, v in res.items():
        print(f"{k}: {v}")
    return 0


def _accepts(job, key):
    return {"all": {"sweep"}, "predict": {"notify"}, "sweep": {"max_pages"}}.get(job, set()) >= {key}


if __name__ == "__main__":
    sys.exit(main())
