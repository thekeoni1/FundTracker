"""Traction score for an emerging manager (PLAN.md 7.6). Pure: facts in, score and reasons out.

The emerging view asks which shops that launched 6 to 18 months ago are
working: money raised, investors on board, an amendment or a second vehicle
filed, recent activity. derive.build_manager_stats stores the facts on the
manager row; this module only weighs them. Tune TRACTION_WEIGHTS and the
thresholds below without touching the logic.
"""

import json
from datetime import date

TRACTION_WEIGHTS = {
    "raised": 25,             # any fund's latest filing reports an amount sold above 0
    "raised_10m": 10,         # total sold at least RAISED_10M
    "raised_50m": 10,         # total sold at least RAISED_50M
    "investors": 10,          # total investors above 0
    "investors_10": 5,        # total investors at least MANY_INVESTORS
    "amended": 15,            # a D/A filed after the first notice
    "second_vehicle": 15,     # two or more fund vehicles
    "recent_activity": 10,    # a filing within RECENT_MONTHS
    "hedge": 0,               # label: a Hedge Fund among the latest fund types (type all only)
    "large": -20,             # LARGE_FUNDS or more funds
    "nothing_raised": -20,    # every fund still reports nothing sold and no investors
}
TRACTION_LABELS = {
    "raised": "raised money",
    "raised_10m": "raised 10m or more",
    "raised_50m": "raised 50m or more",
    "investors": "has investors",
    "investors_10": "10 or more investors",
    "amended": "amended after the first notice",
    "second_vehicle": "second fund vehicle",
    "recent_activity": "filed in the last 6 months",
    "hedge": "runs a hedge fund",
    "large": "many funds already",
    "nothing_raised": "nothing raised yet",
}
RAISED_10M = 10_000_000
RAISED_50M = 50_000_000
MANY_INVESTORS = 10
RECENT_MONTHS = 6
LARGE_FUNDS = 10
HEDGE_FUND = "Hedge Fund"


def months_before(day, months):
    """The same day of the month, months earlier, clamped to the month's last day."""
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    month += 1
    for d in (day.day, 30, 29, 28):
        try:
            return date(year, month, d)
        except ValueError:
            continue


def age_months(first_seen, today):
    """Whole months from first_seen (ISO date) to today."""
    first = date.fromisoformat(first_seen)
    months = (today.year - first.year) * 12 + today.month - first.month
    return months - (today.day < first.day)


def score_manager(facts):
    """(score 0 to 100, reasons) for one manager.

    facts is a manager row (derive.build_manager_stats columns) plus:
      today   the date the window is measured from
      type    "hedge" or "all"; the hedge label is only given in all mode
    reasons is a list of {"code", "points", "detail"} in the order applied.
    """
    reasons = []

    def add(code, detail):
        reasons.append({"code": code, "points": TRACTION_WEIGHTS[code], "detail": detail})

    sold = facts.get("total_sold") or 0
    investors = facts.get("total_investors") or 0
    n_funds = facts.get("n_funds") or 0
    if sold > 0:
        add("raised", f"{sold:,} sold across funds")
    if sold >= RAISED_10M:
        add("raised_10m", f"{sold:,} sold")
    if sold >= RAISED_50M:
        add("raised_50m", f"{sold:,} sold")
    if investors > 0:
        add("investors", f"{investors:,} investors")
    if investors >= MANY_INVESTORS:
        add("investors_10", f"{investors:,} investors")
    if facts.get("has_amendment"):
        add("amended", "a fund's latest filing is a D/A")
    if n_funds >= 2:
        add("second_vehicle", f"{n_funds} funds")
    latest = facts.get("latest_filing_date")
    if latest and latest >= months_before(facts["today"], RECENT_MONTHS).isoformat():
        add("recent_activity", f"latest filing {latest}")
    types = facts.get("fund_types") or []
    if isinstance(types, str):
        types = json.loads(types)
    if facts.get("type") == "all" and HEDGE_FUND in types:
        add("hedge", HEDGE_FUND)
    if n_funds >= LARGE_FUNDS:
        add("large", f"{n_funds} funds")
    if sold == 0 and investors == 0:
        add("nothing_raised", "every fund reports nothing sold and no investors")

    score = max(0, min(100, sum(r["points"] for r in reasons)))
    return score, reasons


def reason_phrase(reason):
    """A reason dict as its label and signed points: "raised money (+25)"."""
    label = TRACTION_LABELS.get(reason["code"], reason["code"].replace("_", " "))
    return f"{label} ({reason['points']:+d})" if reason["points"] else label
