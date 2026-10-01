"""Lowe's lane: the `/wpd/<itemId>/productdetail/<store>/Guest` JSON.

Probed 2026-09-30 with curl_cffi `chrome` (also `safari_ios`; `chrome124`,
`firefox`, `safari` are walled). The store number in the path is ignored:
the store comes from the `sn` cookie, and sending `sn` on a cold session
draws an Akamai 403, so warm the session with one request first. About 70
requests in 15 minutes from one IP bought a block that lasted over 72
minutes, so this lane is for verifying a short list, never for sweeping.
Store numbers come from `sitemap/store0.xml` (the locator API is
challenge-gated).
"""

from __future__ import annotations

import re

from curl_cffi import requests

from .base import LaneBlocked, LaneRetry, Observation, Retailer, Store, transport

WPD = "https://www.lowes.com/wpd/{item}/productdetail/{store}/Guest"
SITEMAP = "https://www.lowes.com/sitemap/store0.xml"
JH = {"Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
      "Referer": "https://www.lowes.com/"}
STORE_URL = re.compile(r"https://www\.lowes\.com/store/([A-Z]{2})-([^/<]+)/(\d+)")


class Lowes(Retailer):
    key = "lowes"
    label = "Lowe's"
    gap = 4.0
    can_sweep = False

    def __init__(self):
        self.s = requests.Session(impersonate="chrome")
        self._warm = False
        self._store = None

    @transport
    def _prep(self, store_id: str) -> None:
        if not self._warm:
            r = self.s.get(WPD.format(item="1000381989", store="1539"), headers=JH, timeout=25)
            if r.status_code == 403:
                raise LaneBlocked("lowes 403 on warm-up")
            self._warm = True
        if self._store != store_id:
            self.s.cookies.set("sn", str(store_id), domain=".lowes.com")
            self._store = store_id

    @transport
    def stores_near(self, zip_code: str, radius: float = 25) -> list[Store]:
        """The sitemap lists every store with state and city, no distance.
        Filter to the two states around the zip; distance stays None and the
        user picks stores by name in Settings."""
        r = self.s.get(SITEMAP, headers={"Accept": "application/xml"}, timeout=25)
        if r.status_code != 200:
            raise LaneRetry(f"lowes sitemap {r.status_code}")
        states = _states_for_zip(zip_code)
        out = []
        for st, city, num in STORE_URL.findall(r.text):
            if st in states:
                out.append(Store(self.key, num, f"Lowe's of {city.replace('-', ' ')}", city=city.replace("-", " "),
                                 state=st))
        return out

    @transport
    def lookup(self, item_id: str, store_id: str) -> Observation | None:
        self._prep(store_id)
        r = self.s.get(WPD.format(item=item_id, store=store_id), headers=JH, timeout=25)
        if r.status_code == 403:
            raise LaneBlocked("lowes 403")
        if r.status_code == 404:
            return None
        if r.status_code != 200 or "json" not in (r.headers.get("content-type") or ""):
            raise LaneRetry(f"lowes {r.status_code}")
        pd = (r.json().get("productDetails") or {}).get(str(item_id))
        return self._obs(pd, store_id) if pd else None

    def by_upc(self, upc: str, store_id: str) -> Observation | None:
        return None  # no UPC search lane found; the local index resolves scans

    def item_url(self, item_id: str) -> str:
        return f"https://www.lowes.com/pd/{item_id}"

    def _obs(self, pd: dict, store_id: str) -> Observation:
        prod = pd.get("product") or {}
        loc = pd.get("location") or {}
        price = loc.get("price") or {}
        pdl = (price.get("pricingDataList") or [{}])[0]
        reason = price.get("priceTypeReason") or ""
        qty = in_stock = None
        for a in (loc.get("itemInventory") or {}).get("itemAvailList") or []:
            if a.get("fullMtdMsg") == "Pickup":
                qty = a.get("onhandQty")
                in_stock = bool(a.get("isAvlSts"))
        inv = loc.get("itemInventory") or {}
        aisle = (inv.get("productLocation") or {}).get("aisle")
        status = prod.get("status")
        final = pdl.get("finalPrice")
        base = pdl.get("basePrice") or pdl.get("retailPrice")
        markdown = "_MD_" in reason
        return Observation(
            retailer=self.key, item_id=str(prod.get("omniItemId") or pd.get("itemId") or ""),
            store_id=str(loc.get("storeNumber") or store_id), price=final, original=base if base != final else None,
            clearance_price=final if markdown else None,
            promo=(f"{pdl.get('displayType')} {reason.split(':')[0]}" + (f" aisle {aisle}" if aisle else "")).strip(),
            qty=int(qty) if qty is not None else None, in_stock=in_stock,
            discontinued=status in ("DISCONTINUED", "REMOVED", "DELETED"),
            store_status="CLEARANCE" if markdown else status, online_status=str(price.get("type") or ""),
            name=prod.get("description") or prod.get("title") or "", brand=prod.get("brand") or "",
            upc=str(prod.get("barcode") or ""), sku=str(prod.get("itemNumber") or ""),
            model=str(prod.get("modelId") or ""), url=prod.get("pdURL") or "", raw=pd)


def _states_for_zip(zip_code: str) -> set[str]:
    z = int((zip_code or "0")[:3] or 0)
    # Coarse 3-digit prefix map for the Kansas City metro and neighbours.
    if 640 <= z <= 658:
        return {"MO", "KS"}
    if 660 <= z <= 679:
        return {"KS", "MO"}
    return set()

