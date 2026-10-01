"""Small CLI verbs that are not scheduled jobs (see bin/penny)."""

from __future__ import annotations

import json
import sys
import time

from . import db, detect
from .retailers import registry
from .retailers.base import LaneBlocked, LaneRetry


def cmd_lookup(retailer: str, item_id: str, store_id: str | None = None) -> int:
    lane = registry()[retailer]
    stores = [store_id] if store_id else [s["store_id"] for s in db.watched_stores(retailer)]
    for sid in stores:
        try:
            o = lane.lookup(item_id, sid)
        except (LaneBlocked, LaneRetry) as e:
            print(f"{sid}: {e}")
            continue
        if not o:
            print(f"{sid}: no such item")
            continue
        db.record(o)
        sc = detect.score_item(retailer, o.item_id, sid) or {}
        print(f"{sid} {o.name[:50]!r} price={o.price} orig={o.original} clr={o.clearance_price} "
              f"qty={o.qty} status={o.store_status} score={sc.get('score')} {sc.get('stage')}")
        for r in sc.get("reasons", []):
            print(f"    - {r}")
        time.sleep(lane.gap)
    return 0


def cmd_upc(upc: str, store_id: str | None = None) -> int:
    hits = db.items_by_upc(upc)
    for it in hits:
        print(f"index: {it['retailer']} {it['item_id']} {it['name'][:60]!r}")
        cmd_lookup(it["retailer"], it["item_id"], store_id)
    if not any(h["retailer"] == "dollargeneral" for h in hits):
        lane = registry()["dollargeneral"]
        for sid in [store_id] if store_id else [s["store_id"] for s in db.watched_stores("dollargeneral")]:
            try:
                o = lane.by_upc(upc, sid)
            except (LaneBlocked, LaneRetry) as e:
                print(f"DG {sid}: {e}")
                continue
            if o:
                db.record(o)
                print(f"DG {sid} {o.name[:50]!r} price={o.price} qty={o.qty} {o.store_status or ''}")
            time.sleep(lane.gap)
    return 0


def cmd_top(store_id: str | None = None) -> int:
    for st in db.watched_stores():
        if store_id and st["store_id"] != store_id:
            continue
        ranked = detect.score_store(st["retailer"], st["store_id"])[:15]
        if not ranked:
            continue
        print(f"== {st['retailer']} {st['store_id']} {st['name']}")
        for r in ranked:
            it = db.item(st["retailer"], r["item_id"]) or {}
            lt = r["latest"]
            print(f"  {r['score']:3d} {r['stage']:8s} {it.get('name', '')[:48]!r} ${lt.get('price')} qty={lt.get('qty')}")
    return 0


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    verb, args = argv[0], argv[1:]
    if verb == "lookup":
        return cmd_lookup(*args)
    if verb == "upc":
        return cmd_upc(*args)
    if verb == "top":
        return cmd_top(*args)
    if verb == "json":
        print(json.dumps(db.rows("SELECT * FROM predictions ORDER BY score DESC LIMIT 50"), indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
