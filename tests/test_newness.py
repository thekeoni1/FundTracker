import json
import sys
from pathlib import Path

import pytest

from fundtracker import derive, newness
from fundtracker.db import repo
from fundtracker.newness import WEIGHTS, score_launch

sys.path.insert(0, str(Path(__file__).parent))
from test_derive import all_fixtures  # noqa: E402

# A fund that triggers nothing on its own.
QUIET_FUND = {
    "name": "Quiet Fund LP", "n_filings": 2, "sale_yet_to_occur": 0,
    "total_amount_sold_num": 5_000_000, "n_investors": 12,
    "year_of_inc_choice": "overFiveYears", "year_of_inc_value": None,
    "exemptions": ["06b", "3C.1"], "min_investment_num": 5_000_000,
}


def facts(*funds, **overrides):
    base = {"window_start": "2026-10-28", "key_seen_before": True, "prior_funds": 0,
            "signer_titles": [], "platform_keys": [], "funds": list(funds) or [dict(QUIET_FUND)]}
    base.update(overrides)
    return base


def fund(**changes):
    return dict(QUIET_FUND, **changes)


def codes(reasons):
    return [r["code"] for r in reasons]


def test_quiet_launch_scores_zero():
    assert score_launch(facts()) == (0, [])


@pytest.mark.parametrize("code, launch", [
    ("new_manager", facts(key_seen_before=False)),
    ("single_filing", facts(fund(n_filings=1), fund(n_filings=1))),
    ("sale_yet_to_occur", facts(fund(), fund(sale_yet_to_occur=1))),
    ("nothing_raised", facts(fund(total_amount_sold_num=0, n_investors=0))),
    ("newly_formed", facts(fund(year_of_inc_choice="withinFiveYears", year_of_inc_value=2025))),
    ("exemption_06c", facts(fund(), fund(exemptions=["06c", "3C.1"]))),
    ("min_investment_band", facts(fund(min_investment_num=100_000))),
])
def test_each_signal_gives_exactly_its_points(code, launch):
    score, reasons = score_launch(launch)
    assert codes(reasons) == [code]
    assert score == WEIGHTS[code] == reasons[0]["points"]
    assert reasons[0]["detail"]


@pytest.mark.parametrize("launch", [
    facts(fund(n_filings=1), fund(n_filings=2)),                       # not every fund
    facts(fund(total_amount_sold_num=0, n_investors=3)),               # investors present
    facts(fund(total_amount_sold_num=0, n_investors=0), fund()),       # not every fund
    facts(fund(total_amount_sold_num=None, n_investors=None)),         # unknown is not zero
    facts(fund(year_of_inc_choice="withinFiveYears", year_of_inc_value=2023)),  # too early
    facts(fund(year_of_inc_choice="overFiveYears", year_of_inc_value=2026)),
    facts(fund(min_investment_num=99_999)),
    facts(fund(min_investment_num=1_000_001)),
])
def test_near_misses_give_nothing(launch):
    assert score_launch(launch) == (0, [])


def test_min_investment_band_is_inclusive_at_the_top():
    assert codes(score_launch(facts(fund(min_investment_num=1_000_000)))[1]) == ["min_investment_band"]


def all_positive():
    return facts(fund(n_filings=1, sale_yet_to_occur=1, total_amount_sold_num=0, n_investors=0,
                      year_of_inc_choice="withinFiveYears", year_of_inc_value=2026,
                      exemptions=["06c"], min_investment_num=250_000),
                 key_seen_before=False)


def test_established_manager_penalty():
    score, reasons = score_launch(facts(fund(sale_yet_to_occur=1), prior_funds=5))
    assert codes(reasons) == ["sale_yet_to_occur", "established_manager"]
    assert score == 0  # 15 - 30, clamped
    assert codes(score_launch(facts(fund(sale_yet_to_occur=1), prior_funds=4))[1]) == [
        "sale_yet_to_occur"]


def test_cco_penalty_needs_an_earlier_fund():
    launch = all_positive()
    launch["signer_titles"] = ["Chief Compliance Officer"]
    assert "cco_signer" not in codes(score_launch(launch)[1])
    launch["prior_funds"] = 1
    score, reasons = score_launch(launch)
    assert codes(reasons)[-1] == "cco_signer"
    assert score == 90  # 100 - 10


def test_pe_vc_re_name_penalty():
    launch = all_positive()
    launch["funds"].append(dict(launch["funds"][0], name="Oak Real Estate Partners LP"))
    score, reasons = score_launch(launch)
    assert reasons[-1] == {"code": "pe_vc_re_name", "points": -20,
                           "detail": "Oak Real Estate Partners LP"}
    assert score == 80  # 100 - 20


def test_platform_reason_has_no_points():
    score, reasons = score_launch(facts(platform_keys=["BELLTOWER FUND GROUP", "SYDECAR"]))
    assert (score, reasons) == (0, [{"code": "platform", "points": 0,
                                     "detail": "BELLTOWER FUND GROUP, SYDECAR"}])


def test_clamped_to_0_and_100():
    score, reasons = score_launch(all_positive())
    assert sum(r["points"] for r in reasons) == 100 and score == 100
    worst = facts(fund(name="Oak Ventures LP"), prior_funds=9, signer_titles=["CHIEF COMPLIANCE OFFICER"])
    score, reasons = score_launch(worst)
    assert sum(r["points"] for r in reasons) == -60 and score == 0


def test_weights_are_one_dict_with_the_penalties_negative():
    assert {k for k, v in WEIGHTS.items() if v < 0} == {
        "established_manager", "cco_signer", "pe_vc_re_name"}
    assert sum(v for v in WEIGHTS.values() if v > 0) == 100


def test_axiaris_like_launch_scores_at_least_85():
    axiaris = fund(name="Axiaris Capital Management Fund, LP", n_filings=1, sale_yet_to_occur=1,
                   total_amount_sold_num=0, n_investors=0, year_of_inc_choice="withinFiveYears",
                   year_of_inc_value=2026, exemptions=["06c", "3C", "3C.1"],
                   min_investment_num=None)
    score, reasons = score_launch(facts(axiaris, key_seen_before=False))
    assert score >= 85
    assert codes(reasons)[:2] == ["new_manager", "single_filing"]


def test_derive_scores_the_real_axiaris_fixture():
    conn = repo.connect(":memory:")
    for f in all_fixtures():
        repo.upsert_filing(conn, f)
    derive.run(conn)
    row = conn.execute("SELECT * FROM launch WHERE anchor_cik = '0002158817'").fetchone()
    reasons = json.loads(row["newness_reasons"])
    assert row["newness_score"] >= 85
    assert [r["code"] for r in reasons][:3] == ["new_manager", "single_filing", "sale_yet_to_occur"]
    assert all(r["newness_score"] is not None for r in conn.execute("SELECT newness_score FROM launch"))
