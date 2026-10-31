"""derive.build_manager_advisers on synthetic funds and the trimmed October 2026 adviser files."""

import dataclasses
import json
import sys
from pathlib import Path

import pytest

from fundtracker import derive
from fundtracker.db import repo
from fundtracker.edgar import adv
from fundtracker.edgar.adv import AdviserRow

sys.path.insert(0, str(Path(__file__).parent))
from test_derive import fixture, person, variant  # noqa: E402

ADV = Path(__file__).parent / "fixtures" / "adv"
BASE = fixture("0002158817-26-000001")


def fund(n, name, phone=None, street1=None, zip_code="10001", entity=None):
    """A pooled hedge fund under its own CIK and signer, with the given issuer phone and
    address (default ones no adviser has) and optionally one entity related person."""
    cik = f"{7100000000 + n:010d}"
    sig = dataclasses.replace(BASE.signatures[0], signer_norm=f"SIGNER NUMBER {n}")
    people = (person(BASE, 1, "N/A", entity, entity=True),) if entity else ()
    return variant(BASE, f"{cik}-26-000001", cik=cik, name=name, filing_date="2025-09-01",
                   issuer={"phone": phone or f"999444{n:04d}", "street1": street1 or f"{n} Quiet Lane",
                           "zip": zip_code},
                   related_persons=people, signatures=(sig,))


def adviser(crd, name, street1=None, postal_code=None, phone=None, hedge=False):
    return AdviserRow(crd=crd, kind="era", name=name, legal_name=name, street1=street1,
                      postal_code=postal_code, phone=phone, any_hedge_funds=hedge)


SHARED = "500 Shared Plaza"
CROWDED = "600 Crowded Plaza"
EXTRA = [
    # Three advisers in one building, one of them with hedge funds.
    adviser("900001", "PLAZA ONE ADVISORS", "500 SHARED PLAZA", "10022"),
    adviser("900002", "PLAZA TWO PARTNERS", "500 SHARED PLAZA", "10022-4611", hedge=True),
    adviser("900003", "PLAZA THREE CAPITAL", "500 SHARED PLAZA", "10022"),
    # Three in another, none with hedge funds.
    *(adviser(f"90001{i}", f"CROWD {i} ADVISERS", "600 CROWDED PLAZA", "10017") for i in range(3)),
    # Twenty-one sharing a service provider's phone.
    *(adviser(f"9001{i:02d}", f"SERVICED {i} ADVISERS", phone="212-555-0100") for i in range(21)),
    # One alone at its address.
    adviser("900200", "LONE OFFICE MANAGEMENT", "77 SINGLE STREET", "60601"),
    # Alone at an address, sharing a name token with the manager there (Orso Partners).
    adviser("900201", "ORSINO PARTNERS, LP", "88 HARBOR ROAD", "02110"),
    # A filing service's phone, no hedge funds.
    adviser("900202", "DOCS BOX FILINGS", phone="303-555-0199"),
    # A phone of an adviser with hedge funds and an unrelated name.
    adviser("900203", "HARBOR HEDGE ADVISERS", phone="303-555-0177", hedge=True),
    # Alone at an address, sharing only a suffix and a generic token with the manager there.
    adviser("900204", "BRIGHT FUND LLC", "99 MEADOW LANE", "73301"),
    # Alone at an address, sharing only CAPITAL MANAGEMENT with the manager there (Benchstone).
    adviser("900205", "SHALESTONE CAPITAL MANAGEMENT LP", "12 FERRY STREET", "94105"),
    # Alone at an address, its name written with an accent (Arène).
    adviser("900206", "ARÈNE CAPITAL MANAGEMENT LP", "14 RUE STREET", "10013"),
]


@pytest.fixture
def conn():
    c = repo.connect(":memory:")
    funds = [
        fund(1, "Boxer Capital Fund LP", entity="Boxer Capital Management, LLC"),
        fund(2, "Northfork Fund LP", phone="(646) 450-5521"),
        fund(3, "Plaza Fund LP", street1=SHARED, zip_code="10022"),
        fund(4, "Crowd Fund LP", street1=CROWDED, zip_code="10017"),
        fund(5, "Serviced Fund LP", phone="+1 212 555 0100"),
        fund(6, "Single Fund LP", street1="77 Single Street", zip_code="60601"),
        fund(7, "Unknown Fund LP"),
        fund(8, "Orsino Partners Fund LP", street1="88 Harbor Road", zip_code="02110",
             entity="Orsino Partners Fund GP, LLC"),
        fund(9, "Tundra Fund LP", phone="303 555 0199"),
        fund(10, "Glacier Fund LP", phone="303.555.0177"),
        fund(11, "Polar Fund LLC", street1="99 Meadow Lane", zip_code="73301", entity="Polar Fund LLC"),
        fund(12, "Squarestreet Fund LP", street1="12 Ferry Street", zip_code="94105",
             entity="Squarestreet Capital Management, LLC"),
        fund(13, "Arene Fund LP", street1="14 Rue Street", zip_code="10013",
             entity="Arene Capital Partners GP LLC"),
    ]
    for f in funds:
        repo.upsert_filing(c, f)
    with c:
        repo.replace_advisers(c, "ria", "2026-10",
                              adv.read_adv_csv((ADV / "ia100226_trimmed.zip").read_bytes(), "ria"))
        exempt = list(adv.read_adv_csv((ADV / "ia100226-exempt_trimmed.zip").read_bytes(), "era"))
        repo.replace_advisers(c, "era", "2026-10", exempt + EXTRA)
    derive.run(c)
    yield c
    c.close()


