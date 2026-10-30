import pytest

from fundtracker import classify
from fundtracker.classify import is_hedge_candidate, matches_pe_vc_re, normalize_exemptions

POOLED = "Pooled Investment Fund"


@pytest.mark.parametrize("fund_type, expected", [
    ("Hedge Fund", (True, "hedge_fund")),
    ("Private Equity Fund", (False, "")),
    ("Venture Capital Fund", (False, "")),
    ("Other Investment Fund", (True, "other_fund_3c")),
])
def test_the_four_fund_types(fund_type, expected):
    assert is_hedge_candidate(POOLED, fund_type, ["06b", "3C.1"], "Example Fund LP") == expected


def test_not_a_pooled_fund_is_never_a_candidate():
    assert is_hedge_candidate("Other Technology", "Hedge Fund", ["3C.1"], "X Fund LP") == (False, "")
    assert is_hedge_candidate(None, None, [], None) == (False, "")


def test_hedge_fund_needs_no_company_act_exemption_and_ignores_the_name():
    assert is_hedge_candidate(POOLED, "Hedge Fund", ["06b"], "X Real Estate Fund LP") == (
        True, "hedge_fund")


def test_06_is_read_as_06b():
    assert normalize_exemptions(["06", "3C.7"]) == ["06b", "3C.7"]
    assert normalize_exemptions(["06", "06b"]) == ["06b"]
    assert normalize_exemptions(["06c", "3C", "3C.1"]) == ["06c", "3C", "3C.1"]
    assert normalize_exemptions(None) == []


def test_other_fund_with_3c7_is_accepted():
    assert is_hedge_candidate(POOLED, "Other Investment Fund", ["06", "3C.7"],
                              "Northwind Multi-Strategy Fund, L.P.") == (True, "other_fund_3c")


def test_other_fund_excluded_by_name():
    assert is_hedge_candidate(POOLED, "Other Investment Fund", ["06b", "3C.7"],
                              "Northwind Real Estate Partners II, L.P.") == (False, "")


def test_other_fund_without_3c1_or_3c7_is_not_a_candidate():
    assert is_hedge_candidate(POOLED, "Other Investment Fund", ["06b", "3C"],
                              "Northwind Fund LP") == (False, "")


# One name per pattern that should hit, and a near miss that should not.
PATTERN_CASES = {
    "real_estate": ("Oak Real-Estate Fund LP", "Oak Realtime Fund LP"),
    "reit": ("Oak REIT Holdings LLC", "Oak Reiter Fund LP"),
    "spv": ("Oak SPV I LLC", "Oak SPVX Fund LP"),
    "co_invest": ("Oak Co-Invest Partners LP", "Oak Coinbase Strategies LP"),
    "credit_opportunities": ("Oak Credit Opportunities Fund LP", "Oak Credit Fund LP"),
    "private_equity": ("Oak Private Equity Fund III LP", "Oak Private Credit Fund LP"),
    "venture": ("Oak Ventures II LP", "Oak Adventure Fund LP"),
    "buyout": ("Oak Buy-Out Fund LP", "Oak Buyer Fund LP"),
    "growth_equity": ("Oak Growth Equity Fund LP", "Oak Growth Fund LP"),
    "infrastructure": ("Oak Infrastructure Partners LP", "Oak Infra Fund LP"),
    "secondaries": ("Oak Secondaries Fund LP", "Oak Second Street Fund LP"),
    "bdc": ("Oak BDC Inc.", "Oak BDCX Fund LP"),
    "clo": ("Oak CLO 2026-1 Ltd", "Oak Clover Fund LP"),
    "pe_vc_feeder": ("Oak PE Feeder LP", "Oak Pear Fund LP"),
}


def test_every_pattern_has_a_case():
    assert set(PATTERN_CASES) == set(classify.EXCLUSION_PATTERNS)


@pytest.mark.parametrize("key", sorted(PATTERN_CASES))
def test_exclusion_pattern_hit_and_near_miss(key):
    hit, miss = PATTERN_CASES[key]
    assert key in classify.exclusion_matches(hit)
    assert classify.exclusion_matches(miss) == []
    assert is_hedge_candidate(POOLED, "Other Investment Fund", ["3C.1"], hit) == (False, "")
    assert is_hedge_candidate(POOLED, "Other Investment Fund", ["3C.1"], miss) == (
        True, "other_fund_3c")


def test_matches_pe_vc_re():
    assert matches_pe_vc_re("Oak Private Equity Fund III LP")
    assert matches_pe_vc_re("Oak Ventures II LP")
    assert matches_pe_vc_re("Oak Real Estate Fund LP")
    assert not matches_pe_vc_re("Oak Credit Opportunities Fund LP")
    assert not matches_pe_vc_re("Axiaris Capital Management Fund, LP")
    assert not matches_pe_vc_re(None)
