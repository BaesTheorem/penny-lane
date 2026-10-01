"""PennyRecon: aggregates every public Home Depot penny report, rebuilt daily
at 10:00 UTC. `/data/items.json` carries sku, name, gtin/upc, retail, report
counts per state, a decayed likelihood score and a tier. Verified 2026-09-30.
"""

from __future__ import annotations

from .base import Report, Source, norm_sku, norm_upc, parse_ts

DATA = "https://www.pennyrecon.com/data/items.json"


class PennyRecon(Source):
    key = "pennyrecon"
    label = "PennyRecon"
    retailer = "homedepot"
    every_h = 12

    def fetch(self):
        body = self.get(DATA).json()
        items = body.get("items") if isinstance(body, dict) else body
        for it in items or []:
            sku = norm_sku(str(it.get("sku") or it.get("storeSku") or ""))
            item_id = str(it.get("internetNumber") or it.get("omsid") or it.get("itemId") or "")
            if not (sku or item_id):
                continue
            states = it.get("states") or it.get("reportsByState") or {}
            hint = ""
            if isinstance(states, dict):
                top = sorted(states.items(), key=lambda kv: -int(kv[1] or 0))[:12]
                hint = ", ".join(f"{k}:{v}" for k, v in top)
            reports = it.get("reports") or it.get("reportCount") or 0
            yield Report(
                source=self.key, key=f"{self.key}:{sku or item_id}", retailer=self.retailer,
                item_id=item_id, sku=sku,
                upc=norm_upc(str(it.get("gtin12") or it.get("upc") or it.get("gtin") or "")),
                name=(it.get("name") or it.get("title") or "").strip(), price=0.01,
                retail=_num(it.get("retail") or it.get("retailPrice")),
                reported_at=parse_ts(it.get("lastSeen") or it.get("lastReportedAt") or it.get("updated")),
                url=it.get("url") or it.get("homeDepotUrl") or "",
                store_hint=(f"{reports} reports; " + hint) if hint else f"{reports} reports")


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
