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


def flagged(conn, kind):
    return {r[0]: r[1] for r in conn.execute(
        "SELECT key, max(over_cap) FROM link_key WHERE kind = ? GROUP BY key", (kind,))}


@pytest.mark.parametrize("n_ciks, over", [(derive.SERVICE_PROVIDER_CAP, 0),
                                          (derive.SERVICE_PROVIDER_CAP + 1, 1)])
def test_phone_and_address_shared_beyond_the_cap_are_flagged_not_dropped(n_ciks, over):
    conn = repo.connect(":memory:")
    base = fixture("0002158817-26-000001")
    for n in range(n_ciks):
        cik = f"{8000000000 + n:010d}"
        repo.upsert_filing(conn, variant(
            base, f"{cik}-26-000001", cik=cik, name=f"Unrelated Fund {n} LP",
            issuer={"phone": "(302) 555-0100", "street1": "1 Agent Plaza", "zip": "19801"},
            related_persons=(), signatures=()))
    result = derive.build_link_keys(conn)
    assert flagged(conn, "phone") == {"3025550100": over}
    assert flagged(conn, "address") == {"1AGENTPLAZA19801": over}
    assert conn.execute("SELECT count(*) FROM link_key WHERE kind = 'phone'").fetchone()[0] == n_ciks
    assert result["over_cap"]["phone"] == result["over_cap"]["address"] == over


def test_entity_keys_are_flagged_over_their_own_cap_and_signers_never_are():
    conn = repo.connect(":memory:")
    base = fixture("0002158817-26-000001")
    for n in range(derive.ENTITY_KEY_CAP + 1):
        cik = f"{8000000000 + n:010d}"
        repo.upsert_filing(conn, variant(base, f"{cik}-26-000001", cik=cik,
                                         name=f"Platform SPV {n} LLC",
                                         issuer={"phone": f"212555{n:04d}"}))
    result = derive.build_link_keys(conn)
    assert flagged(conn, "entity_stem") == {"AXIARIS CAPITAL MANAGEMENT": 1}
    assert flagged(conn, "entity_tokens") == {"AXIARIS CAPITAL MANAGEMENT": 1}
    assert flagged(conn, "signer") == {"ANDREW MURTLAND": 0}
    assert result["over_cap"]["entity_stem"] == 1

    # An over-cap entity key links nobody: every SPV is its own manager.
    derive.build_funds(conn)
    derive.build_managers(conn)
    assert conn.execute("SELECT count(*) FROM manager").fetchone()[0] == derive.ENTITY_KEY_CAP + 1


def test_generic_stems_are_skipped(conn):
    base = fixture("0002158817-26-000001")
    person = base.related_persons[0]
    names = ("FUND LLC", "SERIES I LLC", "LIMITED LIABILITY COMPANY", "SPV FUNDS LP")
    repo.upsert_filing(conn, variant(
        base, "0009999995-26-000001", cik="0009999995",
        related_persons=tuple(dataclasses.replace(person, seq=n, name_norm=name, is_entity=True)
                              for n, name in enumerate(names, start=1))))
    derive.build_link_keys(conn)
    assert keys(conn, "entity_stem", "0009999995") == set()


def test_address_needs_street_and_zip(conn):
    base = fixture("0002158817-26-000001")
    repo.upsert_filing(conn, variant(base, "0009999996-26-000001", cik="0009999996",
                                     issuer={"street1": None}))
    derive.build_link_keys(conn)
    assert keys(conn, "address", "0009999996") == set()


# build_managers, build_launches, run


def lone(base, n, name, filing_date, **kw):
    """A pooled fund under its own CIK that shares no key with anything else."""
    cik = f"{7000000000 + n:010d}"
    sig = dataclasses.replace(base.signatures[0], signer_norm=f"SIGNER NUMBER {n}")
    kw.setdefault("related_persons", ())
    return variant(base, f"{cik}-26-000001", cik=cik, name=name, filing_date=filing_date,
                   issuer={"phone": f"999555{n:04d}", "street1": f"{n} Lone Road", "zip": "10001"},
                   signatures=(sig,), **kw)


