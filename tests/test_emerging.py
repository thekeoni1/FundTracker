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
        monkeypatch.setitem(derive.KEY_CAPS, kind, 10)
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
        # An Other Investment Fund that the 3C rule makes a hedge fund candidate for new.
        # emerging hedge mode wants a Hedge Fund vehicle, so it too is listed only with --type all.
        lone(base, 7, "Granite Absolute Return III LP", ago(10),
             offering={"fund_type": "Other Investment Fund", "exemptions": ("06b", "3C.7"),
                       "total_amount_sold_num": 900_000_000, "n_investors": 40}),
    ]
    # Eleven funds on one entity: over the patched cap of 10, so a platform, never listed.
    platform = person(base, 1, "N/A", "Bigco Platform LLC", entity=True)
    funds += [with_people(lone(base, 10 + n, f"Bigco Vehicle {n} LP", ago(12), offering=sold), platform)
              for n in range(11)]
    # A ten-fund firm, outside every window, where Jane Q. Doe was named before Heron:
    # Heron's prior firm.
    bigfirm = person(base, 2, "N/A", "Bigfirm Capital LLC", entity=True)
    funds += [with_people(lone(base, 40 + n, f"Bigfirm Vehicle {n} LP", ago(40)), jane, bigfirm)
              for n in range(10)]
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
    assert names(every) == ["Granite Absolute Return III LP", "Heron Master Fund LP",
                            "Oakridge Buyout Fund LP", "Teal Fund LP"]
    conn_funds = {r[0]: r[1] for r in conn.execute("SELECT name, is_hedge_candidate FROM fund")}
    assert conn_funds["Granite Absolute Return III LP"] == 1  # new would still take it
    assert names(repo.emerging_managers(conn, TODAY, 6, 18, ["Hedge Fund"], max_funds=2)) == ["Teal Fund LP"]
    with_platform = repo.emerging_managers(conn, TODAY, 6, 18, ["Hedge Fund"], exclude_over_cap=False)
    assert len(with_platform) == 2 + 11
    assert len(repo.emerging_managers(conn, TODAY, timedelta(days=0), 36, ["Hedge Fund"])) == 4


