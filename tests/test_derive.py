import dataclasses
import json
import re
from pathlib import Path

import pytest

from fundtracker import derive
from fundtracker.db import repo
from fundtracker.edgar.formd_xml import parse_formd

FORMD = Path(__file__).parent / "fixtures" / "formd"
# Filing dates from the fixture README (the XML carries none).
FILING_DATES = dict(re.findall(r"`(\d{10}-\d{2}-\d{6})\.xml`.*?filed (\d{4}-\d{2}-\d{2})",
                               (FORMD / "README.md").read_text(encoding="utf-8")))


def fixture(accession):
    return parse_formd((FORMD / f"{accession}.xml").read_bytes(), accession,
                       filing_date=FILING_DATES[accession])


def all_fixtures():
    return [fixture(a) for a in sorted(FILING_DATES)]


def variant(f, accession, cik=None, filing_date=None, name=None, issuer=None, offering=None,
            related_persons=None, signatures=None):
    """A copy of FormDFiling f under a new accession, with the given parts changed."""
    cik = cik or f.filing.cik
    issuers = tuple(dataclasses.replace(i, accession=accession, cik=cik if i.is_primary else i.cik,
                                        **({"entity_name": name} if name and i.is_primary else {}),
                                        **(issuer or {} if i.is_primary else {}))
                    for i in f.issuers)
    people = related_persons if related_persons is not None else f.related_persons
    sigs = signatures if signatures is not None else f.signatures
    return dataclasses.replace(
        f,
        filing=dataclasses.replace(f.filing, accession=accession, cik=cik,
                                   filing_date=filing_date or f.filing.filing_date),
        offering=dataclasses.replace(f.offering, accession=accession, **(offering or {})),
        issuers=issuers,
        related_persons=tuple(dataclasses.replace(p, accession=accession) for p in people),
        recipients=tuple(dataclasses.replace(r, accession=accession) for r in f.recipients),
        signatures=tuple(dataclasses.replace(s, accession=accession) for s in sigs),
    )


@pytest.fixture
def conn():
    c = repo.connect(":memory:")
    for f in all_fixtures():
        repo.upsert_filing(c, f)
    yield c
    c.close()


def keys(conn, kind, cik=None):
    sql = "SELECT key FROM link_key WHERE kind = ?" + (" AND cik = ?" if cik else "")
    return {r[0] for r in conn.execute(sql, (kind, cik) if cik else (kind,))}


# build_funds


def test_one_fund_per_cik_with_classification(conn):
    assert derive.build_funds(conn) == 12
    axiaris = repo.get_fund(conn, "2158817")
    assert axiaris["name"] == "Axiaris Capital Management Fund, LP"
    assert (axiaris["fund_type"], axiaris["is_hedge_candidate"], axiaris["candidate_reason"]) == (
        "Hedge Fund", 1, "hedge_fund")
    assert json.loads(axiaris["exemptions"]) == ["06c", "3C", "3C.1"]
    assert (axiaris["first_filing_date"], axiaris["latest_filing_date"], axiaris["n_filings"]) == (
        "2026-10-28", "2026-10-28", 1)
    assert axiaris["first_accession"] == axiaris["latest_accession"] == "0002158817-26-000001"
    assert axiaris["sale_yet_to_occur"] == 1
    assert (axiaris["year_of_inc_choice"], axiaris["year_of_inc_value"]) == ("withinFiveYears", 2026)
    assert axiaris["family_stem"] == "AXIARIS CAPITAL MANAGEMENT"

    four_hundred = repo.get_fund(conn, "1467029")
    assert json.loads(four_hundred["exemptions"]) == ["06b", "3C", "3C.7"]
    armada = repo.get_fund(conn, "2154220")
    assert (armada["fund_type"], armada["candidate_reason"]) == ("Other Investment Fund", "other_fund_3c")
    equera = repo.get_fund(conn, "2153113")
    assert (equera["is_hedge_candidate"], equera["candidate_reason"]) == (0, "")
    assert repo.get_fund(conn, "2134351")["is_hedge_candidate"] == 0  # Private Equity Fund


def test_classification_from_latest_terms_from_first(conn):
    first = fixture("0002158817-26-000001")
    later = variant(first, "0002158817-26-000002", filing_date="2026-11-20",
                    offering={"fund_type": "Other Investment Fund", "exemptions": ("06", "3C.7"),
                              "sale_yet_to_occur": False})
    repo.upsert_filing(conn, later)
    derive.build_funds(conn)
    fund = repo.get_fund(conn, "0002158817")
    assert fund["n_filings"] == 2
    assert (fund["first_accession"], fund["latest_accession"]) == (
        "0002158817-26-000001", "0002158817-26-000002")
    assert fund["latest_filing_date"] == "2026-11-20"
    assert (fund["fund_type"], fund["candidate_reason"]) == ("Other Investment Fund", "other_fund_3c")
    assert json.loads(fund["exemptions"]) == ["06b", "3C.7"]
    assert fund["sale_yet_to_occur"] == 1  # from the first filing


