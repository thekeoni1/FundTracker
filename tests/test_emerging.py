"""repo.emerging_managers and the emerging command, on synthetic funds dated relative to today."""

import dataclasses
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from fundtracker import cli, derive
from fundtracker.db import repo
from fundtracker.traction import months_before

sys.path.insert(0, str(Path(__file__).parent))
from test_derive import fixture, lone, person  # noqa: E402

TODAY = date.today()


def ago(months, days=0):
    return (months_before(TODAY, months) - timedelta(days=days)).isoformat()


def with_people(f, *people):
    return dataclasses.replace(f, related_persons=tuple(
        dataclasses.replace(p, accession=f.accession, seq=i) for i, p in enumerate(people, start=1)))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Managers of every kind around the default 6 to 18 month window; returns the db path."""
    for kind in ("entity_stem", "entity_tokens"):
        monkeypatch.setitem(derive.KEY_CAPS, kind, 5)
    base = fixture("0002158817-26-000001")
    sold = {"total_amount_sold_num": 18_000_000, "n_investors": 12}
    jane = person(base, 1, "Jane", "Doe", ("Executive Officer",), middle="Q.")
    heron_gp = person(base, 2, "N/A", "Heron Capital Management LLC", entity=True)
    funds = [
        # Heron: 12 months old, two vehicles, money raised. The best emerging manager.
        with_people(lone(base, 1, "Heron Master Fund LP", ago(12), offering=sold), jane, heron_gp),
        with_people(lone(base, 2, "Heron Offshore Fund Ltd", ago(11)), heron_gp),
        # Teal: 9 months old, one fund, nothing raised.
        lone(base, 3, "Teal Fund LP", ago(9), offering={"total_amount_sold_num": 0, "n_investors": 0}),
        # Too young, and too old.
        lone(base, 4, "Fresh Fund LP", ago(3), offering=sold),
        lone(base, 5, "Vintage Fund LP", ago(24), offering=sold),
        # A private equity fund in the window: listed only with --type all.
        lone(base, 6, "Oakridge Buyout Fund LP", ago(10),
             offering={"fund_type": "Private Equity Fund", "total_amount_sold_num": 2_000_000}),
    ]
    # Six funds on one entity: over the patched cap of 5, so a platform, never listed.
    platform = person(base, 1, "N/A", "Bigco Platform LLC", entity=True)
    funds += [with_people(lone(base, 10 + n, f"Bigco Vehicle {n} LP", ago(12), offering=sold), platform)
              for n in range(6)]
    path = tmp_path / "emerging.db"
    conn = repo.connect(path)
    for f in funds:
        repo.upsert_filing(conn, f)
    derive.run(conn)
    conn.close()
    monkeypatch.setenv("COLUMNS", "110")
    return str(path)


def names(rows):
    """Each manager by its first fund's name, sorted (Teal and Oakridge are named by their signer)."""
    return sorted(r["funds"][0]["name"] for r in rows)


def test_repo_keeps_the_window_and_drops_platforms(world):
    conn = repo.connect(world)
    hedge = repo.emerging_managers(conn, TODAY, 6, 18, ["Hedge Fund"])
    assert names(hedge) == ["Heron Master Fund LP", "Teal Fund LP"]
    heron = next(m for m in hedge if m["display_name"].startswith("Heron"))
    assert [f["name"] for f in heron["funds"]] == ["Heron Master Fund LP", "Heron Offshore Fund Ltd"]
    assert heron["funds"][0]["latest_total_amount_sold"] == 18_000_000
    every = repo.emerging_managers(conn, TODAY, 6, 18)
    assert names(every) == ["Heron Master Fund LP", "Oakridge Buyout Fund LP", "Teal Fund LP"]
    assert names(repo.emerging_managers(conn, TODAY, 6, 18, ["Hedge Fund"], max_funds=2)) == ["Teal Fund LP"]
    with_platform = repo.emerging_managers(conn, TODAY, 6, 18, ["Hedge Fund"], exclude_over_cap=False)
    assert len(with_platform) == 2 + 6
    assert len(repo.emerging_managers(conn, TODAY, timedelta(days=0), 36, ["Hedge Fund"])) == 4


def test_emerging_prints_best_first_with_people_and_reasons(world, capsys):
    assert cli.main(["--db", world, "emerging"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[:4] == ["Score", "Manager", "First", "seen"]
    first, second = [i for i, line in enumerate(lines[1:-1], start=1) if line[:5].strip().isdigit()]
    heron = lines[first:second]
    # 18m raised, 12 investors, two vehicles; no amendment, nothing filed in six months.
    assert heron[0].split()[:3] == ["65", "Heron", "Capital"]  # the name wraps in its column
    assert "Management LLC" in heron[1]
    assert ago(12) in heron[0] and "18,000,000" in heron[0] and "Houston, TX" in heron[0]
    notes = [line for line in heron if line.startswith(" " * 6) and line[6] != " "]
    assert notes[0] == "      Jane Q. Doe (Executive Officer)"
    assert notes[1].startswith("      raised money (+25); raised 10m or more (+10); has investors (+10)")
    assert lines[second].split()[0] == "0" and "nothing raised yet (-20)" in " ".join(lines[second:-1])
    assert lines[-1].startswith("2 managers first seen ")
    assert lines[-1].endswith(", hedge fund candidates, fewer than 20 funds, no over-cap organisations")
    assert all(len(line) <= 110 for line in lines[:-1])  # the table; the summary line may run on


def test_emerging_type_all_min_score_ages_and_csv(world, capsys, tmp_path):
    cli.main(["--db", world, "emerging", "--type", "all"])
    out = capsys.readouterr().out
    assert out.splitlines()[-1].startswith("3 managers first seen ") and "runs a hedge fund" in out
    cli.main(["--db", world, "emerging", "--min-score", "50"])
    out = capsys.readouterr().out
    assert "Heron" in out and out.splitlines()[-1].startswith("1 managers first seen ")
    cli.main(["--db", world, "emerging", "--min-age", "60d", "--max-age", "36m"])
    assert capsys.readouterr().out.splitlines()[-1].startswith("4 managers first seen ")
    path = tmp_path / "e.csv"
    cli.main(["--db", world, "emerging", "--csv", str(path)])
    assert f"wrote {path}" in capsys.readouterr().out
    header, heron = path.read_text(encoding="utf-8").splitlines()[:2]
    assert header.startswith("score,manager,first_seen,age,funds,raised")
    assert "people_text" in header and "manager_id" in header and "Jane Q. Doe" in heron


def test_parse_age():
    assert cli.parse_age("6m") == 6 and cli.parse_age("180D") == timedelta(days=180)
    with pytest.raises(Exception):
        cli.parse_age("half a year")
    assert cli.default_emerging_path("hedge", "6m", "18m", today=date(2026, 10, 30)).name == (
        "emerging-hedge-6m-18m-20261030.xlsx")


def test_emerging_before_derive_says_so(tmp_path, capsys):
    path = tmp_path / "empty.db"
    repo.connect(path).close()
    assert cli.main(["--db", str(path), "emerging"]) == 1
    assert "run fundtracker derive first" in capsys.readouterr().out
