"""Form ADV adviser fields on the Emerging page, its export and the manager page."""

import io
import re
import sys
from pathlib import Path

import pytest

from fundtracker import derive, traction
from fundtracker.db import repo
from fundtracker.edgar import adv
from fundtracker.edgar.adv import AdviserRow
from fundtracker.web.app import create_app

sys.path.insert(0, str(Path(__file__).parent))
from test_derive import fixture, lone  # noqa: E402
from test_emerging import TODAY, ago, world  # noqa: E402,F401

ADV = Path(__file__).parent / "fixtures" / "adv"
# Manager IDs: Teal and Malt are both named after the fixture's signer on the page.
HERON, TEAL, MALT = "m7000000001", "m7000000003", "m7000000060"


@pytest.fixture
def enriched(world):
    """The emerging world plus a fund on MALT's phone, the trimmed advisers, a registered
    Heron and an exempt adviser on Teal's phone, derived again."""
    conn = repo.connect(world)
    base = fixture("0002158817-26-000001")
    malt = lone(base, 60, "Malt Fund LP", ago(8), offering={"total_amount_sold_num": 3_000_000, "n_investors": 4})
    malt = malt.__class__(**{**malt.__dict__, "issuers": tuple(
        i.__class__(**{**i.__dict__, "phone": "646.450.5521"}) for i in malt.issuers)})
    repo.upsert_filing(conn, malt)
    with conn:
        registered = list(adv.read_adv_csv((ADV / "ia100226_trimmed.zip").read_bytes(), "ria"))
        registered.append(AdviserRow(crd="990001", kind="ria", name="HERON CAPITAL MANAGEMENT, LLC",
                                     website="www.heroncap.com", employees=4, raum=50_000_000,
                                     any_hedge_funds=True, n_hedge_funds=2, n_private_funds=2,
                                     latest_filing_date="2026-04-01"))
        repo.replace_advisers(conn, "ria", "2026-10", registered)
        repo.replace_advisers(conn, "era", "2026-10", [AdviserRow(
            crd="990002", kind="era", name="TEAL ADVISERS LP", phone="999-555-0003",
            any_hedge_funds=True, n_hedge_funds=1)])
    derive.run(conn)
    conn.close()
    return world


@pytest.fixture
def client(enriched):
    return create_app(connect=lambda: repo.connect_readonly(enriched), today=lambda: TODAY).test_client()


def managers(client, query=""):
    response = client.get(f"/emerging?{query}")
    assert response.status_code == 200
    return re.findall(r'href="/manager/([^"]+)">', response.get_data(as_text=True))


def test_emerging_page_shows_the_adviser_columns(client):
    html = client.get("/emerging").get_data(as_text=True)
    for header in ("Website", "Adviser", "Employees", "RAUM", "SEC hedge funds"):
        assert f">{header}</th>" in html
    assert 'href="https://www.heroncap.com"' in html and ">www.heroncap.com</a>" in html
    assert 'href="https://adviserinfo.sec.gov/firm/summary/990001"' in html and ">RIA, high</a>" in html
    assert ">50,000,000</td>" in html
    assert 'href="https://adviserinfo.sec.gov/firm/summary/337855"' in html and ">36,321,031</td>" in html
    assert "files Form ADV" in html  # the zero-point label in the score's reasons


@pytest.mark.parametrize("query, expected", [
    ("type=all&adviser=linked", {HERON, MALT, TEAL}),
    ("type=all&adviser=ria", {HERON, MALT}),
    ("type=all&adviser=era", {TEAL}),
    ("type=all&employees=1-5", {HERON, MALT}),
    ("type=all&employees=6-20", set()),
    ("type=all&adviser=ria&employees=1-5&min_score=60", {HERON}),
])
def test_adviser_and_employee_filters(client, query, expected):
    assert set(managers(client, query)) == expected


def test_no_adviser_filter_keeps_the_rest(client):
    rest = set(managers(client, "type=all&adviser=none"))
    assert rest and not rest & {HERON, MALT, TEAL}
    assert len(rest) + 3 == len(managers(client, "type=all"))


def test_filters_are_in_the_summary(client):
    html = client.get("/emerging?adviser=era&employees=21%2B").get_data(as_text=True)
    assert "exempt reporting adviser (ERA); 21 or more employees" in html


def test_export_matches_the_filtered_page(client):
    from openpyxl import load_workbook

    response = client.get("/emerging/export.xlsx?type=all&adviser=ria")
    ws = load_workbook(io.BytesIO(response.data))["Managers"]
    header = [c.value for c in ws[1]]
    rows = [dict(zip(header, [c.value for c in r])) for r in ws.iter_rows(min_row=2)]
    assert {r["Manager ID"] for r in rows} == set(managers(client, "type=all&adviser=ria"))
    malt = next(r for r in rows if r["Adviser CRD"] == "337855")
    assert (malt["Adviser"], malt["Employees"], malt["RAUM"], malt["SEC hedge funds"], malt["Website"]) == (
        "RIA, high", 2, 36_321_031, 2, None)


def manager_page(client, manager_id):
    return client.get(f"/manager/{manager_id}").get_data(as_text=True)


def test_malt_manager_page_shows_its_adviser(client):
    html = manager_page(client, MALT)
    assert "MALT PARTNERS MANAGEMENT LLC (registered, RIA)" in html
    assert "high confidence, by phone" in html
    assert "<dd>2 (2 advisory)</dd>" in html and "<dd>36,321,031</dd>" in html
    assert 'href="https://adviserinfo.sec.gov/firm/summary/337855"' in html
    assert "none filed" in html and "data as of 2026-10" in html


def test_an_exempt_adviser_reports_no_headcount(client):
    html = manager_page(client, TEAL)
    assert "TEAL ADVISERS LP (exempt reporting, ERA)" in html
    assert "not reported (ERAs do not file Item 5)" in html


def test_possible_advisers_are_listed_when_unlinked(enriched):
    conn = repo.connect(enriched)
    with conn:
        repo.replace_advisers(conn, "era", "2026-10", [
            AdviserRow(crd=f"99100{i}", kind="era", name=f"SHARED {i} LLC", phone="999-555-0003")
            for i in range(3)])
    derive.run(conn)
    conn.close()
    client = create_app(connect=lambda: repo.connect_readonly(enriched), today=lambda: TODAY).test_client()
    html = manager_page(client, TEAL)
    assert "Possible advisers" in html
    assert all(f'href="https://adviserinfo.sec.gov/firm/summary/99100{i}"' in html for i in range(3))
    assert 'firm/summary/99100' not in client.get("/emerging?type=all").get_data(as_text=True)


def test_adviser_label_scores_zero():
    score, reasons = traction.score_manager({"today": TODAY, "adviser_kind": "ria",
                                             "adviser_confidence": "medium", "employees": 3})
    adviser = next(r for r in reasons if r["code"] == "adviser")
    assert adviser == {"code": "adviser", "points": 0, "detail": "RIA, medium confidence, 3 employees"}
