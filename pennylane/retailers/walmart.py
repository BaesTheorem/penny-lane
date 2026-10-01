"""Walmart lane: the product page's `__NEXT_DATA__`.

Probed 2026-09-30. A product page parses fully (price, wasPrice,
`priceDisplayCodes.clearance/rollback/reducedPrice`, upc, availability,
per-store PICKUP fulfillment), but PerimeterX put this IP behind a
"Robot or human?" wall after about 25 requests in two minutes, `/store/`
is walled on the first hit, and the store a page renders for comes from a
server-set `locGuestData` cookie that we cannot mint. So: no store
switching, no search, no sweep. This lane reads the price for whatever
store the session is anchored to, slowly, and is best used from the phone
in the store (its own IP, its own cookie).
"""

from __future__ import annotations

import json
import re

from curl_cffi import requests

from .base import LaneBlocked, LaneRetry, Observation, Retailer, Store

NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
H = {"Accept-Language": "en-US,en;q=0.9"}


class Walmart(Retailer):
    key = "walmart"
    label = "Walmart"
    gap = 8.0
    can_sweep = False

    def __init__(self):
        self.s = requests.Session(impersonate="chrome")

    def stores_near(self, zip_code: str, radius: float = 25) -> list[Store]:
        raise NotImplementedError("walmart store finder is bot-walled; add store ids by hand in Settings")

    def lookup(self, item_id: str, store_id: str) -> Observation | None:
        r = self.s.get(f"https://www.walmart.com/ip/{item_id}", headers=H, timeout=25, allow_redirects=True)
        if "Robot or human" in r.text or "/blocked?" in r.url:
            raise LaneBlocked("walmart PX wall")
        if r.status_code != 200:
            raise LaneRetry(f"walmart {r.status_code}")
        m = NEXT.search(r.text)
        if not m:
            raise LaneRetry("walmart no __NEXT_DATA__")
        try:
            p = json.loads(m.group(1))["props"]["pageProps"]["initialData"]["data"]["product"]
        except (KeyError, ValueError, TypeError):
            return None
        return self._obs(p, store_id)

    def item_url(self, item_id: str) -> str:
        return f"https://www.walmart.com/ip/{item_id}"

    def _obs(self, p: dict, store_id: str) -> Observation:
        pi = p.get("priceInfo") or {}
        cur = (pi.get("currentPrice") or {}).get("price")
        was = (pi.get("wasPrice") or {}).get("price") if isinstance(pi.get("wasPrice"), dict) else pi.get("wasPrice")
        codes = pi.get("priceDisplayCodes") or {}
        flags = [k for k in ("clearance", "rollback", "reducedPrice") if codes.get(k)]
        pickup = next((o for o in p.get("fulfillmentOptions") or [] if o.get("type") == "PICKUP"), {}) or {}
        loc = p.get("location") or {}
        rendered_store = (loc.get("pickupLocation") or {}).get("storeId") or (loc.get("storeIds") or [store_id])[0]
        return Observation(
            retailer=self.key, item_id=str(p.get("usItemId") or ""), store_id=str(rendered_store),
            price=cur, original=was if was and was != cur else None,
            clearance_price=cur if "clearance" in flags else None,
            promo=(", ".join(flags) + (f" @ {pickup.get('locationText')}" if pickup.get("locationText") else "")) or None,
            qty=pickup.get("availableQuantity"),
            in_stock=(pickup.get("availabilityStatus") == "IN_STOCK") if pickup else None,
            discontinued=p.get("availabilityStatus") == "NOT_AVAILABLE" if p.get("availabilityStatus") else None,
            store_status="CLEARANCE" if "clearance" in flags else None,
            online_status=p.get("availabilityStatus"),
            name=p.get("name") or "", brand=p.get("brand") or "", upc=str(p.get("upc") or ""),
            sku=str(p.get("usItemId") or ""), model=str(p.get("model") or ""),
            url=self.item_url(str(p.get("usItemId") or "")), raw={"sellerName": p.get("sellerName")})
