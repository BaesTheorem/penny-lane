"""WordPress penny lists via the open wp-json posts endpoint.

Each site publishes a weekly (Dollar General, Tuesdays) or batched (Home
Depot) post whose body carries UPC-12s, sometimes with a name on the same
line. We pull the newest matching posts, strip the HTML and take every
UPC with the text around it as the name. RetailShout prints 11-digit UPCs
(leading zero dropped) and a per-item "Penny Date"; norm_upc restores the
zero. Verified 2026-09-30 for all four sites.
"""

from __future__ import annotations

import re

from .base import UPC11, UPC12, Report, Source, norm_sku, norm_upc, parse_ts, strip_html

SITES = [
    # key, label, retailer, base, search term, item-line style
    ("freebieguy_dg", "The Freebie Guy (DG)", "dollargeneral", "https://thefreebieguy.com", "penny", "upc"),
    ("pennypinchinmom_dg", "Penny Pinchin' Mom (DG)", "dollargeneral", "https://pennypinchinmom.com",
     "dollar general penny list", "upc"),
    ("pennypinchinmom_hd", "Penny Pinchin' Mom (HD)", "homedepot", "https://pennypinchinmom.com",
     "home depot penny", "sku"),
    ("retailshout_dg", "RetailShout (DG)", "dollargeneral", "https://retailshout.com", "dollar general penny", "block"),
]

SKU_HD = re.compile(r"\b(\d{4}-\d{3}-\d{3}|\d{10})\b")
PENNY_DATE = re.compile(r"Penny Date:?\s*([A-Za-z]{3,9}\.? \d{1,2},? \d{4})")
# RetailShout: "Name\n Penny Date: Sep 25, 2026\n SKU: 32271201 | UPC: 17000329024"
BLOCK = re.compile(r"\n\s*(?P<name>[^\n]{4,160}?)\s*\n\s*Penny Date:?\s*(?P<date>[^\n|]+?)\s*\n\s*SKU:?\s*(?P<sku>\d+)\s*\|\s*UPC:?\s*(?P<upc>\d{11,13})", re.I)


class WordPressList(Source):
    every_h = 12

    def __init__(self, key, label, retailer, base, term, style):
        super().__init__()
        self.key, self.label, self.retailer = key, label, retailer
        self.base, self.term, self.style = base, term, style

    def posts(self, n=4):
        r = self.get(f"{self.base}/wp-json/wp/v2/posts",
                     params={"search": self.term, "per_page": n, "orderby": "modified", "order": "desc",
                             "_fields": "id,date,modified,link,title,content"})
        return r.json()

    def fetch(self):
        try:
            posts = self.posts()
        except ValueError:
            # A bot-verification page instead of JSON (Penny Pinchin' Mom does
            # this from some IPs). Best effort: skip this poll.
            return
        for post in posts:
            title = strip_html((post.get("title") or {}).get("rendered") or "")
            if self.retailer == "dollargeneral" and "penny" not in title.lower():
                continue
            link = post.get("link") or ""
            modified = parse_ts(post.get("modified") or post.get("date"))
            text = strip_html((post.get("content") or {}).get("rendered") or "")
            seen = set()
            if self.style == "block":
                for m in BLOCK.finditer(text):
                    upc = norm_upc(m.group("upc"))
                    if not upc or upc in seen:
                        continue
                    seen.add(upc)
                    when = parse_ts(m.group("date").replace(".", "")) or modified
                    yield Report(self.key, f"{self.key}:{upc}", self.retailer, upc=upc, sku=m.group("sku"),
                                 name=m.group("name").strip()[:120],
                                 reported_at=when, first_reported_at=when, url=link)
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if self.style == "sku":
                    for m in SKU_HD.finditer(line):
                        sku = norm_sku(m.group(1))
                        if sku in seen:
                            continue
                        seen.add(sku)
                        yield Report(self.key, f"{self.key}:{sku}", self.retailer, sku=sku,
                                     name=_name_from(line, m.group(0)), reported_at=modified, url=link)
                    continue
                rx = UPC11 if self.style == "upc11" else UPC12
                for m in rx.finditer(line):
                    upc = norm_upc(m.group(1))
                    if not upc or upc in seen:
                        continue
                    seen.add(upc)
                    when = modified
                    pd = PENNY_DATE.search(line)
                    if pd:
                        when = parse_ts(pd.group(1).replace(".", "")) or modified
                    yield Report(self.key, f"{self.key}:{upc}", self.retailer, upc=upc,
                                 name=_name_from(line, m.group(0)), reported_at=when, first_reported_at=when, url=link)


def _name_from(line: str, token: str) -> str:
    name = line.split(token)[0]
    name = re.sub(r"(UPC|SKU|Penny Date)[:#\s]*$", "", name, flags=re.I).strip(" -:|,.–")
    name = re.sub(r"\s+", " ", name)
    return name[:120]


def all_sites():
    return [WordPressList(*row) for row in SITES]
