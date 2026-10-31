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


# The real MASTER header with FT KEY added; every row below is made up.
SHEET_HEADER = ",".join(outreach.SHEET_COLUMNS)
SHEET_CSV = "\n".join([
    SHEET_HEADER,
    f'Ann Example,"met at a conference, warm",Outreach Sent,2026-10-20,HF,Osprey Capital,,,,,'
    f"ann@example.com,{OSPREY}",
    f"Bo Sample,,To Contact,,HF,Osprey Capital,,,,,,{OSPREY}",
    "Cy Placeholder,,Follow-Up,2026-10-01,PE,Other Firm,,,,,cy@example.com,",
    "Di Madeup,,Waiting On Intro,2026-09-15,HF,Wren Partners,,,,,,2",
]) + "\n"


def outreach_rows(path):
    conn = repo.connect(path)
    try:
        return {r["ft_key"]: dict(r) for r in conn.execute("SELECT * FROM outreach")}
    finally:
        conn.close()


def pull_file(sheet_world, tmp_path, text=SHEET_CSV):
    source = tmp_path / "outreach.csv"
    source.write_text(text, encoding="utf-8")
    conn = repo.connect(sheet_world)
    try:
        return outreach.pull(conn, source)
    finally:
        conn.close()


def ticked(html):
    return re.findall(r'name="manager_id" value="([^"]+)"', html)


def test_pull_keeps_keyed_rows_and_only_four_columns(sheet_world, tmp_path):
    assert pull_file(sheet_world, tmp_path) == (3, 1)
    rows = outreach_rows(sheet_world)
    # The key 2 is a CIK whose zeros Sheets dropped; it is padded back.
    assert sorted(rows) == ["0000000002", OSPREY]
    osprey = rows[OSPREY]
    assert osprey["status"] == "Outreach Sent" and osprey["last_action"] == "2026-10-20"
    assert osprey["name"] == "Ann Example, Bo Sample"
    assert set(osprey) == {"ft_key", "name", "status", "last_action", "fetched_at"}
    assert "example.com" not in repr(rows) and "conference" not in repr(rows)
    assert rows["0000000002"]["status"] == "Waiting On Intro"  # not in the sheet's list, kept as written


def test_pull_replaces_the_table(sheet_world, tmp_path):
    pull_file(sheet_world, tmp_path)
    text = SHEET_HEADER + f"\nBo Sample,,Ghosted,,HF,,,,,,,{OSPREY}\n"
    assert pull_file(sheet_world, tmp_path, text) == (1, 0)
    assert {k: r["status"] for k, r in outreach_rows(sheet_world).items()} == {OSPREY: "Ghosted"}


def test_pull_matches_columns_by_header_name(sheet_world, tmp_path):
    text = f"FT KEY,STATUS,NAME\n{WREN},Call Scheduled,Di Madeup\n"
    assert pull_file(sheet_world, tmp_path, text) == (1, 0)
    assert outreach_rows(sheet_world)[WREN]["status"] == "Call Scheduled"


def test_pull_without_an_ft_key_column_says_so(sheet_world, tmp_path):
    with pytest.raises(ValueError, match="FT KEY"):
        pull_file(sheet_world, tmp_path, "NAME,STATUS\nAnn Example,To Contact\n")


def test_pull_from_a_url_goes_through_fetch(sheet_world):
    asked = []
    conn = repo.connect(sheet_world)
    try:
        kept = outreach.pull(conn, "https://docs.example.com/sheet.csv",
                             fetch=lambda url: asked.append(url) or SHEET_CSV.encode("utf-8"))
    finally:
        conn.close()
    assert kept == (3, 1) and asked == ["https://docs.example.com/sheet.csv"]


def test_derive_leaves_the_outreach_table_alone(sheet_world, tmp_path):
    pull_file(sheet_world, tmp_path)
    before = outreach_rows(sheet_world)
    conn = repo.connect(sheet_world)
    derive.run(conn)
    conn.close()
    assert outreach_rows(sheet_world) == before