def link(conn, n):
    row = conn.execute("SELECT ma.* FROM manager_adviser ma JOIN fund_manager fm USING (manager_id) "
                       "WHERE fm.cik = ?", (f"{7100000000 + n:010d}",)).fetchone()
    if row is None:
        return None
    return (row["crd"], row["confidence"], json.loads(row["match_kinds"]),
            json.loads(row["evidence"])["candidates"])


def test_boxer_links_high_by_its_entity_name(conn):
    assert link(conn, 1) == ("330106", "high", ["name"], ["330106"])


def test_malt_links_high_by_phone(conn):
    assert link(conn, 2) == ("337855", "high", ["phone"], ["337855"])


def test_a_shared_building_links_medium_through_the_one_adviser_with_hedge_funds(conn):
    assert link(conn, 3) == ("900002", "medium", ["address", "hedge_tiebreak"],
                             ["900001", "900002", "900003"])


def test_three_advisers_without_hedge_funds_give_candidates_only(conn):
    assert link(conn, 4) == (None, "none", [], ["900010", "900011", "900012"])


def test_a_phone_shared_by_21_advisers_is_ignored(conn):
    assert link(conn, 5) is None


def test_one_adviser_at_an_address_with_a_shared_name_token_links_medium(conn):
    assert link(conn, 8) == ("900201", "medium", ["address", "name_token"], ["900201"])


def test_one_adviser_at_an_address_with_no_shared_name_token_gives_candidates_only(conn):
    assert link(conn, 6) == (None, "none", [], ["900200"])


def test_a_phone_of_an_adviser_without_hedge_funds_and_no_shared_name_token_gives_candidates_only(conn):
    assert link(conn, 9) == (None, "none", [], ["900202"])


def test_a_phone_of_an_adviser_with_hedge_funds_links_high_without_a_shared_name_token(conn):
    assert link(conn, 10) == ("900203", "high", ["phone"], ["900203"])


def test_suffix_and_generic_tokens_are_not_shared_name_tokens(conn):
    assert link(conn, 11) == (None, "none", [], ["900204"])


def test_sharing_only_capital_management_gives_candidates_only(conn):
    assert link(conn, 12) == (None, "none", [], ["900205"])


def test_an_accented_adviser_name_shares_its_folded_token(conn):
    assert link(conn, 13) == ("900206", "medium", ["address", "name_token"], ["900206"])


def test_a_manager_matching_nothing_gets_no_row(conn):
    assert link(conn, 7) is None


def test_evidence_names_the_matching_keys(conn):
    row = conn.execute("SELECT evidence FROM manager_adviser WHERE crd = '337855'").fetchone()
    assert json.loads(row[0])["keys"] == ["phone:6464505521"]


def test_derive_twice_gives_identical_tables(conn):
    tables = ("manager_adviser", "manager", "link_key", "fund_manager")

    def dump():
        return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")] for t in tables}

    before = dump()
    counts = derive.run(conn)
    assert dump() == before
    assert counts["manager_adviser"] == {"high": 3, "medium": 3, "none": 5}


@pytest.mark.parametrize("name, stem", [
    ("BOXER CAPITAL MANAGEMENT, LLC", "BOXER CAPITAL MANAGEMENT"),
    ("Malt Partners Management L.L.C.", "MALT PARTNERS MANAGEMENT"),
    ("ACME ADVISORS CO., LTD.", "ACME ADVISORS"),
    ("THE FUND, LLC", None),
    ("CAPITAL LLP", None),
])
def test_adviser_stem(name, stem):
    assert derive.adviser_stem(name) == stem


@pytest.mark.parametrize("name, tokens", [
    ("Orsino Partners Fund GP, LLC", {"ORSINO"}),
    ("BRIGHT CAPITAL FUND LLC", {"BRIGHT"}),
    ("RRJ MANAGEMENT LTD", set()),
    ("ARÈNE CAPITAL MANAGEMENT LP", {"ARENE"}),
    ("THE FUND, LLC", set()),
    (None, set()),
])
def test_adviser_tokens(name, tokens):
    assert derive.adviser_tokens(name) == tokens
