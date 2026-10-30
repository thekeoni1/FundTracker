"""The derive, new, fund, manager and search commands, through cli.main on the WP1 fixtures."""

import csv
import sys
from datetime import date
from pathlib import Path

import pytest

from fundtracker import cli
from fundtracker.db import repo

sys.path.insert(0, str(Path(__file__).parent))
from test_derive import all_fixtures  # noqa: E402


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "derived.db"
    conn = repo.connect(path)
    for f in all_fixtures():
        repo.upsert_filing(conn, f)
    conn.close()
    return str(path)


@pytest.fixture
def derived(db, capsys):
    assert cli.main(["--db", db, "derive"]) == 0
    capsys.readouterr()
    return db


def test_derive_prints_counts(db, capsys):
    assert cli.main(["--db", db, "derive"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("12 funds, 12 managers, ")
    assert "link keys (0 entity_stem, 0 entity_tokens, 0 phone, 0 address over the cap)" in out


def test_new_before_derive_says_so(db, capsys):
    assert cli.main(["--db", db, "new", "--since", "2026-10-01"]) == 1
    assert "run fundtracker derive first" in capsys.readouterr().out


def test_new_lists_hedge_launches_best_first(derived, capsys):
    assert cli.main(["--db", derived, "new", "--since", "2026-10-01"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[:3] == ["date", "launch", "manager"]
    body = lines[1:-1]
    assert len(body) == 3  # Axiaris, Arrogate and Callodine are the October hedge launches
    top = body[0]
    assert top.startswith("2026-10-28  Axiaris Capital Management Fund, ~")
    assert "Axiaris Capital Management" in top and "06c,3C,3C.1" in top and "TX" in top
    assert "new_manager single_filing" in top
    assert top.endswith("https://www.sec.gov/Archives/edgar/data/2158817/000215881726000001/")
    assert lines[-1] == "3 launches since 2026-10-01, Hedge Fund only"


def test_new_type_all_and_min_score(derived, capsys):
    cli.main(["--db", derived, "new", "--since", "2026-10-01", "--type", "all"])
    out = capsys.readouterr().out
    assert "Armada Capital Credit LP" in out and "Endless VI A LP" in out
    assert "Equera" not in out  # not a pooled fund
    cli.main(["--db", derived, "new", "--since", "2026-10-01", "--min-score", "85"])
    out = capsys.readouterr().out
    assert "Axiaris" in out and "Callodine" not in out
    assert "with score >= 85" in out


def test_new_csv_has_printed_and_raw_columns(derived, capsys, tmp_path):
    path = tmp_path / "exports" / "new.csv"
    cli.main(["--db", derived, "new", "--since", "2026-10-01", "--type", "all", "--csv", str(path)])
    assert f"wrote {path}" in capsys.readouterr().out
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["launch"].startswith("Axiaris")
    for column in ("edgar", "launch_id", "newness_reasons", "fund_ciks", "manager_name",
                   "fund_first_filing_date", "fund_candidate_reason", "fund_exemptions"):
        assert column in rows[0]
    assert rows[0]["launch_id"] == "0002158817-26-000001"


def test_parse_since():
    assert cli.parse_since("7d", today=date(2026, 10, 30)) == "2026-10-23"
    assert cli.parse_since("30d", today=date(2026, 10, 30)) == "2026-09-30"
    assert cli.parse_since("2026-10-01") == "2026-10-01"
    with pytest.raises(Exception):
        cli.parse_since("last week")


def test_fund_shows_filings_people_signers_and_links(derived, capsys):
    assert cli.main(["--db", derived, "fund", "2158817"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Axiaris Capital Management Fund, LP (CIK 0002158817), Hedge Fund, "
                          "hedge candidate (hedge_fund)")
    assert "manager m0002158817" in out
    assert "2026-10-28  D    0002158817-26-000001  06c,3C,3C.1" in out
    assert "AXIARIS CAPITAL MANAGEMENT LLC (entity)" in out
    assert "ANDREW MURTLAND" in out
    assert "https://www.sec.gov/Archives/edgar/data/2158817/000215881726000001/" in out
    assert cli.main(["--db", derived, "fund", "1"]) == 1


def test_manager_lists_its_funds(derived, capsys):
    assert cli.main(["--db", derived, "manager", "m0001635511"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("AQR Capital Management GP Ltd. (m0001635511), first seen 2015-06-15, 1 funds")
    assert "AQR OFFSHORE MULTI-STRATEGY FUND XIV" in out
    assert cli.main(["--db", derived, "manager", "m1"]) == 1


def test_search_finds_funds_managers_and_people(derived, capsys):
    assert cli.main(["--db", derived, "search", "axiaris"]) == 0
    out = capsys.readouterr().out.splitlines()
    kinds = [line.split()[0] for line in out[:-1]]
    assert kinds == ["manager", "fund", "person"]
    assert "AXIARIS CAPITAL MANAGEMENT LLC  (on fund 0002158817)" in out[2]
    assert out[-1] == "3 matches"
    cli.main(["--db", derived, "search", "murtland"])
    assert "person                ANDREW CUNNINGHAM MURTLAND  (on fund 0002158817)" in (
        capsys.readouterr().out)
    cli.main(["--db", derived, "search", "50%_off"])
    assert capsys.readouterr().out == "0 matches\n"  # wildcards are literal
