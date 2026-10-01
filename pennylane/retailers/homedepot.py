"""Home Depot lane: the federation-gateway GraphQL.

Probed 2026-09-30 from this Mac with curl_cffi Chrome impersonation: no
cookies or store header needed, storeId is an argument on `pricing` and
`fulfillment`, 20 requests at 0.5 s draw no block, `pageSize` above 24 on
searchModel draws an Akamai 403, and an upstream outage shows up as a
`206 {"error":[{"message":"Generic errors"}]}` that means "retry later",
not "blocked". `keyword` search returns empty; navParam and itemIds work.
`/p/<id>` HTML is behind a behavioral challenge: dead lane.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from curl_cffi import requests

from .base import LaneBlocked, LaneRetry, Observation, Retailer, Store, transport

GW = "https://www.homedepot.com/federation-gateway/graphql?opname="
STORE_SEARCH = "https://www.homedepot.com/StoreSearchServices/v2/storesearch"
HEADERS = {
    "content-type": "application/json",
    "x-experience-name": "general-merchandise",
    "x-hd-dc": "origin",
    "origin": "https://www.homedepot.com",
    "referer": "https://www.homedepot.com/",
    "accept": "*/*",
}

PRODUCT_FIELDS = """
    itemId
    identifiers { itemId modelNumber upc upcGtin13 storeSkuNumber productLabel brandName canonicalUrl }
    info { classNumber subClassNumber productDepartment isSpecialBuy hidePrice }
    availabilityType { buyable discontinued type }
    pricing(storeId: $storeId) { value original specialBuy message unitOfMeasure
      clearance { value dollarOff percentageOff unitsClearancePrice }
      promotion { type dollarOff percentageOff savingsCenter
        description { shortDesc } dates { start end } } }
    fulfillment(storeId: $storeId) { anchorStoreStatus anchorStoreStatusType onlineStoreStatus
      fulfillmentOptions { type fulfillable services { type locations { isAnchor locationId storeName
        inventory { isOutOfStock isInStock isLimitedQuantity isUnavailable quantity } } } } }
