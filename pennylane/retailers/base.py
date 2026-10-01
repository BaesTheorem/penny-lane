"""The adapter contract every retailer lane implements.

An adapter turns (item, store) into an Observation: the price the register
will charge at that store right now, what it was, whether the item is on
clearance, and how many are on the shelf. Adapters never write to the DB;
`scan.py` records what they return.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


@dataclass
class Store:
    retailer: str
    store_id: str
    name: str
    address: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    lat: float | None = None
    lon: float | None = None
    distance: float | None = None


@dataclass
class Observation:
    retailer: str
    item_id: str
    store_id: str
    price: float | None = None
    original: float | None = None
    clearance_price: float | None = None
    promo: str | None = None
    qty: int | None = None
    in_stock: bool | None = None
    discontinued: bool | None = None
    online_status: str | None = None
    store_status: str | None = None
    name: str = ""
    brand: str = ""
    upc: str = ""
    sku: str = ""
    model: str = ""
    url: str = ""
    dept: str = ""
    ts: float = field(default_factory=time.time)
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


class LaneBlocked(Exception):
    """The retailer answered with a bot wall or a rate limit. Back off."""


class LaneRetry(Exception):
    """A transient upstream error (Home Depot's Akamai 206). Retry later."""


def transport(fn: Callable[P, R]) -> Callable[P, R]:
    """Turn curl/network failures (timeouts, resets, DNS) into LaneRetry so a
    job records the miss and moves on instead of dying mid-run."""
    def wrapped(*a: P.args, **kw: P.kwargs) -> R:
        from curl_cffi.requests.exceptions import RequestException
        try:
            return fn(*a, **kw)
        except RequestException as e:
            raise LaneRetry(f"transport: {str(e)[:120]}") from e
    return wrapped


class Retailer:
    key = "base"
    label = "Base"
    # Seconds between requests; scan.py honours it per lane.
    gap = 0.6
    # Whether a per-store clearance sweep exists for this lane.
    can_sweep = False

    def stores_near(self, zip_code: str, radius: float = 25) -> list[Store]:
        raise NotImplementedError

    def lookup(self, item_id: str, store_id: str) -> Observation | None:
        raise NotImplementedError

    def lookup_many(self, item_ids: list[str], store_id: str) -> list[Observation]:
        out = []
        for i in item_ids:
            o = self.lookup(i, store_id)
            if o:
                out.append(o)
            time.sleep(self.gap)
        return out

    def by_upc(self, upc: str, store_id: str) -> Observation | None:
        """Resolve a scanned barcode. None when the lane has no UPC search;
        scan.py then falls back to the local UPC index built from sweeps."""
        return None

    def sweep(self, store_id: str, max_pages: int = 400):
        """Yield Observations for every clearance-ish item at a store."""
        return iter(())

    def item_url(self, item_id: str) -> str:
        return ""
