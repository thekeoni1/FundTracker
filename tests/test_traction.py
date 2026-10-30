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


@pytest.mark.parametrize("sold, band_points", [
    (999_999, None), (1_000_000, 5), (4_999_999, 5), (5_000_000, 15), (99_999_999, 15),
    (100_000_000, 10), (499_999_999, 10), (500_000_000, -10), (1_999_999_999, -10),
    (2_000_000_000, -30), (8_676_080_000, -30),
])
def test_raised_band_edges(sold, band_points):
    score, reasons = score_manager(facts(total_sold=sold))
    band = [r for r in reasons if r["code"] == "raised_band"]
    assert [r["points"] for r in band] == ([] if band_points is None else [band_points])
    assert score == max(0, 25 + (band_points or 0))


def test_billions_raised_rank_below_the_sweet_spot():
    institution = facts(n_funds=6, total_sold=8_676_080_000, total_investors=334, has_amendment=1,
                        latest_filing_date="2026-10-13")
    shop = dict(institution, n_funds=2, total_sold=60_000_000, total_investors=33)
    assert score_manager(institution)[0] == 25 - 30 + 15 + 15 + 15 + 10 == 50
    assert score_manager(shop)[0] == 25 + 15 + 15 + 15 + 15 + 10 == 95
    assert score_manager(dict(shop, total_sold=700_000_000))[0] == 25 - 10 + 15 + 15 + 15 + 10 == 70


def test_investor_tiers_stack():
    assert codes(score_manager(facts(total_investors=10))[1]) == ["raised", "investors", "investors_10"]
    assert codes(score_manager(facts(total_investors=9))[1]) == ["raised", "investors"]


def test_prior_firm_is_a_zero_point_label():
    score, reasons = score_manager(facts(prior_firms='["Ares Management LLC", "KKR & Co"]'))
    assert reasons[-1] == {"code": "prior_firm", "points": 0, "detail": "Ares Management LLC, KKR & Co"}
    assert score == 25
    assert "prior_firm" not in codes(score_manager(facts(prior_firms="[]"))[1])


def test_recent_activity_edge_and_hedge_label_only_in_all_mode():
    assert "recent_activity" not in codes(score_manager(facts(latest_filing_date="2026-04-29"))[1])
    assert "hedge" not in codes(score_manager(facts(type="all", fund_types='["Private Equity Fund"]'))[1])


def test_penalties():
    score, reasons = score_manager(facts(total_sold=0, total_investors=0))
    assert codes(reasons) == ["nothing_raised"] and score == 0
    score, reasons = score_manager(facts(n_funds=10, total_sold=2_000_000))
    assert codes(reasons) == ["raised", "raised_band", "second_vehicle", "large"] and score == 25 + 5 + 15 - 20


def test_most_and_clamped():
    every = facts(type="all", n_funds=3, total_sold=80_000_000, total_investors=40, has_amendment=1,
                  latest_filing_date="2026-10-01")
    score, reasons = score_manager(every)
    assert sum(r["points"] for r in reasons) == 95 and score == 95  # the most the table gives
    assert score_manager(facts(total_sold=0, total_investors=0, n_funds=12))[0] == 0


def test_eleven_month_old_shop_with_traction_scores_at_least_85():
    shop = facts(n_funds=2, total_sold=18_000_000, total_investors=12, has_amendment=1,
                 latest_filing_date="2026-09-15")
    assert age_months("2025-11-20", TODAY) == 11
    score, reasons = score_manager(shop)
    assert score >= 85
    assert codes(reasons) == ["raised", "raised_band", "investors", "investors_10", "amended",
                              "second_vehicle", "recent_activity"]
    assert score == 95


def test_months_and_labels():
    assert months_before(date(2026, 8, 31), 6) == date(2026, 2, 28)
    assert months_before(TODAY, 18) == date(2025, 4, 30)
    assert age_months("2025-04-30", TODAY) == 18 and age_months("2025-05-01", TODAY) == 17
    assert set(TRACTION_LABELS) == set(TRACTION_WEIGHTS) | {"raised_band"}  # band points: RAISED_BANDS
    assert reason_phrase({"code": "raised", "points": 25, "detail": ""}) == "raised money (+25)"
    assert reason_phrase({"code": "hedge", "points": 0, "detail": ""}) == "runs a hedge fund"
