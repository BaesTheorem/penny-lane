"""Penny Lane HTTP server: JSON API + the web UI (also what the Mac and
iPhone shells render). Binds 127.0.0.1 by default; the iPhone reaches it
through `remote.py` (token-guarded tunnel origin), never by opening this
port to the LAN unguarded.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

from . import config, db, detect, scan
from .retailers import registry
from .retailers.base import LaneBlocked, LaneRetry

WEB = Path(__file__).resolve().parent.parent / "web"
app = Flask(__name__, static_folder=str(WEB / "static"), static_url_path="/static")
log = logging.getLogger("pennylane.server")
_jobs: dict[str, dict] = {}


@app.get("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.get("/health")
def health():
    return jsonify(ok=True, t=time.time())


# ---- read ----------------------------------------------------------------

@app.get("/api/status")
def status():
    cfg = config.load()
    stores = db.watched_stores()
    counts = {
        "items": db.count("SELECT COUNT(*) FROM items"),
        "observations": db.count("SELECT COUNT(*) FROM observations"),
        "reports": db.count("SELECT COUNT(*) FROM reports"),
        "alerts_unseen": db.count("SELECT COUNT(*) FROM alerts WHERE seen=0"),
    }
    runs = db.rows("SELECT * FROM runs ORDER BY id DESC LIMIT 12")
    sources = {k[len("source_last:"):]: v for k, v in
               ((r["k"], db.kv_get(r["k"])) for r in db.rows("SELECT k FROM kv WHERE k LIKE 'source_last:%'"))}
    sweeps = {k[len("sweep_last:"):]: v for k, v in
              ((r["k"], db.kv_get(r["k"])) for r in db.rows("SELECT k FROM kv WHERE k LIKE 'sweep_last:%'"))}
    lanes = {k: {"label": v.label, "can_sweep": v.can_sweep} for k, v in registry().items()}
    return jsonify(zip=cfg["zip"], stores=stores, counts=counts, runs=runs, sources=sources, sweeps=sweeps,
                   jobs=_jobs, lanes=lanes, min_score=cfg["notify"]["min_score"])


@app.get("/api/predictions")
def predictions():
    retailer = request.args.get("retailer")
    store = request.args.get("store")
    min_score = int(request.args.get("min_score") or 0)
    stage = request.args.get("stage")
    limit = int(request.args.get("limit") or 200)
    sort = request.args.get("sort") or "score"
    order = {"score": "p.score DESC, msrp DESC", "msrp": "msrp DESC, p.score DESC",
             "savings": "(COALESCE(msrp,0) - COALESCE(o.clearance_price, o.price, 0)) DESC, p.score DESC",
             "recent": "o.ts DESC"}.get(sort, "p.score DESC")
    sql = """SELECT p.*, i.name, i.brand, i.upc, i.sku, i.url, s.name AS store_name,
                    o.price, o.original, o.clearance_price, o.qty, o.promo, o.store_status, o.ts AS observed_at,
                    COALESCE(o.original,
                             (SELECT MAX(r.retail) FROM reports r WHERE r.retailer=p.retailer
                                AND (r.item_id=p.item_id OR (i.sku<>'' AND r.sku=i.sku) OR (i.upc<>'' AND r.upc=i.upc))),
                             CASE WHEN o.price > 0.01 THEN o.price END) AS msrp
             FROM predictions p
             JOIN items i ON i.retailer=p.retailer AND i.item_id=p.item_id
             LEFT JOIN stores s ON s.retailer=p.retailer AND s.store_id=p.store_id
             LEFT JOIN observations o ON o.id = (SELECT id FROM observations
                   WHERE retailer=p.retailer AND item_id=p.item_id AND store_id=p.store_id ORDER BY ts DESC LIMIT 1)
             WHERE p.score>=?"""
    params: list = [min_score]
    if retailer:
        sql += " AND p.retailer=?"
        params.append(retailer)
    if store:
        sql += " AND p.store_id=?"
        params.append(store)
    if stage:
        sql += " AND p.stage=?"
        params.append(stage)
    sql += f" ORDER BY {order} LIMIT ?"
    params.append(limit)
    return jsonify(db.rows(sql, params))


@app.get("/api/alerts")
def alerts():
    limit = int(request.args.get("limit") or 100)
    rows = db.rows("""SELECT a.*, i.name, i.url, s.name AS store_name FROM alerts a
                      LEFT JOIN items i ON i.retailer=a.retailer AND i.item_id=a.item_id
                      LEFT JOIN stores s ON s.retailer=a.retailer AND s.store_id=a.store_id
                      ORDER BY a.id DESC LIMIT ?""", (limit,))
    return jsonify(rows)


@app.post("/api/alerts/seen")
def alerts_seen():
    with db.tx() as c:
        c.execute("UPDATE alerts SET seen=1 WHERE seen=0")
    return jsonify(ok=True)


@app.get("/api/reports")
def reports():
    retailer = request.args.get("retailer")
    days = int(request.args.get("days") or 45)
    rows = _merge_reports(db.report_items(retailer, since_days=days))
    # Attach what we know locally: any observation at a watched store.
    for r in rows:
        r["local"] = _local_state(r)
        if r.get("retail") is None:
            r["retail"] = next((o.get("original") for o in r["local"] if o.get("original")), None)
    sort = request.args.get("sort") or "recent"
    if sort == "retail":
        rows.sort(key=lambda r: -(r.get("retail") or 0))
    elif sort == "stocked":
        rows.sort(key=lambda r: -sum((o.get("qty") or 0) for o in r["local"]))
    return jsonify(rows)


def _merge_reports(rows: list[dict]) -> list[dict]:
    """One row per item: the same SKU/UPC arrives from several lists."""
    merged: dict[str, dict] = {}
    for r in rows:
        key = f"{r.get('retailer')}:{r.get('item_id') or ''}:{r.get('sku') or ''}:{r.get('upc') or ''}"
        for alt in (r.get("item_id"), r.get("sku"), r.get("upc")):
            if alt and f"{r.get('retailer')}:{alt}" in merged:
                key = f"{r.get('retailer')}:{alt}"
                break
        m = merged.get(key)
        if not m:
            m = dict(r)
            m["sources"] = [r["source"]]
            merged[key] = m
            for alt in (r.get("item_id"), r.get("sku"), r.get("upc")):
                if alt:
                    merged.setdefault(f"{r.get('retailer')}:{alt}", m)
            continue
        if r["source"] not in m["sources"]:
            m["sources"].append(r["source"])
        for f in ("item_id", "sku", "upc", "name", "url", "retail"):
            if not m.get(f) and r.get(f):
                m[f] = r[f]
        if (r.get("reported_at") or 0) > (m.get("reported_at") or 0):
            m["reported_at"] = r["reported_at"]
        if r.get("store_hint") and r["store_hint"] not in (m.get("store_hint") or ""):
            m["store_hint"] = ((m.get("store_hint") or "") + " · " + r["store_hint"]).strip(" ·")
    seen, out = set(), []
    for m in merged.values():
        if id(m) in seen:
            continue
        seen.add(id(m))
        m["source"] = ", ".join(m["sources"])
        out.append(m)
    out.sort(key=lambda r: -(r.get("reported_at") or r.get("fetched_at") or 0))
    return out


def _local_state(rep: dict) -> list[dict]:
    retailer = rep.get("retailer")
    ids = []
    if rep.get("item_id"):
        ids.append(rep["item_id"])
    if rep.get("sku"):
        hit = db.one("SELECT item_id FROM items WHERE retailer=? AND sku=?", (retailer, rep["sku"]))
        if hit:
            ids.append(hit["item_id"])
    if rep.get("upc"):
        ids += [i["item_id"] for i in db.items_by_upc(rep["upc"]) if i["retailer"] == retailer]
    out = []
    for iid in sorted(set(ids)):
        for st in db.watched_stores(retailer):
            o = db.latest(retailer, iid, st["store_id"])
            if o:
                o["store_name"] = st["name"]
                out.append(o)
    return out


@app.get("/api/item/<retailer>/<item_id>")
def item(retailer, item_id):
    it = db.item(retailer, item_id) or abort(404)
    stores = db.watched_stores(retailer)
    latest = []
    for st in stores:
        o = db.latest(retailer, item_id, st["store_id"])
        if o:
            o["store_name"] = st["name"]
            sc = detect.score_item(retailer, item_id, st["store_id"])
            o["score"] = sc["score"] if sc else None
            o["stage"] = sc["stage"] if sc else None
            o["reasons"] = sc["reasons"] if sc else []
            latest.append(o)
    reports = db.rows("SELECT * FROM reports WHERE retailer=? AND (item_id=? OR sku=? OR upc=?) ORDER BY reported_at DESC",
                      (retailer, item_id, it.get("sku") or "-", it.get("upc") or "-"))
    watched = db.one("SELECT 1 FROM watches WHERE retailer=? AND item_id=?", (retailer, item_id)) is not None
    return jsonify(item=it, latest=latest, history=db.history(retailer, item_id), reports=reports, watched=watched)


@app.get("/api/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    like = f"%{q}%"
    rows = db.rows("""SELECT i.*, (SELECT MAX(score) FROM predictions p WHERE p.retailer=i.retailer AND p.item_id=i.item_id) AS score
                      FROM items i WHERE name LIKE ? OR upc LIKE ? OR sku LIKE ? OR item_id LIKE ? OR model LIKE ?
                      ORDER BY score DESC NULLS LAST, last_seen DESC LIMIT 60""", (like, like, like, like, like))
    return jsonify(rows)


# ---- live lookups (the scan flow) --------------------------------------------

@app.post("/api/lookup")
def lookup():
    """Body: {retailer?, item_id? | upc? | sku?, store_id?}. Resolves the code,
    hits the live lane at the chosen or every watched store, records and
    scores. This is what the phone calls after a barcode scan."""
    body = request.get_json(force=True) or {}
    upc = (body.get("upc") or "").strip()
    sku = (body.get("sku") or "").strip()
    item_id = (body.get("item_id") or "").strip()
    retailer = body.get("retailer")
    store_id = body.get("store_id")
    targets = _resolve(retailer, item_id, upc, sku)
    if not targets:
        return jsonify(ok=False, why="not in the local index; enter the retailer item number", candidates=[])
    results = []
    for r_key, iid in targets:
        lane = registry().get(r_key)
        if not lane:
            continue
        stores = [s for s in db.watched_stores(r_key) if not store_id or s["store_id"] == str(store_id)]
        if store_id and not stores:
            stores = [{"store_id": str(store_id), "name": str(store_id)}]
        for st in stores:
            try:
                o = lane.by_upc(upc, st["store_id"]) if (upc and r_key == "dollargeneral") else lane.lookup(iid, st["store_id"])
            except LaneBlocked as e:
                results.append({"retailer": r_key, "store_id": st["store_id"], "error": f"blocked: {e}"})
                break
            except LaneRetry as e:
                results.append({"retailer": r_key, "store_id": st["store_id"], "error": f"retry: {e}"})
                continue
            if not o:
                results.append({"retailer": r_key, "store_id": st["store_id"], "error": "no such item"})
                continue
            db.record(o)
            sc = detect.score_item(r_key, o.item_id, st["store_id"]) or {}
            if sc:
                db.put_prediction(r_key, o.item_id, st["store_id"], sc["score"], sc["stage"], sc["next_step_at"], sc["reasons"])
            d = o.as_dict()
            d.update(store_name=st.get("name"), score=sc.get("score"), stage=sc.get("stage"), reasons=sc.get("reasons", []))
            results.append(d)
            time.sleep(min(lane.gap, 1.0))
    return jsonify(ok=True, results=results)


def _resolve(retailer, item_id, upc, sku) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if item_id and retailer:
        return [(retailer, item_id)]
    if sku:
        for row in db.rows("SELECT retailer, item_id FROM items WHERE sku=?" + (" AND retailer=?" if retailer else ""),
                           (sku, retailer) if retailer else (sku,)):
            out.append((row["retailer"], row["item_id"]))
        for row in db.rows("SELECT retailer, item_id FROM reports WHERE sku=? AND item_id<>''", (sku,)):
            out.append((row["retailer"], row["item_id"]))
    if upc:
        for row in db.items_by_upc(upc):
            if not retailer or row["retailer"] == retailer:
                out.append((row["retailer"], row["item_id"]))
        for row in db.rows("SELECT retailer, item_id FROM reports WHERE upc=? AND item_id<>''", (upc,)):
            if not retailer or row["retailer"] == retailer:
                out.append((row["retailer"], row["item_id"]))
        # Dollar General item ids are UPCs: always a live candidate.
        if not retailer or retailer == "dollargeneral":
            out.append(("dollargeneral", upc.lstrip("0")))
    return sorted(set(out))


# ---- writes ------------------------------------------------------------------

@app.post("/api/watch")
def watch():
    b = request.get_json(force=True) or {}
    if b.get("remove"):
        db.remove_watch(b["retailer"], b["item_id"])
    else:
        db.add_watch(b["retailer"], b["item_id"], b.get("note", ""))
    return jsonify(ok=True)


@app.get("/api/stores")
def stores():
    return jsonify(db.rows("SELECT * FROM stores ORDER BY retailer, distance IS NULL, distance, name"))


@app.post("/api/stores/watch")
def stores_watch():
    b = request.get_json(force=True) or {}
    with db.tx() as c:
        c.execute("UPDATE stores SET watched=? WHERE retailer=? AND store_id=?",
                  (1 if b.get("watched") else 0, b["retailer"], str(b["store_id"])))
    cfg = config.load()
    cfg["retailers"].setdefault(b["retailer"], {"enabled": True, "stores": []})
    cfg["retailers"][b["retailer"]]["stores"] = [s["store_id"] for s in db.watched_stores(b["retailer"])]
    config.save(cfg)
    return jsonify(ok=True)


@app.post("/api/stores/add")
def stores_add():
    """Hand-added store (Walmart has no locator lane)."""
    b = request.get_json(force=True) or {}
    from .retailers.base import Store
    db.upsert_store(Store(b["retailer"], str(b["store_id"]), b.get("name") or f"{b['retailer']} {b['store_id']}"),
                    watched=True)
    return stores_watch()


@app.get("/api/qr")
def qr():
    """PNG QR of the pairing payload (local only: it carries the token)."""
    import io

    import segno
    from .remote import is_local
    if not is_local(request):
        abort(403)
    buf = io.BytesIO()
    segno.make(request.args.get("d") or "", error="m").save(buf, kind="png", scale=6, dark="#e3e2e6", light="#121316")
    from flask import Response
    return Response(buf.getvalue(), mimetype="image/png")


@app.get("/api/config")
def get_config():
    return jsonify(config.load())


@app.post("/api/config")
def set_config():
    cfg = config.load()
    config.merge(cfg, request.get_json(force=True) or {})
    config.save(cfg)
    return jsonify(cfg)


@app.post("/api/run/<job>")
def run_job(job):
    if job not in list(scan.JOBS) + ["all"]:
        abort(404)
    if _jobs.get(job, {}).get("running"):
        return jsonify(ok=False, why="already running")
    kw = {}
    if job == "all" and request.args.get("sweep"):
        kw["sweep"] = True
    if job == "sweep" and request.args.get("pages"):
        kw["max_pages"] = int(request.args["pages"])

    def _go():
        _jobs[job] = {"running": True, "started": time.time()}
        try:
            res = scan.run(job, **kw)
            _jobs[job] = {"running": False, "finished": time.time(), "result": res}
        except Exception as e:  # noqa: BLE001
            _jobs[job] = {"running": False, "finished": time.time(), "error": str(e)}

    threading.Thread(target=_go, daemon=True, name=f"job-{job}").start()
    return jsonify(ok=True)


def main():
    import os
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    db.connect()
    host = os.environ.get("PENNYLANE_HOST", "127.0.0.1")
    try:
        from . import remote
        remote.init(app)
    except Exception as e:  # noqa: BLE001
        log.warning("remote access disabled: %s", e)
    app.run(host=host, port=config.PORT, threaded=True)


if __name__ == "__main__":
    main()
