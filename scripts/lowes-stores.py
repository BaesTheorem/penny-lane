#!/usr/bin/env python3
"""Rebuild pennylane/retailers/lowes_stores.json, the Lowe's store
coordinates the locator measures distance with.

Source: an All the Places run (CC0), spider `lowes_us`. Their recent runs
are often near empty (Lowe's blocks the scraper), so pass a run id that
had the full set; check `stats/_results.json` of a run for the feature count.

    scripts/lowes-stores.py 2026-07-11-13-32-25
"""

import json
import sys
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "pennylane" / "retailers" / "lowes_stores.json"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    url = f"https://alltheplaces-data.openaddresses.io/runs/{sys.argv[1]}/output/lowes_us.geojson"
    with urllib.request.urlopen(url, timeout=60) as r:
        feats = json.load(r)["features"]
    stores = {}
    for f in feats:
        p, g = f["properties"], f.get("geometry") or {}
        if not p.get("ref") or not g.get("coordinates"):
            continue
        lon, lat = g["coordinates"]
        stores[str(p["ref"]).zfill(4)] = {
            "city": p.get("addr:city", ""), "state": p.get("addr:state", ""), "zip": p.get("addr:postcode", ""),
            "address": (p.get("addr:street_address") or "").title(), "lat": round(lat, 5), "lon": round(lon, 5)}
    if len(stores) < 1000:
        print(f"only {len(stores)} stores in run {sys.argv[1]}; pick a run with the full set")
        return 1
    OUT.write_text(json.dumps({"source": url, "stores": dict(sorted(stores.items()))}, separators=(",", ":")) + "\n")
    print(f"{len(stores)} stores -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