def test_build_funds_replaces_earlier_rows(conn):
    derive.build_funds(conn)
    conn.execute("DELETE FROM filing WHERE cik = '0002153113'")
    assert derive.build_funds(conn) == 11
    assert repo.get_fund(conn, "2153113") is None


# build_link_keys


def test_aqr_yields_entity_stem_and_sorted_tokens(conn):
    derive.build_link_keys(conn)
    assert keys(conn, "entity_stem", "0001635511") == {"MANAGEMENT AQR CAPITAL"}
    assert keys(conn, "entity_tokens", "0001635511") == {
        " ".join(sorted("AQR CAPITAL MANAGEMENT".split()))}
    row = conn.execute("SELECT first_date FROM link_key WHERE cik = '0001635511' "
                       "AND kind = 'entity_stem'").fetchone()
    assert row[0] == "2015-06-15"


def test_entity_keys_for_axiaris(conn):
    derive.build_link_keys(conn)
    assert keys(conn, "entity_stem", "0002158817") == {"AXIARIS CAPITAL MANAGEMENT"}
    assert keys(conn, "entity_tokens", "0002158817") == {"AXIARIS CAPITAL MANAGEMENT"}
    assert keys(conn, "signer", "0002158817") == {"ANDREW MURTLAND"}
    assert keys(conn, "phone", "0002158817") == {"2489219225"}
    assert len(keys(conn, "address", "0002158817")) == 1


def test_signer_equal_to_issuer_name_or_an_entity_is_skipped(conn):
    base = fixture("0002158817-26-000001")
    sig = base.signatures[0]
    self_signed = variant(base, "0009999999-26-000001", cik="0009999999", name="Jane Q Example",
                          signatures=(dataclasses.replace(sig, signer_norm="JANE Q EXAMPLE"),))
    entity_signed = variant(base, "0009999998-26-000001", cik="0009999998",
                            signatures=(dataclasses.replace(sig, signer_norm="EXAMPLE GP LLC"),))
    repo.upsert_filing(conn, self_signed)
    repo.upsert_filing(conn, entity_signed)
    derive.build_link_keys(conn)
    assert keys(conn, "signer", "0009999999") == set()
    assert keys(conn, "signer", "0009999998") == set()


def test_stoplist_short_keys_and_short_phones_are_skipped(conn):
    base = fixture("0002158817-26-000001")
    person = base.related_persons[0]
    generic = variant(
        base, "0009999997-26-000001", cik="0009999997", issuer={"phone": "555-12"},
        related_persons=(dataclasses.replace(person, name_norm="GENERAL PARTNER LLC", is_entity=True),
                         dataclasses.replace(person, seq=2, name_norm="ABC LLC", is_entity=True)))
    repo.upsert_filing(conn, generic)
    derive.build_link_keys(conn)
    assert keys(conn, "entity_stem", "0009999997") == set()
    assert keys(conn, "entity_tokens", "0009999997") == set()
    assert keys(conn, "phone", "0009999997") == set()


@pytest.mark.parametrize("n_ciks, kept", [(derive.SERVICE_PROVIDER_CAP, True),
                                          (derive.SERVICE_PROVIDER_CAP + 1, False)])
def test_phone_and_address_shared_beyond_the_cap_are_dropped(n_ciks, kept):
    conn = repo.connect(":memory:")
    base = fixture("0002158817-26-000001")
    for n in range(n_ciks):
        cik = f"{8000000000 + n:010d}"
        repo.upsert_filing(conn, variant(
            base, f"{cik}-26-000001", cik=cik, name=f"Unrelated Fund {n} LP",
            issuer={"phone": "(302) 555-0100", "street1": "1 Agent Plaza", "zip": "19801"},
            related_persons=(), signatures=()))
    result = derive.build_link_keys(conn)
    assert bool(keys(conn, "phone")) is kept
    assert bool(keys(conn, "address")) is kept
    assert result["dropped"] == ({"phone": 0, "address": 0} if kept else {"phone": 1, "address": 1})


def test_entity_keys_are_never_capped():
    conn = repo.connect(":memory:")
    base = fixture("0002158817-26-000001")
    for n in range(derive.SERVICE_PROVIDER_CAP + 5):
        cik = f"{8000000000 + n:010d}"
        repo.upsert_filing(conn, variant(base, f"{cik}-26-000001", cik=cik, signatures=(),
                                         issuer={"phone": f"212555{n:04d}"}))
    derive.build_link_keys(conn)
    n = conn.execute("SELECT count(*) FROM link_key WHERE kind = 'entity_stem' "
                     "AND key = 'AXIARIS CAPITAL MANAGEMENT'").fetchone()[0]
    assert n == derive.SERVICE_PROVIDER_CAP + 5


def test_address_needs_street_and_zip(conn):
    base = fixture("0002158817-26-000001")
    repo.upsert_filing(conn, variant(base, "0009999996-26-000001", cik="0009999996",
                                     issuer={"street1": None}))
    derive.build_link_keys(conn)
    assert keys(conn, "address", "0009999996") == set()