@pytest.fixture
def empty():
    c = repo.connect(":memory:")
    yield c
    c.close()


def manager_of(conn, cik):
    return conn.execute("SELECT manager_id FROM fund_manager WHERE cik = ?", (cik,)).fetchone()[0]


def test_three_funds_sharing_an_entity_related_person_are_one_manager(empty):
    base = fixture("0002158817-26-000001")
    for n, day in ((1, "2026-03-01"), (2, "2026-01-15"), (3, "2026-06-30")):
        f = lone(base, n, f"Shared Fund {n} LP", day)
        repo.upsert_filing(empty, dataclasses.replace(f, related_persons=tuple(
            dataclasses.replace(p, accession=f.accession) for p in base.related_persons)))
    derive.run(empty)
    ids = {manager_of(empty, f"{7000000000 + n:010d}") for n in (1, 2, 3)}
    assert ids == {"m7000000002"}  # the member with the earliest first filing
    m = repo.get_manager(empty, "m7000000002")
    assert (m["n_funds"], m["first_seen_date"], m["key_kind"]) == (3, "2026-01-15", "entity_stem")
    assert m["display_name"] == "AXIARIS CAPITAL MANAGEMENT"
    assert [f["cik"] for f in m["funds"]] == ["7000000002", "7000000001", "7000000003"]
    assert m["funds"][1]["evidence"].startswith("entity_")


def test_aqr_reversed_encoding_links_to_the_forward_one(conn):
    aqr = fixture("0001635511-15-000001")
    person = dataclasses.replace(aqr.related_persons[0], first="AQR Capital Management", last="LLC",
                                 name_norm="AQR CAPITAL MANAGEMENT LLC", is_entity=True)
    forward = lone(aqr, 9, "AQR Global Fund LP", "2016-01-04", related_persons=(person,))
    repo.upsert_filing(conn, forward)
    derive.run(conn)
    assert manager_of(conn, "7000000009") == manager_of(conn, "0001635511") == "m0001635511"
    assert repo.get_fund(conn, "7000000009")["evidence"] == "entity_tokens:AQR CAPITAL MANAGEMENT"


def test_a_fund_with_no_shared_key_is_its_own_manager(conn):
    derive.run(conn)
    m = repo.get_manager(conn, "m0002158817")
    assert (m["n_funds"], m["key_kind"]) == (1, None)
    assert m["display_name"] == "AXIARIS CAPITAL MANAGEMENT"  # its own entity related person
    assert repo.get_fund(conn, "2158817")["evidence"] is None


