import dataclasses
import re
from pathlib import Path

import pytest

from fundtracker.db import repo
from fundtracker.edgar.formd_xml import parse_formd
from fundtracker.model import Filing, Issuer, Offering, Recipient, RelatedPerson, Signature

FIXTURES = Path(__file__).parent / "fixtures" / "formd"
TABLES = ("filing", "issuer", "offering", "related_person", "recipient", "signature")


def fixture_dates():
    """Filing dates recorded in the fixture README ("filed YYYY-MM-DD")."""
    text = (FIXTURES / "README.md").read_text(encoding="utf-8")
    return dict(re.findall(r"`(\d{10}-\d{2}-\d{6})\.xml`.*?filed (\d{4}-\d{2}-\d{2})", text))


def parsed_fixtures():
    dates = fixture_dates()
    return [
        parse_formd(p.read_bytes(), p.stem, filing_date=dates[p.stem])
        for p in sorted(FIXTURES.glob("*.xml"))
    ]


def dump(conn):
    return {
        t: sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall()) for t in TABLES
    }


@pytest.fixture
def conn(tmp_path):
    c = repo.connect(tmp_path / "test.db")
    yield c
    c.close()


def test_schema_columns_match_dataclasses(conn):
    for table, cls in [("filing", Filing), ("issuer", Issuer), ("offering", Offering),
                       ("related_person", RelatedPerson), ("recipient", Recipient),
                       ("signature", Signature)]:
        columns = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
        expected = [f.name for f in dataclasses.fields(cls)]
        if table == "filing":
            expected.append("loaded_at")
        assert columns == expected, table


def test_connect_sets_wal_and_foreign_keys_and_is_idempotent(tmp_path):
    path = tmp_path / "sub" / "x.db"
    repo.connect(path).close()
    c = repo.connect(path)
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    c.close()


def test_every_fixture_has_a_filing_date():
    assert len(fixture_dates()) == 12


def test_loading_twice_is_idempotent(conn):
    filings = parsed_fixtures()
    for f in filings:
        repo.upsert_filing(conn, f)
    first = dump(conn)
    for f in filings:
        repo.upsert_filing(conn, f)
    assert dump(conn) == first
    counts = {t: len(rows) for t, rows in first.items()}
    assert counts["filing"] == counts["offering"] == 12
    assert counts["issuer"] == 13  # GEF has one co-issuer
    assert counts["signature"] == 13  # GEF has two signatures
    assert counts["recipient"] == sum(len(f.recipients) for f in filings) == 6
    assert counts["related_person"] == sum(len(f.related_persons) for f in filings)


def test_round_trip_is_exact(conn):
    for f in parsed_fixtures():
        repo.upsert_filing(conn, f)
        assert repo.get_filing(conn, f.accession) == f


def test_rewrite_drops_stale_children_and_keeps_loaded_at(conn):
    f = parsed_fixtures()[0]
    repo.upsert_filing(conn, f)
    loaded_at = conn.execute("SELECT loaded_at FROM filing").fetchone()[0]
    conn.execute("UPDATE filing SET loaded_at = '2000-01-01T00:00:00+00:00'")
    conn.commit()
    trimmed = dataclasses.replace(
        f, related_persons=f.related_persons[:1],
        offering=dataclasses.replace(f.offering, fund_type="Changed"))
    repo.upsert_filing(conn, trimmed)
    assert repo.get_filing(conn, f.accession) == trimmed
    assert conn.execute("SELECT loaded_at FROM filing").fetchone()[0] == "2000-01-01T00:00:00+00:00"
    assert loaded_at


def test_failed_write_rolls_back(conn):
    f = parsed_fixtures()[0]
    repo.upsert_filing(conn, f)
    before = dump(conn)
    # is_entity is NOT NULL, so the last related person fails after the filing
    # and offering were updated and the old child rows deleted.
    bad_person = dataclasses.replace(f.related_persons[-1], is_entity=None)
    broken = dataclasses.replace(
        f, offering=dataclasses.replace(f.offering, fund_type="Changed"),
        related_persons=f.related_persons[:-1] + (bad_person,))
    with pytest.raises(Exception):
        repo.upsert_filing(conn, broken)
    assert dump(conn) == before


def test_get_filing_missing_returns_none(conn):
    assert repo.get_filing(conn, "0000000000-00-000000") is None


def test_filings_for_cik_orders_by_filing_date(conn):
    xml = (FIXTURES / "0002158817-26-000001.xml").read_bytes()
    for accession, date in [("0002158817-26-000009", "2026-10-28"),
                            ("0002158817-25-000001", "2025-01-02"),
                            ("0002158817-26-000002", "2026-03-15"),
                            ("0002158817-24-000001", None)]:
        repo.upsert_filing(conn, parse_formd(xml, accession, filing_date=date))
    got = [(f.accession, f.filing.filing_date) for f in repo.filings_for_cik(conn, "0002158817")]
    assert got == [("0002158817-24-000001", None),
                   ("0002158817-25-000001", "2025-01-02"),
                   ("0002158817-26-000002", "2026-03-15"),
                   ("0002158817-26-000009", "2026-10-28")]


def test_filings_for_cik_includes_co_issuer(conn):
    for f in parsed_fixtures():
        repo.upsert_filing(conn, f)
    assert [f.accession for f in repo.filings_for_cik(conn, "0002161347")] == ["0002161535-26-000001"]
    assert repo.filings_for_cik(conn, "0009999999") == []
