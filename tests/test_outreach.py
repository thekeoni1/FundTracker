"""The outreach sheet: rows exported in the sheet's column order. Every name here is made up."""

import csv
import io
import re
import sys
from pathlib import Path

import pytest

from fundtracker import derive, outreach
from fundtracker.db import repo
from fundtracker.web.app import create_app

sys.path.insert(0, str(Path(__file__).parent))
from test_derive import fixture, lone, person  # noqa: E402
from test_emerging import TODAY, ago, with_people  # noqa: E402

OSPREY = "7000000001"
WREN = "7000000002"


@pytest.fixture
def sheet_world(tmp_path):
    """Osprey, two named people and money raised; Wren, nobody named. Both hedge, 10 and 9 months old."""
    base = fixture("0002158817-26-000001")
    ann = person(base, 1, "Ann", "Example", ("Executive Officer",))
    bo = person(base, 2, "Bo", "Sample", ("Director",))
    gp = person(base, 3, "N/A", "Osprey Capital Management LLC", entity=True)
    funds = [
        with_people(lone(base, 1, "Osprey Fund LP", ago(10),
                         offering={"total_amount_sold_num": 12_000_000, "n_investors": 5}), ann, bo, gp),
        lone(base, 2, "Wren Fund LP", ago(9), offering={"total_amount_sold_num": 0, "n_investors": 0}),
    ]
    path = tmp_path / "sheet.db"
    conn = repo.connect(path)
    for f in funds:
        repo.upsert_filing(conn, f)
    derive.run(conn)
    conn.close()
    return str(path)


@pytest.fixture
def client(sheet_world):
    return create_app(connect=lambda: repo.connect_readonly(sheet_world), today=lambda: TODAY).test_client()


def read_csv(response):
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert f"outreach-{TODAY:%Y%m%d}.csv" in response.headers["Content-Disposition"]
    return list(csv.reader(io.StringIO(response.get_data(as_text=True))))


def column(row, header):
    return row[outreach.SHEET_COLUMNS.index(header)]


def test_sheet_columns_are_the_master_tab_with_ft_key_last():
    assert outreach.SHEET_COLUMNS == ("NAME", "NOTES", "STATUS", "LAST ACTION", "FIELD", "FIRM",
                                      "SENIORITY", "HOOK", "LINKEDIN", "CONTACT", "PERSONAL EMAIL",
                                      "FT KEY")


def test_two_people_give_two_rows_in_sheet_order_without_a_header(client):
    rows = read_csv(client.get(f"/manager/m{OSPREY}/outreach.csv"))
    assert len(rows) == 2 and all(len(r) == 12 for r in rows)
    assert [r[0] for r in rows] == ["Ann Example", "Bo Sample"]
    assert all(r[-1] == OSPREY for r in rows)
    first = rows[0]
    assert column(first, "STATUS") == "To Contact" and column(first, "FIELD") == "HF"
    assert column(first, "FIRM") == "Osprey Capital Management LLC"
    assert column(first, "NOTES") == f"FundTracker: raised 12,000,000; first seen {ago(10)}"
    assert column(first, "LINKEDIN").startswith("https://www.linkedin.com/search/results/people/")
    assert "Ann+Example+Osprey" in column(first, "LINKEDIN")
    for blank in ("LAST ACTION", "SENIORITY", "HOOK", "CONTACT", "PERSONAL EMAIL"):
        assert column(first, blank) == ""


def test_no_people_give_one_row_with_name_blank(client):
    rows = read_csv(client.get(f"/manager/m{WREN}/outreach.csv"))
    assert len(rows) == 1
    assert rows[0][0] == "" and rows[0][-1] == WREN and column(rows[0], "STATUS") == "To Contact"
    assert column(rows[0], "NOTES") == f"FundTracker: first seen {ago(9)}"


def test_unknown_manager_404s(client):
    assert client.get("/manager/M9999999999/outreach.csv").status_code == 404
    assert client.post("/emerging/outreach.csv", data={"manager_id": ["M9999999999"]}).status_code == 404


def test_emerging_page_posts_the_ticked_managers(client):
    html = client.get("/emerging").get_data(as_text=True)
    assert 'action="/emerging/outreach.csv"' in html
    ticks = re.findall(r'name="manager_id" value="([^"]+)" form="outreach"', html)
    assert sorted(ticks) == [f"m{OSPREY}", f"m{WREN}"]
    rows = read_csv(client.post("/emerging/outreach.csv", data={"manager_id": ticks}))
    assert sorted(r[-1] for r in rows) == [OSPREY, OSPREY, WREN]
    assert client.post("/emerging/outreach.csv", data={}).status_code == 400


def test_notes_name_each_part_only_when_present():
    m = {"manager_id": "M1", "total_sold": 12_000_000, "first_seen_date": "2025-06-03",
         "website": "https://Example.com/", "adviser_kind": "ria", "employees": 3}
    assert outreach.notes(m) == ("FundTracker: raised 12,000,000; first seen 2025-06-03; "
                                 "website example.com; RIA, 3 employees")
    assert outreach.notes({"manager_id": "M1", "adviser_kind": "era"}) == "FundTracker: ERA"
    assert outreach.notes({"manager_id": "M1"}) == "FundTracker"


def test_write_sheet_csv_has_no_header():
    out = io.StringIO()
    outreach.write_sheet_csv(out, [["Ann Example", "a, b"] + [""] * 10])
    assert out.getvalue() == 'Ann Example,"a, b",,,,,,,,,,\r\n'
