from datetime import date

import pytest

from fundtracker.traction import (
    TRACTION_LABELS,
    TRACTION_WEIGHTS,
    age_months,
    months_before,
    reason_phrase,
    score_manager,
)

TODAY = date(2026, 10, 30)
# One fund, a sale but nothing else: scores only "raised" until changed.
BASE = {"today": TODAY, "type": "hedge", "n_funds": 1, "total_sold": 1, "total_investors": 0,
        "has_amendment": 0, "latest_filing_date": "2025-11-03", "fund_types": '["Hedge Fund"]'}


def facts(**changes):
    return dict(BASE, **changes)


def codes(reasons):
    return [r["code"] for r in reasons]


def test_base_scores_only_raised():
    assert score_manager(facts()) == (25, [{"code": "raised", "points": 25, "detail": "1 sold across funds"}])


@pytest.mark.parametrize("code, changes", [
    ("raised_10m", {"total_sold": 10_000_000}),
    ("investors", {"total_investors": 1}),
    ("amended", {"has_amendment": 1}),
    ("second_vehicle", {"n_funds": 2}),
    ("recent_activity", {"latest_filing_date": "2026-04-30"}),
    ("hedge", {"type": "all"}),
])
def test_each_signal_adds_its_code(code, changes):
    score, reasons = score_manager(facts(**changes))
    assert codes(reasons) == ["raised", code]
    assert score == 25 + TRACTION_WEIGHTS[code]


def test_tiers_stack():
    assert codes(score_manager(facts(total_sold=50_000_000))[1]) == ["raised", "raised_10m", "raised_50m"]
    assert codes(score_manager(facts(total_investors=10))[1]) == ["raised", "investors", "investors_10"]
    assert codes(score_manager(facts(total_sold=9_999_999, total_investors=9))[1]) == ["raised", "investors"]


def test_recent_activity_edge_and_hedge_label_only_in_all_mode():
    assert "recent_activity" not in codes(score_manager(facts(latest_filing_date="2026-04-29"))[1])
    assert "hedge" not in codes(score_manager(facts(type="all", fund_types='["Private Equity Fund"]'))[1])


def test_penalties():
    score, reasons = score_manager(facts(total_sold=0, total_investors=0))
    assert codes(reasons) == ["nothing_raised"] and score == 0
    score, reasons = score_manager(facts(n_funds=10, total_sold=2_000_000))
    assert codes(reasons) == ["raised", "second_vehicle", "large"] and score == 25 + 15 - 20


def test_clamped():
    every = facts(type="all", n_funds=3, total_sold=80_000_000, total_investors=40, has_amendment=1,
                  latest_filing_date="2026-10-01")
    score, reasons = score_manager(every)
    assert sum(r["points"] for r in reasons) == 100 and score == 100
    assert score_manager(facts(total_sold=0, total_investors=0, n_funds=12))[0] == 0


def test_eleven_month_old_shop_with_traction_scores_at_least_85():
    shop = facts(n_funds=2, total_sold=18_000_000, total_investors=12, has_amendment=1,
                 latest_filing_date="2026-09-15")
    assert age_months("2025-11-20", TODAY) == 11
    score, reasons = score_manager(shop)
    assert score >= 85
    assert codes(reasons) == ["raised", "raised_10m", "investors", "investors_10", "amended",
                              "second_vehicle", "recent_activity"]


def test_months_and_labels():
    assert months_before(date(2026, 8, 31), 6) == date(2026, 2, 28)
    assert months_before(TODAY, 18) == date(2025, 4, 30)
    assert age_months("2025-04-30", TODAY) == 18 and age_months("2025-05-01", TODAY) == 17
    assert set(TRACTION_LABELS) == set(TRACTION_WEIGHTS)
    assert reason_phrase({"code": "raised", "points": 25, "detail": ""}) == "raised money (+25)"
    assert reason_phrase({"code": "hedge", "points": 0, "detail": ""}) == "runs a hedge fund"