def launches(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM launch ORDER BY window_start, launch_id")]


def test_master_and_offshore_fold_into_one_launch_and_a_later_fund_starts_another(empty):
    base = fixture("0002158817-26-000001")
    for f in (lone(base, 1, "Zephyr Master Fund LP", "2026-05-01"),
              lone(base, 2, "Zephyr Offshore Fund Ltd", "2026-05-11"),
              lone(base, 3, "Zephyr Fund II LP", "2026-06-15"),
              lone(base, 4, "Unrelated Fund LP", "2026-05-05")):
        repo.upsert_filing(empty, f)
    derive.run(empty)
    rows = launches(empty)
    by_funds = {tuple(json.loads(r["fund_ciks"])): r for r in rows}
    assert set(by_funds) == {("7000000001", "7000000002"), ("7000000004",), ("7000000003",)}
    zephyr = by_funds[("7000000001", "7000000002")]
    assert zephyr["launch_id"] == "7000000001-26-000001"
    assert (zephyr["window_start"], zephyr["window_end"]) == ("2026-05-01", "2026-05-11")
    assert zephyr["anchor_cik"] == "7000000001" and zephyr["fund_type"] == "Hedge Fund"
    assert by_funds[("7000000003",)]["window_start"] == "2026-06-15"


def test_one_managers_funds_window_at_30_days(empty):
    base = fixture("0002158817-26-000001")
    days = ("2026-01-01", "2026-01-31", "2026-02-01", "2026-02-20")
    for n, day in enumerate(days, start=1):
        f = lone(base, n, f"Different Name {n} LP", day)
        repo.upsert_filing(empty, dataclasses.replace(f, related_persons=tuple(
            dataclasses.replace(p, accession=f.accession) for p in base.related_persons)))
    derive.run(empty)
    assert [json.loads(r["fund_ciks"]) for r in launches(empty)] == [
        ["7000000001", "7000000002"], ["7000000003", "7000000004"]]
    assert {r["manager_id"] for r in launches(empty)} == {"m7000000001"}


def test_launch_id_is_the_earliest_d_filing(empty):
    base = fixture("0002158817-26-000001")
    f = lone(base, 1, "Amended Fund LP", "2026-04-01")
    repo.upsert_filing(empty, f)
    amendment = variant(f, "7000000001-26-000002", filing_date="2026-03-01")
    repo.upsert_filing(empty, dataclasses.replace(
        amendment, filing=dataclasses.replace(amendment.filing, submission_type="D/A")))
    derive.run(empty)
    [launch] = launches(empty)
    assert launch["launch_id"] == "7000000001-26-000001"
    assert launch["window_start"] == "2026-03-01"  # the fund's first filing of any kind


def test_only_pooled_funds_get_launches(conn):
    derive.run(conn)
    anchored = {r["anchor_cik"] for r in launches(conn)}
    assert "0002158817" in anchored
    assert "0002153113" not in anchored and "0001546296" not in anchored  # operating companies
    every = [c for r in launches(conn) for c in json.loads(r["fund_ciks"])]
    assert len(every) == len(set(every))


def dump(conn):
    return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()
            for t in ("fund", "link_key", "manager", "fund_manager", "launch")}


def test_run_twice_gives_identical_tables_and_keeps_review(conn):
    first = derive.run(conn, today=__import__("datetime").date(2026, 10, 30))
    before = {t: [tuple(r) for r in rows] for t, rows in dump(conn).items()}
    with conn:
        conn.execute("INSERT INTO review (launch_id, status, notes) VALUES (?, 'watching', 'call')",
                      (launches(conn)[0]["launch_id"],))
    second = derive.run(conn, today=__import__("datetime").date(2026, 10, 30))
    assert first == second
    assert {t: [tuple(r) for r in rows] for t, rows in dump(conn).items()} == before
    assert conn.execute("SELECT status, notes FROM review").fetchall()[0][:] == ("watching", "call")
    log = repo.ingest_status(conn, "derive", "2026-10-30")
    assert (log["status"], log["n_rows"]) == ("ok", first["launch"])


def test_shared_signer_phone_or_address_do_not_link_managers(empty):
    base = fixture("0002158817-26-000001")
    sig = base.signatures[0]
    for n in (1, 2):
        f = lone(base, n, f"Separate Fund {n} LP", f"2026-02-0{n}")
        repo.upsert_filing(empty, dataclasses.replace(
            f, signatures=(dataclasses.replace(sig, accession=f.accession),),
            issuers=tuple(dataclasses.replace(i, phone="2125550199", street1="9 Same St")
                          for i in f.issuers)))
    derive.run(empty)
    assert manager_of(empty, "7000000001") == "m7000000001"
    assert manager_of(empty, "7000000002") == "m7000000002"


@pytest.mark.parametrize("stem, usable", [
    ("DEUTSCHE INTERNATIONAL CORPORATE SERVICES LIMITED", False),
    ("MAPLES FIDUCIARY SERVICES DELAWARE", False),
    ("MAPLES TRUSTEE SERVICES CAYMAN LIMITED", False),
    ("MSW DIRECTOR SERVICES", False),
    ("ASTERION FUND SERVICES", False),
    ("ALLOCATIONS FUND ADMINISTRATION", False),
    ("FINANCIAL SERVICES PARTNERS", True),
    ("BREVAN HOWARD CAPITAL MANAGEMENT", True),
])
def test_service_provider_stems_are_skipped(stem, usable):
    assert derive._usable_stem(stem) is usable
