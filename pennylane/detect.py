"""The detector: how close is this item to $0.01 at this store, right now?

Every signal below is something a probe or a community page actually
showed (see README "Signals"). Scores are 0-100 and only mean "rank me";
the reasons list is what Alex reads.

Two penny stages, and what each one claims:

  penny   CONFIRMED. Two kinds of source agree: the retailer's own record
          shows the penny state at this store (db.penny_obs), and a community
          list has the item as a penny. A store-level $0.01 price with a stock
          report needs no list. Within the stage the score ranks the chance
          that a unit is still there to find.
  likely  PROBABLY PENNIED HERE. The store record shows the penny state and
          no list has the item (yet). One source only.

Neither retailer gives a shelf count for a penny (Home Depot zeroes it, Dollar
General answers a placeholder), so no stage claims "on the shelf". Away from a
penny state, nothing on the shelf caps the score at 20 whatever the markdown
state, because there is nothing to buy.

Home Depot lore, held loosely: two ladders (.00 -> .06 -> .03 -> .01 over
~13 weeks, .00 -> .04 -> .02 -> .01 over ~7 weeks); PennyCentral's 2026
guide says the ladder is broken and the DATE is the signal. So the
cadence signal here is weeks-since-clearance, not the cents digit, and
the digit is a small bonus.
"""

from __future__ import annotations

import time

from . import db

WEEK = 7 * 86400
REPORT_WINDOW = 45 * 86400


def cents(price: float | None) -> int | None:
    if price is None:
        return None
    return int(round(price * 100)) % 100


