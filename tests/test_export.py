"""export.write_workbook on the WP1 fixtures, read back with openpyxl."""

import sys
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import load_workbook

from fundtracker import derive, export, newness
from fundtracker.db import repo

sys.path.insert(0, str(Path(__file__).parent))
from test_derive import all_fixtures  # noqa: E402


@pytest.fixture(scope="module")
def workbook(tmp_path_factory):
    conn = repo.connect(":memory:")
    for f in all_fixtures():
        repo.upsert_filing(conn, f)
    derive.run(conn)
    launches = repo.launches_since(conn, "2026-10-01", 0, ["Hedge Fund"])
    funds = repo.funds_for_launches(conn, launches)
    path = tmp_path_factory.mktemp("export") / "new.xlsx"
    meta = {"generated_at": datetime(2026, 10, 30, 18, 5), "since": "2026-10-01", "type": "hedge",
            "min_score": 0, "last_daily": "2026-10-29"}
    export.write_workbook(path, launches, funds, meta)
    return load_workbook(path)


def rows(ws):
    return [[c.value for c in row] for row in ws.iter_rows()]


def by_header(ws, row):
    return dict(zip([c.value for c in ws[1]], [c.value for c in ws[row]]))


def test_sheets_and_launch_header(workbook):
    assert workbook.sheetnames == ["Launches", "Funds", "Reasons", "About"]
    ws = workbook["Launches"]
    assert [c.value for c in ws[1]] == [
        "Date", "Score", "Launch", "Manager", "Funds", "Type", "Exemptions", "Rule 506",
        "Min investment", "Offering amount", "Amount sold", "Investors", "State", "Platform",
        "Reasons", "EDGAR", "Launch ID"]
    assert all(c.font.bold for c in ws[1])
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref == "A1:Q4"


def test_axiaris_row_values_and_formats(workbook):
    ws = workbook["Launches"]
    row = by_header(ws, 2)
    assert row["Date"] == datetime(2026, 10, 28)  # openpyxl reads dates back as datetimes
    assert ws["A2"].number_format == "yyyy-mm-dd"
    assert (row["Score"], row["Launch"], row["Manager"], row["Funds"], row["Type"]) == (
        100, "Axiaris Capital Management Fund, LP", "Axiaris Capital Management LLC", 1, "Hedge Fund")
    assert (row["Exemptions"], row["Rule 506"], row["State"]) == ("06c, 3C, 3C.1", "506(c)", "TX")
    assert row["Offering amount"] == 25_000_000 and ws["J2"].number_format == "#,##0"
    assert row["Reasons"].startswith("new manager: no key seen before (+40); first notice:")
    assert row["Platform"] is None and row["Launch ID"] == "0002158817-26-000001"


def test_edgar_cell_links_to_the_anchor_filing_folder(workbook):
    cell = workbook["Launches"]["P2"]
    assert cell.value == "filing"
    assert cell.hyperlink.target == "https://www.sec.gov/Archives/edgar/data/2158817/000215881726000001/"


def test_indefinite_stays_text(workbook):
    ws = workbook["Launches"]
    offering = {by_header(ws, r)["Launch"]: by_header(ws, r)["Offering amount"] for r in (2, 3, 4)}
    assert offering["Arrogate Capital Fund, LP"] == "Indefinite"
    assert isinstance(offering["Arrogate Capital Fund, LP"], str)


def test_score_has_a_three_colour_scale(workbook):
    ws = workbook["Launches"]
    [formatting] = list(ws.conditional_formatting)
    assert str(formatting.sqref) == "B2:B4"
    [rule] = formatting.rules
    assert rule.type == "colorScale"
    assert [c.rgb[-6:] for c in rule.colorScale.color] == list(export.SCORE_COLOURS)


def test_funds_reasons_and_about(workbook):
    funds = workbook["Funds"]
    header = [c.value for c in funds[1]]
    assert header[:4] == ["Launch ID", "Launch score", "EDGAR", "cik"]
    assert {"first_filing_date", "min_investment_num", "family_stem"} <= set(header)
    first = by_header(funds, 2)
    assert (first["Launch ID"], first["Launch score"], first["cik"]) == (
        "0002158817-26-000001", 100, "0002158817")
    assert first["first_filing_date"] == datetime(2026, 10, 28)

    reasons = rows(workbook["Reasons"])
    assert reasons[0] == ["Launch ID", "Launch", "Code", "Reason", "Points", "Detail"]
    assert reasons[1][:5] == ["0002158817-26-000001", "Axiaris Capital Management Fund, LP",
                              "new_manager", newness.REASON_LABELS["new_manager"], 40]

    about = {r[0]: r[1] for r in rows(workbook["About"]) if r[0]}
    assert about["Launches"] == 3 and about["Fund type"] == "Hedge Fund only"
    assert about["Last daily index loaded"] == datetime(2026, 10, 29)
    assert about["Entity key cap, CIKs"] == derive.ENTITY_KEY_CAP
    assert about["new_manager"] == newness.REASON_LABELS["new_manager"]


def test_every_weight_has_a_reason_label():
    assert set(newness.REASON_LABELS) == set(newness.WEIGHTS)
    assert newness.reason_phrase({"code": "platform", "points": 0, "detail": "x"}) == (
        "organisation on many funds")
