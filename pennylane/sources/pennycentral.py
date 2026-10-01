"""PennyCentral: the public mirror of the biggest Home Depot penny group.

`/api/penny-list?page=N&perPage=50` returns JSON with sku, upc,
internetNumber, name, retailPrice, firstReportedAt, lastSeenAt, locations
and a tier. Verified 2026-09-30: plain GET, no auth, Vercel-cached.
"""

from __future__ import annotations

from .base import Report, Source, norm_sku, norm_upc, parse_ts

API = "https://www.pennycentral.com/api/penny-list"


class PennyCentral(Source):
    key = "pennycentral"
    label = "PennyCentral"
    retailer = "homedepot"
    every_h = 6

    def fetch(self):
        page = 1
        while page <= 20:
            r = self.get(API, params={"page": page, "perPage": 50})
            body = r.json()
            items = body.get("items") or body.get("data") or body.get("pennyList") or []
            if isinstance(body, list):
                items = body
            if not items:
                return
            for it in items:
                sku = norm_sku(str(it.get("sku") or ""))
                item_id = str(it.get("internetNumber") or it.get("itemId") or "")
                if not (sku or item_id):
                    continue
                locs = it.get("locations") or it.get("cityLocations") or []
                if isinstance(locs, dict):
                    locs = list(locs.keys())
                yield Report(
                    source=self.key, key=f"{self.key}:{sku or item_id}", retailer=self.retailer,
                    item_id=item_id, sku=sku, upc=norm_upc(str(it.get("upc") or "")),
                    name=(it.get("name") or "").strip(),
                    price=0.01, retail=_num(it.get("retailPrice")), reported_at=parse_ts(it.get("lastSeenAt") or it.get("dateAdded")
                                                     or it.get("firstReportedAt")),
                    url=it.get("homeDepotUrl") or "",
                    store_hint=", ".join(str(x) for x in locs[:12]) if isinstance(locs, list) else "")
            total = body.get("total") if isinstance(body, dict) else None
            if total is not None and page * 50 >= int(total):
                return
            if len(items) < 50:
                return
            page += 1


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