def score_item(retailer: str, item_id: str, store_id: str) -> dict | None:
    latest = db.latest(retailer, item_id, store_id)
    if not latest:
        return None
    hist = db.history(retailer, item_id, store_id, limit=400)
    reports = db.rows("SELECT * FROM reports WHERE retailer=? AND (item_id=? OR sku=? OR upc=?) AND fetched_at>?",
                      (retailer, item_id, _sku_of(retailer, item_id), _upc_of(retailer, item_id),
                       time.time() - REPORT_WINDOW))
    now = time.time()
    reasons: list[str] = []
    score = 0
    price = latest.get("price")
    qty = latest.get("qty")
    on_shelf = (qty or 0) > 0 or latest.get("in_stock") == 1

    # ---- penny state ----
    if db.penny_obs(retailer, latest):
        res = _penny(retailer, item_id, latest, hist, now)
        if res:
            return res

    # ---- clearance state ----
    if latest.get("store_status") == "CLEARANCE":
        score += 30
        reasons.append("store status CLEARANCE")
    if latest.get("clearance_price") is not None:
        score += 10
        reasons.append(f"clearance price ${latest['clearance_price']:.2f}")
    # A store on clearance drops "original", so fall back to the best known MSRP.
    orig = db.msrp(retailer, item_id, latest)
    eff = latest.get("clearance_price") or price
    off = None
    if orig and eff and orig > 0:
        off = 1 - eff / orig
        if off >= 0.7:
            score += 15
            reasons.append(f"{off:.0%} off original")
        elif off >= 0.5:
            score += 8
            reasons.append(f"{off:.0%} off original")

    # ---- price ending (small bonus, lore only) ----
    c = cents(eff)
    if c in (2, 3):
        score += 12
        reasons.append(f"price ends .0{c} (late ladder step)")
    elif c in (4, 6):
        score += 6
        reasons.append(f"price ends .0{c} (mid ladder step)")

    # ---- cadence: weeks since first clearance observation at this store ----
    first_clr = None
    steps = []
    for h in reversed(hist):  # oldest first
        if h.get("store_status") == "CLEARANCE" or h.get("clearance_price") is not None:
            first_clr = first_clr or h["ts"]
        p = h.get("clearance_price") or h.get("price")
        if p is not None and (not steps or abs(steps[-1] - p) > 0.005):
            steps.append(p)
    if first_clr:
        weeks = (now - first_clr) / WEEK
        bonus = min(14, int(weeks * 2))
        if bonus:
            score += bonus
            reasons.append(f"on clearance {weeks:.1f} weeks here")
    drops = sum(1 for a, b in zip(steps, steps[1:], strict=False) if b < a)
    if drops:
        score += min(15, 5 * drops)
        reasons.append(f"{drops} markdown step(s) seen")

    # ---- online gone, shelf not ----
    if on_shelf and (latest.get("discontinued") == 1 or latest.get("online_status") in ("0", "false", "False")):
        score += 15
        reasons.append("gone online, still on the shelf")

    # ---- community reports ----
    if reports:
        newest = max((r.get("reported_at") or r.get("fetched_at") or 0) for r in reports)
        age_d = (now - newest) / 86400
        score += 25 if age_d <= 14 else 15
        srcs = sorted({r["source"] for r in reports})
        reasons.append(f"pennied elsewhere per {', '.join(srcs)} ({age_d:.0f}d ago)")
        rep_n = _report_count(reports)
        if rep_n:
            score += min(15, rep_n // 2)
            reasons.append(f"{rep_n} reports")
        if on_shelf:
            score += 20
            reasons.append(f"{qty} on this shelf")

    # ---- depth gate ----
    # A penny report from another market says nothing about this store's ladder.
    # Imminent needs local depth: half off MSRP, or two markdown steps seen here.
    deep = (off is not None and off >= 0.5) or drops >= 2
    if on_shelf and not deep and score >= 70:
        score = 69
        depth = f"{off:.0%} off" if off is not None else "discount unknown"
        reasons.append(f"held below imminent: {depth}, {drops} markdown step(s) here")

    # ---- nothing to buy ----
    if not on_shelf:
        score = min(score, 20)
        stage = "gone"
    elif score >= 70:
        stage = "imminent"
    elif score >= 50:
        stage = "late"
    elif score >= 30:
        stage = "early"
    else:
        stage = "watch"

    next_step = None
    if first_clr and on_shelf:
        # HD lore: penny lands 7-13 weeks after the first markdown; mid-point.
        next_step = first_clr + 10 * WEEK
    return _result(min(score, 99), stage, next_step, reasons, latest)


def _penny(retailer, item_id, latest, hist, now) -> dict | None:
    """Score a pair whose latest observation shows the penny state. None means
    "one unrepeated reading": the caller scores it as an ordinary item."""
    ev = db.community(retailer, item_id, REPORT_WINDOW)
    price, qty = latest.get("price"), latest.get("qty")
    reasons: list[str] = []
    if price is not None and price <= 0.01 and (qty is not None or latest.get("in_stock") is not None):
        # A store-level price with a stock report: the register itself.
        reasons.append(f"register price is ${price:.2f}")
        if (qty or 0) > 0 or latest.get("in_stock") == 1:
            return _result(100, "penny", None, reasons, latest)
        reasons.append("store count reads 0: penny items are often zeroed while still on the shelf")
        return _result(95, "penny", None, reasons, latest)

    # The trailing run of penny observations, and what the store showed before it.
    run = 0
    for h in hist:  # newest first
        if not db.penny_obs(retailer, h):
            break
        run += 1
    before = hist[run:]
    since = hist[run - 1]["ts"] if run else latest["ts"]
    stocked = next((h for h in before if (h.get("qty") or 0) > 0), None)
    ladder = next((h for h in before if h.get("clearance_price") is not None), None)

    if retailer == "homedepot":
        if run < 2 and before:
            return None  # a single reading after other states: wait for the next check
        reasons.append("store record shows the penny state: status CLEARANCE, clearance price removed, count zeroed")
        score = 80 if ev else 55
        if stocked and since - stocked["ts"] <= 30 * 86400:
            score += 8 if ev else 10
            reasons.append(f"{stocked['qty']} on this shelf {(since - stocked['ts']) / 86400:.0f}d before the count zeroed")
        if ladder:
            score += 0 if ev else 15
            reasons.append(f"last clearance price here ${ladder['clearance_price']:.2f}")
        if before and now - since <= 14 * 86400:
            score += 5
            reasons.append(f"went to the penny state {(now - since) / 86400:.0f}d ago")
    else:
        reasons.append("the retailer's catalog marks it a penny item at this store")
        score = 85 if ev else 70
        if before and now - since <= 14 * 86400:
            score += 3
            reasons.append(f"marked {(now - since) / 86400:.0f}d ago")

    if not ev:
        reasons.append("on no community penny list yet")
        return _result(min(score, 85), "likely", None, reasons, latest)

    age_d = (now - ev["newest"]) / 86400
    line = f"pennied per {', '.join(ev['sources'])} ({age_d:.0f}d ago)"
    if ev["reports"]:
        line += f": {ev['reports']} reports" + (f" in {ev['states']} states" if ev["states"] else "")
    reasons.append(line)
    if ev["cities"]:
        score += 6
        reasons.append("reported in " + ", ".join(ev["cities"]))
    elif ev["local"]:
        score += 3
        reasons.append("reported in " + ", ".join(f"{st} ({n})" for st, n in ev["local"].items()))
    score += min(4, 2 * (ev["lists"] - 1)) + (2 if ev["listed_confirmed"] else 0) + (2 if ev["reports"] >= 20 else 0)
    reasons.append("shelf count unknown: a penny item reads 0 or a placeholder in the store system")
    return _result(min(score, 99), "penny", None, reasons, latest)


def _result(score, stage, next_step, reasons, latest):
    return {"score": int(score), "stage": stage, "next_step_at": next_step, "reasons": reasons,
            "latest": latest}


def _sku_of(retailer, item_id):
    it = db.item(retailer, item_id)
    return (it or {}).get("sku") or "-"


def _upc_of(retailer, item_id):
    it = db.item(retailer, item_id)
    return (it or {}).get("upc") or "-"


def _report_count(reports) -> int:
    n = 0
    for r in reports:
        hint = r.get("store_hint") or ""
        if hint.endswith("reports") or " reports" in hint:
            try:
                n = max(n, int(hint.split(" reports")[0].split(";")[-1].strip()))
            except ValueError:
                pass
    return n


def score_store(retailer: str, store_id: str, since_days: int = 21) -> list[dict]:
    """Score every item observed at a store recently; persist and return."""
    ids = db.rows("SELECT DISTINCT item_id FROM observations WHERE retailer=? AND store_id=? AND ts>?",
                  (retailer, store_id, time.time() - since_days * 86400))
    out = []
    for row in ids:
        r = score_item(retailer, row["item_id"], store_id)
        if not r:
            continue
        db.put_prediction(retailer, row["item_id"], store_id, r["score"], r["stage"], r["next_step_at"], r["reasons"])
        r["item_id"] = row["item_id"]
        out.append(r)
    out.sort(key=lambda r: -r["score"])
    return out
