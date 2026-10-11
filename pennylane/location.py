"""Where Alex is shopping: turn a zip, a device position, or the Mac's
network location into a zip, then re-pick the stores around it.

Zip to place: Zippopotam. Position to zip: OpenStreetMap Nominatim
(reverse, one request per tap, so its 1 req/s policy is never close).
No position at all: ip-api.com from the server, which is the Mac's
network, coarse to a few miles; the UI says when it used that.
"""

from __future__ import annotations

import logging
import re
import time

from curl_cffi import requests

from . import config, db
from .retailers import registry
from .retailers.base import LaneBlocked, LaneRetry

log = logging.getLogger("pennylane.location")
UA = {"User-Agent": "PennyLane/1.0 (personal penny tracker)"}
# Least stores per retailer to watch after a move, nearest first. A retailer
# that had more watched keeps its count.
AUTO_WATCH = 3


class LocationError(Exception):
    pass


def place_for_zip(zip_code: str) -> dict:
    if not re.fullmatch(r"\d{5}", zip_code or ""):
        raise LocationError("a US zip code has 5 digits")
    r = requests.get(f"https://api.zippopotam.us/us/{zip_code}", timeout=20)
    if r.status_code != 200:
        raise LocationError(f"zip {zip_code} not found")
    pl = r.json()["places"][0]
    return {"zip": zip_code, "lat": float(pl["latitude"]), "lon": float(pl["longitude"]),
            "label": f"{pl['place name']}, {pl['state abbreviation']}"}


def place_for_point(lat: float, lon: float) -> dict:
    r = requests.get("https://nominatim.openstreetmap.org/reverse",
                     params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 18, "addressdetails": 1},
                     headers=UA, timeout=20)
    if r.status_code != 200:
        raise LocationError(f"reverse geocode {r.status_code}")
    a = r.json().get("address") or {}
    zip_code = (a.get("postcode") or "")[:5]
    if not re.fullmatch(r"\d{5}", zip_code):
        raise LocationError("no US zip code at this position")
    town = a.get("city") or a.get("town") or a.get("village") or a.get("suburb") or ""
    state = a.get("ISO3166-2-lvl4", "").replace("US-", "") or a.get("state", "")
    return {"zip": zip_code, "lat": lat, "lon": lon, "label": ", ".join(x for x in (town, state) if x)}


def place_for_network() -> dict:
    r = requests.get("http://ip-api.com/json/", params={"fields": "status,zip,lat,lon,city,region"}, timeout=15)
    d = r.json() if r.status_code == 200 else {}
    if d.get("status") != "success" or not d.get("zip"):
        raise LocationError("network location lookup failed")
    return {"zip": d["zip"], "lat": float(d["lat"]), "lon": float(d["lon"]),
            "label": f"{d.get('city', '')}, {d.get('region', '')}".strip(", ")}


def _stores_near(lane, place: dict, radius: float) -> list:
    """One retry: a locator's first call after a token bootstrap can come back empty."""
    for attempt in (0, 1):
        try:
            if lane.takes_point and place.get("lat") is not None:
                return lane.stores_near(place["zip"], radius, lat=place["lat"], lon=place["lon"])
            return lane.stores_near(place["zip"], radius)
        except LaneRetry:
            if attempt:
                raise
            time.sleep(lane.gap)
    return []


def relocate(place: dict, radius: float | None = None) -> dict:
    """Save the new home zip and rebuild the store list around it. Per lane:
    stores from the old area that are not watched are dropped, and the
    nearest stores become the watched set, as many as were watched before
    (at least AUTO_WATCH). A lane with no distances keeps
    only the watched stores that are still in range. A lane
    with no locator (Walmart) is left alone."""
    cfg = config.load()
    cfg["zip"] = place["zip"]
    if radius:
        cfg["radius_miles"] = radius
    cfg["location"] = {"lat": place.get("lat"), "lon": place.get("lon"), "label": place.get("label", "")}
    out: dict = {}
    for key, lane in registry().items():
        rconf = cfg["retailers"].setdefault(key, {"enabled": True, "stores": []})
        if not rconf.get("enabled", True):
            continue
        try:
            stores = _stores_near(lane, place, cfg["radius_miles"])
        except NotImplementedError:
            continue
        except (LaneBlocked, LaneRetry, LocationError) as e:
            log.warning("%s stores: %s", key, e)
            out[key] = {"error": str(e)}
            continue
        ranked = sorted((s for s in stores if s.distance is not None), key=lambda s: s.distance or 0)
        if ranked:
            count = max(AUTO_WATCH, len(db.watched_stores(key)))
            wanted = {s.store_id for s in ranked[:count]}
        else:
            keep = {str(s) for s in rconf.get("stores") or []}
            wanted = {s.store_id for s in stores} & keep
        ids = [s.store_id for s in stores]
        with db.tx() as c:
            c.execute("UPDATE stores SET watched=0 WHERE retailer=?", (key,))
            marks = ",".join("?" * len(ids)) or "''"
            c.execute(f"DELETE FROM stores WHERE retailer=? AND store_id NOT IN ({marks})", (key, *ids))
        for s in stores:
            db.upsert_store(s, watched=s.store_id in wanted)
        rconf["stores"] = sorted(wanted)
        out[key] = {"found": len(stores), "watched": sorted(wanted)}
    config.save(cfg)
    return {"place": place, "radius_miles": cfg["radius_miles"], "lanes": out}
