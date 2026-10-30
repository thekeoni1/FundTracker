"""The site's Emerging page, through the Flask test client on the emerging fixtures."""

import re
import sys
from pathlib import Path

import pytest

from fundtracker.db import repo
from fundtracker.traction import months_before
from fundtracker.web.app import create_app

sys.path.insert(0, str(Path(__file__).parent))
from test_emerging import TODAY, ago, world  # noqa: E402,F401


@pytest.fixture
def client(world):
    return create_app(connect=lambda: repo.connect_readonly(world), today=lambda: TODAY).test_client()


def managers(client, query=""):
    response = client.get(f"/emerging?{query}")
    assert response.status_code == 200
    return re.findall(r'href="/manager/[^"]+">([^<]+)</a>', response.get_data(as_text=True))


HERON = "Heron Capital Management LLC"


def test_default_window_is_18_to_6_months_of_hedge_managers(client):
    html = client.get("/emerging").get_data(as_text=True)
    assert f'value="{months_before(TODAY, 18)}"' in html and f'value="{months_before(TODAY, 6)}"' in html
    assert (f"2 managers</strong>: first seen {months_before(TODAY, 18)} to {months_before(TODAY, 6)}; "
            "Hedge Fund only; best score first.") in html
    names = managers(client)
    assert names[0] == HERON and len(names) == 2
    # Score badge with the reasons on hover, people, prior firm, place and the EDGAR link.
    assert 'title="raised money (+25); amount raised (+15)' in html
    assert "Jane Q. Doe" in html and "Bigfirm Capital LLC" in html and "Houston, TX" in html
    assert "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=7000000001" in html


@pytest.mark.parametrize("query, count, first", [
    ("type=all", 4, HERON),
    ("min_score=50", 1, HERON),
    ("prior_firm=with", 1, HERON),
    ("prior_firm=without", 1, None),
    ("raised=0", 1, None),
    ("raised=%3E10m", 1, HERON),
    ("state=TX", 2, HERON),
    ("state=NY", 0, None),
    ("platforms=show", 13, HERON),
    (f"first_seen_from={ago(36)}", 3, HERON),
    (f"first_seen_from={ago(36)}&first_seen_to={TODAY}", 4, HERON),
])
def test_each_filter_narrows(client, query, count, first):
    names = managers(client, query)
    assert len(names) == count
    if first:
        assert names[0] == first
    else:
        assert HERON not in names


def test_filters_compose_and_sort(client):
    raised = managers(client, "type=all&raised=%3E1m")
    assert len(raised) == 3 and HERON in raised
    assert len(managers(client, "type=all&raised=%3E1m&prior_firm=without")) == 2
    by_raised = managers(client, "type=all&sort=raised")
    assert by_raised.index(HERON) == 1  # after Granite's 900,000,000
    newest = managers(client, f"sort=first_seen&first_seen_to={TODAY}&first_seen_from={ago(36)}")
    assert newest[-1] != HERON and len(newest) == 4


def test_table_partial_and_push_url(client):
    response = client.get("/emerging/table?min_score=50", headers={"HX-Request": "true"})
    html = response.get_data(as_text=True)
    assert "<html" not in html and "<table>" in html and HERON in html
    assert response.headers["HX-Push-Url"] == "/emerging?min_score=50"


def test_nav_links_both_rankings(client):
    html = client.get("/emerging").get_data(as_text=True)
    assert 'href="/launches"' in html and 'href="/emerging" aria-current="page"' in html
