import dataclasses
import json
import sys
from pathlib import Path

import pytest

from fundtracker import derive, newness
from fundtracker.db import repo
from fundtracker.newness import WEIGHTS, score_launch

sys.path.insert(0, str(Path(__file__).parent))
import test_derive  # noqa: E402
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
            "signer_titles": [], "platforms": [], "funds": list(funds) or [dict(QUIET_FUND)]}
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


def test_platform_reason_has_no_points_and_names_each_organisation():
    platforms = [("Belltower Fund Group, Ltd.", 14_000), ("Sydecar LLC", 3_100)]
    score, reasons = score_launch(facts(platforms=platforms))
    assert reasons[-1] == {"code": "platform", "points": 0,
                           "detail": "Belltower Fund Group, Ltd. (14,000 funds), Sydecar LLC (3,100 funds)"}
    assert score == 0


NEW_VEHICLE = dict(n_filings=1, year_of_inc_choice="withinFiveYears", year_of_inc_value=2026)


@pytest.mark.parametrize("platform", [("AQR Capital Management, LLC", 615),
                                      ("Belltower Fund Group, Ltd.", 14_000)])
def test_an_over_cap_entity_means_established_not_new(platform):
    score, reasons = score_launch(facts(fund(**NEW_VEHICLE), key_seen_before=False,
                                        platforms=[platform]))
    assert codes(reasons) == ["single_filing", "newly_formed", "established_manager", "platform"]
    assert reasons[2]["detail"] == f"{platform[0]} is named on {platform[1]:,} funds"
    assert 0 <= score <= 10


def test_established_by_fund_count_is_not_applied_twice():
    _score, reasons = score_launch(facts(prior_funds=7, platforms=[("AQR Capital Management, LLC", 615)]))
    assert codes(reasons) == ["established_manager", "platform"]
    assert reasons[0]["detail"].startswith("manager has 7 funds")


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


def over_cap_world(conn, shared):
    """ENTITY_KEY_CAP + 1 older funds sharing shared (an entity related person, or an
    issuer address when shared is a dict), then a fund filed on 2026-10-28 with it too;
    return that launch's score and reasons."""
    base = test_derive.fixture("0002158817-26-000001")
    person = base.related_persons[0]
    n_old = derive.ENTITY_KEY_CAP + 1 if not isinstance(shared, dict) else derive.SERVICE_PROVIDER_CAP + 1
    for n in range(n_old + 1):
        day = "2026-10-28" if n == n_old else "2024-01-02"
        f = test_derive.lone(base, n, f"Vehicle {n} LP", day)
        if isinstance(shared, dict):
            f = dataclasses.replace(f, issuers=tuple(dataclasses.replace(i, **shared) for i in f.issuers))
        else:
            f = dataclasses.replace(f, related_persons=(
                dataclasses.replace(person, accession=f.accession, first="N/A", middle=None, last=shared,
                                    name_norm=shared.upper().replace(",", "").replace(".", ""),
                                    is_entity=True),))
        if n == n_old and not isinstance(shared, dict):
            # Only single_filing and newly_formed (the Axiaris base is incorporated in 2026).
            f = dataclasses.replace(f, offering=dataclasses.replace(
                f.offering, sale_yet_to_occur=False, total_amount_sold_num=1_000_000,
                n_investors=3, exemptions=("06b", "3C.1"), min_investment_num=None))
        repo.upsert_filing(conn, f)
    derive.run(conn)
    cik = f"{7000000000 + n_old:010d}"
    row = conn.execute("SELECT * FROM launch WHERE anchor_cik = ?", (cik,)).fetchone()
    return row["newness_score"], json.loads(row["newness_reasons"])


@pytest.mark.parametrize("entity", ["AQR Capital Management, LLC", "Belltower Fund Group, Ltd."])
def test_derive_scores_a_launch_on_an_over_cap_entity_as_established(entity):
    score, reasons = over_cap_world(repo.connect(":memory:"), entity)
    assert "new_manager" not in codes(reasons)
    assert {"established_manager", "platform"} <= set(codes(reasons))
    assert reasons[-1]["detail"] == f"{entity} ({derive.ENTITY_KEY_CAP + 2:,} funds)"
    assert 0 <= score <= 10


def test_derive_keeps_new_manager_when_only_an_agent_address_is_over_cap():
    score, reasons = over_cap_world(repo.connect(":memory:"),
                                    {"street1": "251 Little Falls Drive", "zip": "19808"})
    assert codes(reasons)[0] == "new_manager"
    assert "platform" not in codes(reasons) and "established_manager" not in codes(reasons)
    assert score >= 70