def test_outreach_filter_column_and_manager_page(sheet_world, tmp_path, client):
    assert ticked(client.get("/emerging?outreach=untouched").get_data(as_text=True)) == [
        f"m{OSPREY}", f"m{WREN}"]
    pull_file(sheet_world, tmp_path, SHEET_HEADER + f"\nAnn Example,,Outreach Sent,2026-10-20,,,,,,,,{OSPREY}\n")
    html = client.get("/emerging").get_data(as_text=True)
    assert "<th>Outreach</th>" in html and "Outreach Sent, 2026-10-20" in html
    in_sheet = client.get("/emerging?outreach=in_sheet").get_data(as_text=True)
    assert ticked(in_sheet) == [f"m{OSPREY}"] and "; in the outreach sheet;" in in_sheet
    assert ticked(client.get("/emerging?outreach=untouched").get_data(as_text=True)) == [f"m{WREN}"]
    page = client.get(f"/manager/m{OSPREY}").get_data(as_text=True)
    assert "<dt>Outreach</dt><dd>Outreach Sent, 2026-10-20</dd>" in page
    page = client.get(f"/manager/m{WREN}").get_data(as_text=True)
    assert "<dt>Outreach</dt><dd>Not in the outreach sheet</dd>" in page


def test_workbook_carries_outreach_columns(sheet_world, tmp_path, client):
    from openpyxl import load_workbook

    pull_file(sheet_world, tmp_path)
    ws = load_workbook(io.BytesIO(client.get("/emerging/export.xlsx").data))["Managers"]
    headers = [c.value for c in ws[1]]
    by_id = {r[headers.index("Manager ID")]: r for r in ws.iter_rows(min_row=2, values_only=True)}
    osprey = by_id[f"m{OSPREY}"]
    assert osprey[headers.index("Outreach")] == "Outreach Sent"
    assert osprey[headers.index("Last action")] == "2026-10-20"


@pytest.fixture
def quiet_refresh(monkeypatch):
    """run_refresh with catchup, adv and derive faked out."""
    from fundtracker import ingest

    monkeypatch.setattr(ingest, "catchup", lambda c, client: [])
    monkeypatch.setattr(ingest, "adv_due", lambda c: False)
    monkeypatch.setattr(derive, "run", lambda c: {"fund": 2, "manager": 2, "launch": 2})

    def run(db_path):
        from fundtracker.web import jobs

        lines = []
        conn = repo.connect(db_path)
        try:
            jobs.run_refresh(conn, "client", lines.append)
        finally:
            conn.close()
        return lines

    return run


def test_refresh_pulls_after_derive_and_logs_counts(sheet_world, tmp_path, monkeypatch, quiet_refresh):
    from fundtracker import config

    source = tmp_path / "outreach.csv"
    source.write_text(SHEET_CSV, encoding="utf-8")
    monkeypatch.setattr(config, "OUTREACH_CSV", source)
    lines = quiet_refresh(sheet_world)
    assert lines[-2] == "derive: 2 funds, 2 managers, 2 launches"
    assert lines[-1] == "outreach: 3 rows kept, 1 without a key"
    assert OSPREY in outreach_rows(sheet_world)


def test_refresh_survives_a_failed_pull(sheet_world, tmp_path, monkeypatch, quiet_refresh):
    from fundtracker import config

    monkeypatch.setattr(config, "OUTREACH_CSV", tmp_path / "missing.csv")
    assert quiet_refresh(sheet_world)[-1].startswith("outreach: pull failed (FileNotFoundError")


def test_refresh_without_the_setting_neither_pulls_nor_logs(sheet_world, monkeypatch, quiet_refresh):
    monkeypatch.setattr(outreach, "pull", lambda *a, **k: pytest.fail("pulled"))
    assert not any(line.startswith("outreach") for line in quiet_refresh(sheet_world))


def test_cli_outreach_pull(sheet_world, tmp_path, monkeypatch, capsys):
    from fundtracker import cli, config

    assert cli.main(["--db", sheet_world, "outreach", "pull"]) == 2
    source = tmp_path / "outreach.csv"
    source.write_text(SHEET_CSV, encoding="utf-8")
    monkeypatch.setattr(config, "OUTREACH_CSV", source)
    assert cli.main(["--db", sheet_world, "outreach", "pull"]) == 0
    assert capsys.readouterr().out.strip() == "outreach: 3 rows kept, 1 without a key"
