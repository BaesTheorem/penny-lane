"""Dollar General lane: the `dggo.dollargeneral.com/omni/api`.

Probed 2026-09-30. Bootstrap: `GET /bin/dg/user?timestamp=<ms>` returns a
guest token set (1 h JWT); one `GET https://dggo.dollargeneral.com/`
(403, but it plants the F5 cookie) is required before the API answers.
Per-store price and quantity by UPC come from the v1
`/product/item/details/provider` (the site's v2 path is WAF-rejected) and
carry a literal `pennyOrZeroPriceItem` flag. `/store/search/inventory`
with a `upc` returns per-store stock for that item. There is no product
search: this lane resolves UPCs (scans, community lists), nothing else.
About 60 requests in 25 minutes drew no block.
"""

from __future__ import annotations

import time

from curl_cffi import requests

from .base import LaneBlocked, LaneRetry, Observation, Retailer, Store, transport

BASE = "https://dggo.dollargeneral.com/omni/api"
H = {"Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
     "Referer": "https://www.dollargeneral.com/", "Origin": "https://www.dollargeneral.com"}


class DollarGeneral(Retailer):
    key = "dollargeneral"
    label = "Dollar General"
    gap = 1.0
    can_sweep = False

    def __init__(self):
        self.s = requests.Session(impersonate="chrome")
        self.hdr = None
        self.hdr_at = 0.0

    @transport
    def _auth(self) -> dict:
        if self.hdr and time.time() - self.hdr_at < 50 * 60:
            return self.hdr
        r = self.s.get(f"https://www.dollargeneral.com/bin/dg/user?timestamp={int(time.time() * 1000)}",
                       headers=H, timeout=25)
        if r.status_code != 200:
            raise LaneRetry(f"dollargeneral user bootstrap {r.status_code}")
        d = r.json()
        self.s.get("https://dggo.dollargeneral.com/", headers={**H, "Accept": "text/html"}, timeout=25)
        dev = next((c.value for c in self.s.cookies.jar if c.name == "uniqueDeviceId"), "")
        self.hdr = {**H, "Content-Type": "application/json", "Authorization": "Bearer " + d["idToken"],
                    "X-DG-AppToken": d["appToken"], "X-DG-AppSessionToken": d["appSessionToken"],
                    "X-DG-DeviceUniqueID": dev, "X-DG-CustomerGUID": d["customerGuid"],
                    "X-DG-PartnerAPIToken": d.get("partnerApiToken") or ""}
        self.hdr_at = time.time()
        return self.hdr

    @transport
    def _post(self, path: str, body: dict) -> dict | list:
        r = self.s.post(BASE + path, headers=self._auth(), json=body, timeout=25)
        if r.status_code in (403, 429):
            raise LaneBlocked(f"dollargeneral {r.status_code} on {path}")
        if r.status_code != 200 or not r.text.strip():
            raise LaneRetry(f"dollargeneral {r.status_code} empty on {path}")
        return r.json()

    def stores_near(self, zip_code: str, radius: float = 25, lat=None, lon=None) -> list[Store]:
        if lat is None or lon is None:
            lat, lon = _geocode_zip(zip_code)
        d = self._post("/store/search/inventory", {"latitude": lat, "longitude": lon, "radius": radius})
        out = []
        for st in (d.get("stores") or []) if isinstance(d, dict) else []:
            out.append(Store(self.key, str(st["storeNumber"]),
                             f"DG {st.get('address', '')}".strip(), st.get("address", ""), st.get("city", ""),
                             st.get("state", ""), st.get("zipCode", ""), st.get("latitude"), st.get("longitude"),
                             float(st["distance"]) if st.get("distance") is not None else None))
        return out

    def lookup(self, item_id: str, store_id: str) -> Observation | None:
        """DG item ids ARE UPCs (11 or 12 digits)."""
        return self.by_upc(item_id, store_id)

    def by_upc(self, upc: str, store_id: str) -> Observation | None:
        p = self._post("/product/item/details/provider", {"upc": upc.lstrip("0") or upc, "store": int(store_id)})
        if not isinstance(p, dict) or not (p.get("Upc") or p.get("salsifyUpc") or p.get("sku")):
            return None
        return self._obs(p, store_id)

    def item_url(self, item_id: str) -> str:
        return f"https://www.dollargeneral.com/p/{item_id}"

    def _obs(self, p: dict, store_id: str) -> Observation:
        final = p.get("finalPrice")
        orig = p.get("originalPrice")
        penny = bool(p.get("pennyOrZeroPriceItem"))
        qty = p.get("availableStockStore")
        offers = "; ".join(f"{o.get('method', '')} {o.get('savings', '')}".strip() for o in p.get("offers") or [])
        upc = str(p.get("salsifyUpc") or p.get("Upc") or "")
        hier = p.get("categoryHierarchies") or []
        category = (hier[0] if isinstance(hier, list) and hier else "") or (p.get("category") or "").split("|")[0] \
            or (p.get("pogPrimaryCategory") or "")
        return Observation(
            retailer=self.key, item_id=upc.lstrip("0") or upc, store_id=str(store_id),
            price=0.01 if penny else final, original=orig if orig not in (None, final) else None,
            clearance_price=final if (orig and final and final < orig) else None,
            promo=(offers + (f"; {p['nearByStoresAvailabilityCount']} nearby stores have it" if p.get("nearByStoresAvailabilityCount") else "")).strip("; ") or None, qty=int(qty) if qty is not None else None,
            in_stock=(p.get("stockStatus") in (2, 3)) if p.get("stockStatus") is not None else None,
            discontinued=None, store_status="PENNY" if penny else None,
            online_status=str(p.get("isSellable")) if p.get("isSellable") is not None else None,
            name=p.get("description") or p.get("productName") or "",
            brand=p.get("brand") or "", upc=upc.zfill(12) if upc else "", sku=str(p.get("sku") or ""),
            url=self.item_url(upc.lstrip("0") or upc), category=category, raw=p)


def _geocode_zip(zip_code: str) -> tuple[float, float]:
    """Zip centroid from the free Zippopotam service; cached by the caller's
    config in practice (stores are refreshed rarely)."""
    r = requests.get(f"https://api.zippopotam.us/us/{zip_code}", timeout=20)
    if r.status_code != 200:
        raise LaneRetry(f"geocode {zip_code}: {r.status_code}")
    pl = r.json()["places"][0]
    return float(pl["latitude"]), float(pl["longitude"])