def test_emerging_prints_best_first_with_people_and_reasons(world, capsys):
    assert cli.main(["--db", world, "emerging"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[:4] == ["Score", "Manager", "First", "seen"]
    first, second = [i for i, line in enumerate(lines[1:-1], start=1) if line[:5].strip().isdigit()]
    heron = lines[first:second]
    # 18m raised, 12 investors, two vehicles; no amendment, nothing filed in six months.
    assert heron[0].split()[:3] == ["70", "Heron", "Capital"]  # the name wraps in its column
    assert "Management LLC" in heron[1]
    assert ago(12) in heron[0] and "18,000,000" in heron[0] and "Houston, TX" in heron[0]
    notes = [line for line in heron if line.startswith(" " * 6) and line[6] != " "]
    assert notes[0] == "      Jane Q. Doe (Executive Officer)"
    assert notes[1] == "      prior firm: Bigfirm Capital LLC"
    assert notes[2].startswith("      raised money (+25); amount raised (+15); has investors (+10)")
    assert "people filed earlier for a large firm" in " ".join(notes[2:])
    assert lines[second].split()[0] == "0" and "nothing raised yet (-20)" in " ".join(lines[second:-1])
    assert lines[-1].startswith("2 managers first seen ")
    assert lines[-1].endswith(", with a Hedge Fund vehicle, fewer than 20 funds, no over-cap organisations")
    assert all(len(line) <= 110 for line in lines[:-1])  # the table; the summary line may run on


def test_emerging_type_all_min_score_ages_and_csv(world, capsys, tmp_path):
    cli.main(["--db", world, "emerging", "--type", "all"])
    out = capsys.readouterr().out
    assert out.splitlines()[-1].startswith("4 managers first seen ") and "runs a hedge fund" in out
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


def test_emerging_workbook(world, capsys, tmp_path, monkeypatch):
    from openpyxl import load_workbook

    monkeypatch.setattr(cli.config, "EXPORT_DIR", tmp_path / "data" / "exports")
    assert cli.main(["--db", world, "emerging", "--xlsx"]) == 0
    path = cli.default_emerging_path("hedge", "6m", "18m")
    assert capsys.readouterr().out.splitlines()[-1] == f"wrote {path}"
    wb = load_workbook(path)
    assert wb.sheetnames == ["Managers", "Funds", "People", "Reasons", "About"]

    ws = wb["Managers"]
    header = [c.value for c in ws[1]]
    assert header == ["Score", "Manager", "First seen", "Age months", "Funds", "Raised", "Offering",
                      "Remaining", "Investors", "Amended", "Latest filing", "Types", "Exemptions", "City", "State", "Country",
                      "People", "Prior firm", "Website", "Adviser", "Adviser CRD", "Employees", "RAUM",
                      "SEC hedge funds", "Outreach", "Last action", "Reasons", "EDGAR", "Manager ID"]
    heron = dict(zip(header, [c.value for c in ws[2]]))
    assert (heron["Score"], heron["Manager"], heron["Funds"], heron["Raised"], heron["Investors"]) == (
        70, "Heron Capital Management LLC", 2, 18_000_000, 12)
    assert heron["First seen"].date().isoformat() == ago(12) and ws["C2"].number_format == "yyyy-mm-dd"
    assert ws["F2"].number_format == "#,##0" and heron["Amended"] == "no"
    assert (heron["City"], heron["State"], heron["Country"]) == ("Houston", "TX", "United States")
    assert heron["Types"] == "Hedge Fund" and heron["Exemptions"] == "06c, 3C, 3C.1"
    assert heron["People"] == "Jane Q. Doe (Executive Officer)"
    assert heron["Reasons"].startswith("raised money (+25); amount raised (+15)")
    assert heron["Prior firm"] == "Bigfirm Capital LLC"
    assert heron["Adviser"] is None and heron["Employees"] is None  # no adviser loaded
    assert ws.cell(row=2, column=header.index("EDGAR") + 1).hyperlink.target == ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                                         "&CIK=7000000001&type=D&dateb=&owner=include&count=40")
    [scale] = list(ws.conditional_formatting)
    assert str(scale.sqref) == "A2:A3" and scale.rules[0].type == "colorScale"  # Score is column A
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref == f"A1:{ws.cell(row=1, column=len(header)).column_letter}3"

    funds = wb["Funds"]
    fheader = [c.value for c in funds[1]]
    assert fheader[:3] == ["Manager ID", "Manager score", "EDGAR"]
    assert {"latest_total_amount_sold", "latest_n_investors", "first_filing_date"} <= set(fheader)
    assert funds.max_row - 1 == 3  # Heron's two funds and Teal's one

    people = [[c.value for c in row] for row in wb["People"].iter_rows()]
    assert people[0] == ["Manager", "Name", "Roles", "Fund", "Filing date", "EDGAR"]
    assert people[1][:4] == ["Heron Capital Management LLC", "Jane Q. Doe", "Executive Officer",
                             "Heron Master Fund LP"]
    assert wb["People"]["F2"].hyperlink.target == (
        "https://www.sec.gov/Archives/edgar/data/7000000001/700000000126000001/")

    reasons = [[c.value for c in row] for row in wb["Reasons"].iter_rows()]
    assert reasons[0] == ["Manager ID", "Manager", "Code", "Reason", "Points", "Detail"]
    assert reasons[1][2:5] == ["raised", "raised money", 25]
    about = {r[0].value: r[1].value for r in wb["About"].iter_rows() if r[0].value}
    assert about["Managers"] == 2 and about["People named"] == 1 and about["raised"] == "raised money"
    assert about["Raised bands"] == ("1,000,000 to 5,000,000: +5; 5,000,000 to 100,000,000: +15; "
                                     "100,000,000 to 500,000,000: +10; 500,000,000 to 2,000,000,000: -10; "
                                     "2,000,000,000 and up: -30")
