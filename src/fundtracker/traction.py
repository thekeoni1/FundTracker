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
    # raised_band: points from RAISED_BANDS, a sweet spot rather than a floor
    "investors": 10,          # total investors above 0
    "investors_10": 5,        # total investors at least MANY_INVESTORS
    "amended": 15,            # a D/A filed after the first notice
    "second_vehicle": 15,     # two or more fund vehicles
    "recent_activity": 10,    # a filing within RECENT_MONTHS
    "hedge": 0,               # label: a Hedge Fund among the latest fund types (type all only)
    "prior_firm": 0,          # label: a signer or named person filed earlier for a large manager
    "large": -20,             # LARGE_FUNDS or more funds
    "nothing_raised": -20,    # every fund still reports nothing sold and no investors
}
TRACTION_LABELS = {
    "raised": "raised money",
    "raised_band": "amount raised",
    "investors": "has investors",
    "investors_10": "10 or more investors",
    "amended": "amended after the first notice",
    "second_vehicle": "second fund vehicle",
    "recent_activity": "filed in the last 6 months",
    "hedge": "runs a hedge fund",
    "prior_firm": "people filed earlier for a large firm",
    "large": "many funds already",
    "nothing_raised": "nothing raised yet",
}
# Amount raised in bands: (from, up to, points), from inclusive, up to exclusive,
# None for no upper edge. Small shops raising 5m to 100m score best; a total in
# the billions is an institution's fund series, not an emerging manager.
RAISED_BANDS = (
    (1_000_000, 5_000_000, 5),
    (5_000_000, 100_000_000, 15),
    (100_000_000, 500_000_000, 10),
    (500_000_000, 2_000_000_000, -10),
    (2_000_000_000, None, -30),
)
MANY_INVESTORS = 10
RECENT_MONTHS = 6
LARGE_FUNDS = 10
HEDGE_FUND = "Hedge Fund"
# Managers with this many funds or more are not emerging.
EMERGING_MAX_FUNDS = 20


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

    def add(code, detail, points=None):
        reasons.append({"code": code, "points": TRACTION_WEIGHTS[code] if points is None else points,
                        "detail": detail})

    sold = facts.get("total_sold") or 0
    investors = facts.get("total_investors") or 0
    n_funds = facts.get("n_funds") or 0
    if sold > 0:
        add("raised", f"{sold:,} sold across funds")
    band = raised_band(sold)
    if band:
        low, high, points = band
        add("raised_band", f"{sold:,} sold, in the band {_band_text(low, high)}", points)
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
    prior = facts.get("prior_firms") or []
    if isinstance(prior, str):
        prior = json.loads(prior)
    if prior:
        add("prior_firm", ", ".join(prior))
    if n_funds >= LARGE_FUNDS:
        add("large", f"{n_funds} funds")
    if sold == 0 and investors == 0:
        add("nothing_raised", "every fund reports nothing sold and no investors")

    score = max(0, min(100, sum(r["points"] for r in reasons)))
    return score, reasons


def score_managers(managers, today, fund_type, min_score=0):
    """Score repo.emerging_managers rows in place (score, reasons as JSON, age_months) and
    return those scoring min_score or more, best first, then most raised, then oldest."""
    rows = []
    for m in managers:
        m["score"], reasons = score_manager(dict(m, today=today, type=fund_type))
        m["reasons"] = json.dumps(reasons)
        m["age_months"] = age_months(m["first_seen_date"], today)
        if m["score"] >= min_score:
            rows.append(m)
    rows.sort(key=lambda m: (-m["score"], -(m["total_sold"] or 0), m["first_seen_date"], m["manager_id"]))
    return rows


def raised_band(sold):
    """The RAISED_BANDS entry holding sold, or None below the first band."""
    for low, high, points in RAISED_BANDS:
        if sold >= low and (high is None or sold < high):
            return low, high, points
    return None


def _band_text(low, high):
    return f"{low:,} and up" if high is None else f"{low:,} to {high:,}"


def reason_phrase(reason):
    """A reason dict as its label and signed points: "raised money (+25)"."""
    label = TRACTION_LABELS.get(reason["code"], reason["code"].replace("_", " "))
    return f"{label} ({reason['points']:+d})" if reason["points"] else label