"""

PRODUCT_Q = f"""query productClientOnlyProduct($storeId: String, $itemId: String!) {{
  product(itemId: $itemId) {{ {PRODUCT_FIELDS} }} }}"""

PRODUCTS_Q = f"""query productClientOnlyProducts($storeId: String, $itemIds: [String!]!) {{
  products(itemIds: $itemIds) {{ {PRODUCT_FIELDS} }} }}"""

SEARCH_Q = f"""query searchModel($navParam: String, $itemIds: [String], $storeId: String,
  $storefilter: StoreFilter = ALL, $channel: Channel = DESKTOP, $pageSize: Int, $startIndex: Int) {{
  searchModel(navParam: $navParam, itemIds: $itemIds, storeId: $storeId, storefilter: $storefilter, channel: $channel) {{
    searchReport {{ totalProducts }}
    products(pageSize: $pageSize, startIndex: $startIndex) {{ {PRODUCT_FIELDS} }} }} }}"""

STORES_Q = """query storeSearch($zipCode: String!, $radius: Float!, $limit: Int) {
  storeSearch(zipCode: $zipCode, radius: $radius, limit: $limit) {
    storeId storeName distance address { street city state postalCode } } }"""

# Special Values refinement (the only price-dimension key that exposes markdowns).
SPECIAL_VALUES_NAV = "5yc1vZ7"
PAGE = 24
# products(itemIds) answers null above 12 ids (24 -> null, 12 -> 11 of 12).
BATCH = 12


class HomeDepot(Retailer):
    key = "homedepot"
    label = "Home Depot"
    gap = 0.6
    can_sweep = True

    def __init__(self):
        self.s = requests.Session(impersonate="chrome")

    # ---- transport ----
    @transport
    def gql(self, op: str, query: str, variables: dict, tries: int = 4) -> dict:
        # 206 is Home Depot's own upstream hiccup (AkamaiGHost "Generic errors");
        # it clears within seconds to minutes, so wait it out before giving up.
        r = self.s.post(GW + op, headers=HEADERS,
                        json={"operationName": op, "variables": variables, "query": query}, timeout=25)
        for attempt in range(1, tries):
            if r.status_code != 206:
                break
            time.sleep(3 * attempt)
            r = self.s.post(GW + op, headers=HEADERS,
                            json={"operationName": op, "variables": variables, "query": query}, timeout=25)
        if r.status_code == 206:
            raise LaneRetry("homedepot 206 upstream error")
        if r.status_code in (403, 429):
            raise LaneBlocked(f"homedepot {r.status_code}")
        if r.status_code != 200:
            raise LaneRetry(f"homedepot HTTP {r.status_code}")
        body = r.json()
        if "data" not in body:
            raise LaneRetry(f"homedepot no data: {str(body)[:200]}")
        return body["data"]

    # ---- adapter ----
    @transport
    def stores_near(self, zip_code: str, radius: float = 25) -> list[Store]:
        # GraphQL first (it rides the 206 retry); the REST locator is the fallback.
        try:
            d = self.gql("storeSearch", STORES_Q, {"zipCode": zip_code, "radius": float(radius), "limit": 30})
            out = []
            for st in d.get("storeSearch") or []:
                a = st.get("address") or {}
                out.append(Store(self.key, str(st["storeId"]), st.get("storeName", ""), a.get("street", ""),
                                 a.get("city", ""), a.get("state", ""), a.get("postalCode", ""),
                                 None, None, float(st["distance"]) if st.get("distance") is not None else None))
            if out:
                return out
        except LaneRetry:
            pass
        r = self.s.get(STORE_SEARCH, params={"address": zip_code, "radius": radius, "pagesize": 30},
                       headers={"accept": "application/json", "referer": "https://www.homedepot.com/"},
                       timeout=25)
        if r.status_code != 200:
            raise LaneRetry(f"homedepot store search {r.status_code}")
        out = []
        for st in r.json().get("stores", []):
            a = st.get("address") or {}
            c = st.get("coordinates") or {}
            out.append(Store(self.key, str(st["storeId"]), st.get("name", ""),
                             a.get("street", ""), a.get("city", ""), a.get("state", ""),
                             a.get("postalCode", ""), c.get("lat"), c.get("lng"),
                             float(st["distance"]) if st.get("distance") is not None else None))
        return out

    def lookup(self, item_id: str, store_id: str) -> Observation | None:
        d = self.gql("productClientOnlyProduct", PRODUCT_Q, {"itemId": str(item_id), "storeId": str(store_id)})
        p = d.get("product")
        return self._obs(p, store_id) if p else None

    def lookup_many(self, item_ids: list[str], store_id: str) -> list[Observation]:
        out = []
        for i in range(0, len(item_ids), BATCH):
            chunk = [str(x) for x in item_ids[i:i + BATCH]]
            d = self.gql("productClientOnlyProducts", PRODUCTS_Q, {"itemIds": chunk, "storeId": str(store_id)})
            for p in d.get("products") or []:
                if p:
                    out.append(self._obs(p, store_id))
            if i + BATCH < len(item_ids):
                time.sleep(self.gap)
        return out

    def sweep(self, store_id: str, max_pages: int = 400, nav: str = SPECIAL_VALUES_NAV) -> Iterator[Observation]:
        """Every in-store Special Values item at the store, 24 per request."""
        start = 0
        for _ in range(max_pages):
            d = self.gql("searchModel", SEARCH_Q, {
                "navParam": nav, "storeId": str(store_id), "storefilter": "IN_STORE",
                "channel": "DESKTOP", "pageSize": PAGE, "startIndex": start})
            sm = d.get("searchModel") or {}
            prods = sm.get("products") or []
            if not prods:
                return
            for p in prods:
                yield self._obs(p, store_id)
            total = ((sm.get("searchReport") or {}).get("totalProducts")) or 0
            start += PAGE
            if start >= total:
                return
            time.sleep(self.gap)

    def item_url(self, item_id: str) -> str:
        return f"https://www.homedepot.com/p/{item_id}"

    # ---- parsing ----
    def _obs(self, p: dict, store_id: str) -> Observation:
        ident = p.get("identifiers") or {}
        info = p.get("info") or {}
        avail = p.get("availabilityType") or {}
        pr = p.get("pricing") or {}
        cl = pr.get("clearance") or {}
        promo = pr.get("promotion") or {}
        ful = p.get("fulfillment") or {}
        qty = in_stock = None
        # Shelf stock is the pickup/bopis service at the anchor store. The
        # pickup/boss and delivery services report warehouse counts, which is
        # why a store with nothing on the shelf can show "321".
        for opt in ful.get("fulfillmentOptions") or []:
            if opt.get("type") != "pickup":
                continue
            for svc in opt.get("services") or []:
                if svc.get("type") != "bopis":
                    continue
                for loc in svc.get("locations") or []:
                    if loc.get("isAnchor") or str(loc.get("locationId")) == str(store_id):
                        inv = loc.get("inventory") or {}
                        if inv.get("quantity") is not None:
                            qty = int(inv["quantity"])
                        if inv.get("isInStock") is not None:
                            in_stock = bool(inv["isInStock"])
        if qty is None and ful.get("fulfillmentOptions") is not None:
            qty, in_stock = 0, False
        canon = ident.get("canonicalUrl") or ""
        url = ("https://www.homedepot.com" + canon) if canon.startswith("/") else self.item_url(p.get("itemId", ""))
        promo_txt = promo.get("savingsCenter") or (promo.get("description") or {}).get("shortDesc")
        return Observation(
            retailer=self.key, item_id=str(p.get("itemId") or ident.get("itemId") or ""),
            store_id=str(store_id), price=pr.get("value"), original=pr.get("original"),
            clearance_price=cl.get("value"), promo=promo_txt, qty=qty, in_stock=in_stock,
            discontinued=bool(avail.get("discontinued")) or ful.get("anchorStoreStatusType") == "DELETED",
            online_status=ful.get("onlineStoreStatus"), store_status=ful.get("anchorStoreStatusType"),
            name=ident.get("productLabel") or "", brand=ident.get("brandName") or "",
            upc=ident.get("upc") or ident.get("upcGtin13") or "", sku=ident.get("storeSkuNumber") or "",
            model=ident.get("modelNumber") or "", url=url,
            dept=str(info.get("productDepartment") or ""), raw=p)
