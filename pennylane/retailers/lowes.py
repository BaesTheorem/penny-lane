"""Lowe's lane: the `/wpd/<itemId>/productdetail/<store>/Guest` JSON.

Probed 2026-09-30 with curl_cffi `chrome` (also `safari_ios`; `chrome124`,
`firefox`, `safari` are walled). The store number in the path is ignored:
the store comes from the `sn` cookie, and sending `sn` on a cold session
draws an Akamai 403, so warm the session with one request first. About 70
requests in 15 minutes from one IP bought a block that lasted over 72
minutes, so this lane is for verifying a short list, never for sweeping.
Stores: `sitemap/store0.xml` is gone (404 since 2026-10, also for All the
Places). The per-state directory `/Lowes-Stores/<State>/<ST>` still answers
with every store number and city in the state, one request per state.
Coordinates come from `lowes_stores.json` (an All the Places snapshot, see
`scripts/lowes-stores.py`); the live directory adds stores opened since the
snapshot. It cannot mark a store closed: it links one store per city, so a
city with five stores shows one. The locator API is challenge-gated.
"""

from __future__ import annotations

import json
import logging
import re
from functools import cache
from pathlib import Path

from curl_cffi import requests

from .base import LaneBlocked, LaneRetry, Observation, Retailer, Store, geocode_zip, miles, transport

log = logging.getLogger("pennylane.lowes")

WPD = "https://www.lowes.com/wpd/{item}/productdetail/{store}/Guest"
DIRECTORY = "https://www.lowes.com/Lowes-Stores/{name}/{st}"
SNAPSHOT = Path(__file__).with_name("lowes_stores.json")
JH = {"Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
      "Referer": "https://www.lowes.com/"}
STORE_URL = re.compile(r"/store/([A-Z]{2})-([^/\"'<]+)/(\d+)")
STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
          "CT": "Connecticut", "DE": "Delaware", "DC": "District-Of-Columbia", "FL": "Florida", "GA": "Georgia",
          "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
          "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
          "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
          "NE": "Nebraska", "NV": "Nevada", "NH": "New-Hampshire", "NJ": "New-Jersey", "NM": "New-Mexico",
          "NY": "New-York", "NC": "North-Carolina", "ND": "North-Dakota", "OH": "Ohio", "OK": "Oklahoma",
          "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode-Island", "SC": "South-Carolina", "SD": "South-Dakota",
          "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
          "WV": "West-Virginia", "WI": "Wisconsin", "WY": "Wyoming"}


class Lowes(Retailer):
    key = "lowes"
    label = "Lowe's"
    gap = 4.0
    can_sweep = False
    takes_point = True

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

    def stores_near(self, zip_code: str, radius: float = 25, lat=None, lon=None) -> list[Store]:
        """Snapshot stores within the radius, plus any store the live state
        directory shows that the snapshot does not have."""
        if lat is None or lon is None:
            lat, lon = geocode_zip(zip_code)
        snap = _snapshot()
        near = {sid: (st, miles(lat, lon, st["lat"], st["lon"])) for sid, st in snap.items()}
        near = {sid: v for sid, v in near.items() if v[1] <= radius}
        # States the circle touches: every state with a store within radius + 50 miles.
        states = {st["state"] for st in snap.values() if miles(lat, lon, st["lat"], st["lon"]) <= radius + 50}
        live: dict[str, tuple[str, str]] = {}
        try:
            for code in sorted(states):
                live.update(self._directory(code))
        except (LaneBlocked, LaneRetry) as e:
            log.warning("lowes directory: %s; using the snapshot alone", e)
        out = []
        for sid, (st, dist) in near.items():
            out.append(Store(self.key, sid, f"Lowe's of {st['city']}", st.get("address", ""), st["city"], st["state"],
                             st.get("zip", ""), st["lat"], st["lon"], round(dist, 1)))
        # Opened since the snapshot: no coordinates, so no distance; Settings lists them to pick by hand.
        for sid, (code, city) in live.items():
            if sid not in snap:
                out.append(Store(self.key, sid, f"Lowe's of {city}", city=city, state=code))
        return out

    @transport
    def _directory(self, code: str) -> dict[str, tuple[str, str]]:
        name = STATES.get(code)
        if not name:
            return {}
        r = self.s.get(DIRECTORY.format(name=name, st=code), headers={"Accept": "text/html"}, timeout=25)
        if r.status_code == 403:
            raise LaneBlocked("lowes 403 on the store directory")
        if r.status_code != 200:
            raise LaneRetry(f"lowes directory {code} {r.status_code}")
        found = {num.zfill(4): (st, city.replace("-", " ")) for st, city, num in STORE_URL.findall(r.text) if st == code}
        if not found:
            raise LaneRetry(f"lowes directory {code}: no stores in the page")
        return found

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



@cache
def _snapshot() -> dict[str, dict]:
    return json.loads(SNAPSHOT.read_text())["stores"]
