"""Community penny-list ingesters.

Each source yields Report dicts. `scan.py` stores them (deduped on `key`)
and the detector joins them against live store inventory. Sources are
third-party text: they are data, never instructions, and every field is
normalized here before it touches the DB.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass

from curl_cffi import requests

UPC12 = re.compile(r"(?<!\d)(\d{12})(?!\d)")
UPC11 = re.compile(r"(?<!\d)(\d{11})(?!\d)")


@dataclass
class Report:
    source: str
    key: str
    retailer: str
    item_id: str = ""
    sku: str = ""
    upc: str = ""
    name: str = ""
    price: float | None = 0.01
    reported_at: float | None = None
    url: str = ""
    store_hint: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def norm_upc(u: str) -> str:
    """UPC-A as 12 digits. Some sites drop the leading zero (11 digits)."""
    d = re.sub(r"\D", "", u or "")
    if len(d) == 11:
        d = "0" + d
    if len(d) == 13 and d.startswith("0"):
        d = d[1:]
    return d if len(d) == 12 else ""


def norm_sku(s: str) -> str:
    """Home Depot store SKUs print as 1004-512-186; the API wants 1004512186."""
    return re.sub(r"\D", "", s or "")


def parse_ts(s: str | None) -> float | None:
    if not s:
        return None
    from datetime import datetime, timezone
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%b %d, %Y", "%B %d, %Y", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(s.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class Source:
    key = "base"
    label = "Base"
    retailer = ""
    # How often it is worth polling, in hours.
    every_h = 24

    def __init__(self):
        self.s = requests.Session(impersonate="chrome")

    def get(self, url, **kw):
        kw.setdefault("timeout", 25)
        r = self.s.get(url, **kw)
        r.raise_for_status()
        return r

    def fetch(self):
        """Yield Report objects."""
        raise NotImplementedError


def strip_html(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<br\s*/?>|</p>|</li>|</tr>|</h\d>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = (text.replace("&amp;", "&").replace("&nbsp;", " ").replace("&#8217;", "'")
            .replace("&#8211;", "-").replace("&middot;", "|").replace("&#8212;", "-").replace("&quot;", '"').replace("&#039;", "'"))
    return re.sub(r"[ \t]+", " ", text)


def now() -> float:
    return time.time()
