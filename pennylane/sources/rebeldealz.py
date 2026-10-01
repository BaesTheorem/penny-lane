"""RebelDealz Dollar General list: server-rendered HTML table with a
Confirmed / Reported status per row and UPC-12s. No feed. Verified 2026-09-30.
"""

from __future__ import annotations

import re

from .base import Report, Source, norm_upc, parse_ts, strip_html

URL = "https://www.rebeldealz.net/blog/dollar-general-penny-list"
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
DATE = re.compile(r"([A-Z][a-z]{2,8} \d{1,2}, \d{4})")


class RebelDealz(Source):
    key = "rebeldealz_dg"
    label = "RebelDealz (DG)"
    retailer = "dollargeneral"
    every_h = 12

    def fetch(self):
        html = self.get(URL).text
        m = DATE.search(strip_html(html)[:5000])
        when = parse_ts(m.group(1)) if m else None
        for row in ROW.finditer(html):
            cells = [strip_html(c).strip() for c in CELL.findall(row.group(1))]
            if len(cells) < 2:
                continue
            upc = ""
            for c in cells:
                u = norm_upc(c)
                if u:
                    upc = u
                    break
            if not upc:
                continue
            name = next((c for c in cells if c and not norm_upc(c) and c.lower() not in ("confirmed", "reported")), "")
            status = next((c for c in cells if c.lower() in ("confirmed", "reported")), "")
            yield Report(self.key, f"{self.key}:{upc}", self.retailer, upc=upc, name=name[:120],
                         reported_at=when, first_reported_at=when, url=URL, store_hint=status)
