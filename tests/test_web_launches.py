"""The site's Launches page and its filters, through the Flask test client on the WP1 fixtures."""

import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest

from fundtracker import derive
from fundtracker.db import repo
from fundtracker.web import links
from fundtracker.web.app import create_app

sys.path.insert(0, str(Path(__file__).parent))
from test_derive import all_fixtures  # noqa: E402

TODAY = date(2026, 10, 30)


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("web") / "derived.db"
    conn = repo.connect(path)
    for f in all_fixtures():
        repo.upsert_filing(conn, f)
    derive.run(conn, today=TODAY)
    conn.close()
    return path


@pytest.fixture
def client(db_path):
    app = create_app(connect=lambda: repo.connect_readonly(db_path), today=lambda: TODAY)
    return app.test_client()


def launch_ids(html):
    return re.findall(r'href="/launch/([^"]+)"', html)


AXIARIS = "0002158817-26-000001"
ARROGATE = "0000919574-26-007205"
CALLODINE = "0001741616-26-000005"
ARMADA = "0002154220-26-000001"
PMSF = "0002124823-26-000002"


def get(client, url):
    response = client.get(url)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_root_redirects_to_launches(client):
    response = client.get("/")
    assert response.status_code == 302 and response.headers["Location"] == "/launches"


def test_page_renders_last_seven_days_of_hedge_launches_best_first(client):
    html = get(client, "/launches")
    assert html.startswith("<!doctype html>")
    assert launch_ids(html) == [AXIARIS, ARROGATE, CALLODINE]
    assert "3 launches</strong>: filed 2026-10-23 to 2026-10-30; Hedge Fund only" in html
    assert 'value="2026-10-23"' in html and "htmx-2.0.3.min.js" in html
    # The status bar, and the score badge whose hover carries the reasons.
    assert "Daily index loaded to never" in html and "12 filings" in html
    assert 'title="new manager: no key seen before (+40); ' in html
    assert links.edgar_filing("0002158817", AXIARIS) in html


@pytest.mark.parametrize("query, expected", [
    ("type=all", [AXIARIS, ARROGATE, "0001012975-26-000984", "0002161535-26-000001", ARMADA, PMSF,
                  CALLODINE]),
    ("min_score=90", [AXIARIS, ARROGATE]),
    ("since=2026-10-28", [AXIARIS]),
    ("since=2026-10-01&until=2026-10-27", [ARROGATE, CALLODINE]),
    ("since=2d", [AXIARIS]),
    ("rule=c", [AXIARIS]),
    ("rule=b", [ARROGATE, CALLODINE]),
    ("state=TX", [AXIARIS]),
    ("state=tx&state=MA", [AXIARIS, CALLODINE]),
    ("state=TX,+MA", [AXIARIS, CALLODINE]),
    ("min_inv=%3C100k", [ARROGATE]),
    ("min_inv=100k-1m", [AXIARIS]),
    ("min_inv=%3E1m", [CALLODINE]),
    ("raised=0", [AXIARIS, ARROGATE]),
    ("raised=%3E10m", [CALLODINE]),
    ("sort=raised", [CALLODINE, AXIARIS, ARROGATE]),
    ("sort=date", [AXIARIS, ARROGATE, CALLODINE]),
])
def test_each_filter_narrows(client, query, expected):
    assert launch_ids(get(client, f"/launches?{query}")) == expected


def test_two_filters_compose(client):
    every_fl = launch_ids(get(client, "/launches?type=all&state=FL"))
    assert every_fl == [ARROGATE, ARMADA, PMSF]
    assert launch_ids(get(client, "/launches?type=all&state=FL&min_score=70")) == [ARROGATE, ARMADA]
    assert launch_ids(get(client, "/launches?type=all&state=FL&min_score=70&raised=%3E0")) == [ARMADA]


def test_values_not_understood_fall_back_to_the_defaults(client):
    html = get(client, "/launches?since=yesterday&min_score=lots&type=pe&sort=name&state=Texas")
    assert launch_ids(html) == [AXIARIS, ARROGATE, CALLODINE]


def test_the_summary_names_the_active_filters(client):
    html = get(client, "/launches?min_score=60&rule=b&state=MA&raised=%3E10m&platforms=show")
    assert ("1 launch</strong>: filed 2026-10-23 to 2026-10-30; score 60 or more; Hedge Fund only; "
            "Rule 506(b); raised 10,000,000 or more; organisations on many funds shown; state MA; "
            "best score first.") in html


def test_platforms_are_hidden_by_default(db_path, tmp_path):
    conn = repo.connect(db_path)
    reasons = json.loads(conn.execute("SELECT newness_reasons FROM launch WHERE launch_id = ?",
                                      (CALLODINE,)).fetchone()[0])
    try:
        with conn:
            conn.execute("UPDATE launch SET newness_reasons = ? WHERE launch_id = ?", (json.dumps(
                reasons + [{"code": "platform", "points": 0, "detail": "Bigco LLC (400 funds)"}]),
                CALLODINE))
        client = create_app(connect=lambda: repo.connect_readonly(db_path), today=lambda: TODAY).test_client()
        assert launch_ids(get(client, "/launches")) == [AXIARIS, ARROGATE]
        shown = get(client, "/launches?platforms=show")
        assert launch_ids(shown) == [AXIARIS, ARROGATE, CALLODINE]
        assert "Bigco LLC (400 funds)" in shown
    finally:
        with conn:
            conn.execute("UPDATE launch SET newness_reasons = ? WHERE launch_id = ?",
                         (json.dumps(reasons), CALLODINE))
        conn.close()


def test_table_partial_returns_only_the_table(client):
    response = client.get("/launches/table?type=all&min_score=90")
    html = response.get_data(as_text=True)
    assert "<html" not in html and "<form" not in html and "<table>" in html
    assert launch_ids(html) == [AXIARIS, ARROGATE, "0001012975-26-000984", "0002161535-26-000001"]
    assert "HX-Push-Url" not in response.headers


def test_htmx_request_moves_the_address_bar_to_the_page_url(client):
    response = client.get("/launches/table?type=all&min_score=90", headers={"HX-Request": "true"})
    assert response.headers["HX-Push-Url"] == "/launches?type=all&min_score=90"


def test_the_form_targets_the_partial(client):
    html = get(client, "/launches")
    assert 'hx-get="/launches/table"' in html and 'hx-target="#results"' in html
    assert '<div id="results">' in html


def test_the_site_never_writes(client, db_path):
    before = Path(db_path).stat().st_mtime_ns
    get(client, "/launches?type=all")
    assert Path(db_path).stat().st_mtime_ns == before


def test_links():
    assert links.edgar_company("2158817") == (
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0002158817&type=D"
        "&dateb=&owner=include&count=40")
    assert links.edgar_filing("0002158817", AXIARIS) == (
        "https://www.sec.gov/Archives/edgar/data/2158817/000215881726000001/")
    assert links.web_search("Axiaris  Capital & Co") == (
        "https://www.google.com/search?q=%22Axiaris+Capital+%26+Co%22")
    assert links.linkedin_people("Andrew Murtland", "Axiaris Capital Management LLC") == (
        "https://www.linkedin.com/search/results/people/?keywords="
        "Andrew+Murtland+Axiaris+Capital+Management+LLC")
    assert links.linkedin_people("Andrew Murtland") == (
        "https://www.linkedin.com/search/results/people/?keywords=Andrew+Murtland")
    assert links.iapd_search("Axiaris Capital Management LLC") == (
        "https://adviserinfo.sec.gov/search/genericsearch/firmgrid?query=Axiaris+Capital+Management+LLC")
