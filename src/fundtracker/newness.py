"""Newness score for a launch (PLAN.md 7.4). Pure: facts in, score and reasons out.

derive.py gathers the facts for each launch from the derived tables; this
module only weighs them. Tune the points in WEIGHTS and the thresholds below
without touching the logic. Each reason records the points it gave, so the
user can see why a launch ranks where it does.
"""

from fundtracker import classify

WEIGHTS = {
    "new_manager": 40,          # no key of the launch on an earlier filing, no over-cap entity
    "single_filing": 15,        # every fund in the launch has exactly one filing
    "sale_yet_to_occur": 15,    # any fund has not sold yet
    "nothing_raised": 10,       # every fund reports 0 sold and 0 investors
    "newly_formed": 10,         # incorporated this year or last, within five years
    "exemption_06c": 5,         # Rule 506(c): may market publicly, often emerging
    "min_investment_band": 5,   # minimum ticket in MIN_INVESTMENT_BAND
    "established_manager": -30,  # ESTABLISHED_FUNDS earlier funds, or an over-cap entity
    "cco_signer": -10,          # a Chief Compliance Officer signs for a manager with history
    "pe_vc_re_name": -20,       # a fund name looks like private equity, venture or real estate
    "platform": 0,              # label naming the over-cap entities (large manager or SPV platform)
}
MIN_INVESTMENT_BAND = (100_000, 1_000_000)  # inclusive
ESTABLISHED_FUNDS = 5
CCO_TITLE = "CHIEF COMPLIANCE OFFICER"


def score_launch(facts):
    """(score 0 to 100, reasons) for one launch.

    facts is a dict with:
      window_start       ISO date the launch's first fund was first filed
      key_seen_before    True if any non-over-cap key of the launch is on an earlier filing
      prior_funds        the manager's funds first filed before window_start
      signer_titles      titles on the launch's filings
      platforms          [(name, n_funds), ...] for the over-cap entity keys the launch
                         touches: an organisation named on more funds than the entity
                         cap, a large manager (AQR) or an SPV platform (Belltower)
      funds              list of dicts: name, n_filings, sale_yet_to_occur,
                         total_amount_sold_num, n_investors, year_of_inc_choice,
                         year_of_inc_value, exemptions (list), min_investment_num
    reasons is a list of {"code", "points", "detail"} in the order applied.
    """
    funds = facts["funds"]
    reasons = []

    def add(code, detail):
        reasons.append({"code": code, "points": WEIGHTS[code], "detail": detail})

    start = facts["window_start"]
    platforms = facts.get("platforms") or ()
    if not facts["key_seen_before"] and not platforms:
        add("new_manager", f"no linking key appears on a filing before {start}")
    if funds and all(f.get("n_filings") == 1 for f in funds):
        add("single_filing", "every fund has exactly one filing")
    if any(f.get("sale_yet_to_occur") for f in funds):
        add("sale_yet_to_occur", "first sale has not occurred")
    if funds and all(f.get("total_amount_sold_num") == 0 and f.get("n_investors") == 0
                     for f in funds):
        add("nothing_raised", "nothing sold and no investors")
    year = int(start[:4]) if start else None
    formed = [f["year_of_inc_value"] for f in funds
              if f.get("year_of_inc_choice") == "withinFiveYears"
              and year is not None and f.get("year_of_inc_value") in (year, year - 1)]
    if formed:
        add("newly_formed", f"incorporated in {max(formed)}")
    if any("06c" in (f.get("exemptions") or ()) for f in funds):
        add("exemption_06c", "Rule 506(c)")
    low, high = MIN_INVESTMENT_BAND
    tickets = [f["min_investment_num"] for f in funds
               if f.get("min_investment_num") is not None and low <= f["min_investment_num"] <= high]
    if tickets:
        add("min_investment_band", f"minimum investment {min(tickets):,}")

    prior = facts.get("prior_funds", 0)
    if prior >= ESTABLISHED_FUNDS:
        add("established_manager", f"manager has {prior} funds first filed before {start}")
    elif platforms:
        name, n = max(platforms, key=lambda p: (p[1], p[0]))
        add("established_manager", f"{name} is named on {n:,} funds")
    if prior >= 1 and any(CCO_TITLE in (t or "").upper() for t in facts.get("signer_titles", ())):
        add("cco_signer", "signed by a Chief Compliance Officer of a manager with earlier funds")
    matched = [f["name"] for f in funds if classify.matches_pe_vc_re(f.get("name"))]
    if matched:
        add("pe_vc_re_name", matched[0])
    if platforms:
        add("platform", ", ".join(f"{name} ({n:,} funds)" for name, n in platforms))

    score = max(0, min(100, sum(r["points"] for r in reasons)))
    return score, reasons
