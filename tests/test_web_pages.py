"""The site's launch, manager, person and search pages, through the Flask test client."""

import re
import sys
from pathlib import Path

import pytest
from markupsafe import escape

from fundtracker.db import repo
from fundtracker.web import links
from fundtracker.web.app import create_app

sys.path.insert(0, str(Path(__file__).parent))
from test_emerging import TODAY, world  # noqa: E402,F401
from test_web_launches import AXIARIS, db_path  # noqa: E402,F401

AXIARIS_MANAGER = "m0002158817"


@pytest.fixture
def client(db_path):
    return create_app(connect=lambda: repo.connect_readonly(db_path), today=lambda: TODAY).test_client()


@pytest.fixture
def world_client(world):
    return create_app(connect=lambda: repo.connect_readonly(world), today=lambda: TODAY).test_client()


def page(client, url, status=200):
    response = client.get(url)
    assert response.status_code == status
    return response.get_data(as_text=True)


def test_launch_page_shows_funds_people_signers_and_reasons(client):
    html = page(client, f"/launch/{AXIARIS}")
    assert "<h1>Axiaris Capital Management Fund, LP</h1>" in html
    assert "0002158817" in html and "06c, 3C, 3C.1" in html and "100,000" in html
    assert "yet to occur" in html
    assert f'href="/manager/{AXIARIS_MANAGER}">Axiaris Capital Management LLC</a>' in html
    assert 'href="/person/ANDREW%20CUNNINGHAM%20MURTLAND">Andrew Cunningham Murtland</a>' in html
    assert links.linkedin_people("Andrew Cunningham Murtland", "Axiaris Capital Management LLC") in html
    assert "<td>new manager: no key seen before</td><td class=\"num\">+40</td>" in html
    assert links.edgar_filing("0002158817", AXIARIS) in html


def test_manager_page_lists_every_fund_and_filing(client, db_path):
    html = page(client, f"/manager/{AXIARIS_MANAGER}")
    assert "<h1>Axiaris Capital Management LLC</h1>" in html
    conn = repo.connect_readonly(db_path)
    funds = [r[0] for r in conn.execute(
        "SELECT cik FROM fund_manager WHERE manager_id = ?", (AXIARIS_MANAGER,))]
    filings = [r[0] for r in conn.execute(
        f"SELECT accession FROM filing WHERE cik IN ({', '.join('?' * len(funds))})", funds)]
    conn.close()
    assert len(repo.filings_timeline(repo.connect_readonly(db_path), AXIARIS_MANAGER)) == len(filings)
    for accession in filings:
        assert accession.replace("-", "") in html
    assert links.iapd_search("Axiaris Capital Management LLC") in html
    assert links.web_search("Axiaris Capital Management LLC") in html
    assert str(escape(links.edgar_company("0002158817"))) in html
    assert 'href="/person/ANDREW%20CUNNINGHAM%20MURTLAND"' in html


def test_manager_timeline_shows_the_raise_history(world_client, world):
    conn = repo.connect_readonly(world)
    heron = conn.execute("SELECT manager_id FROM manager WHERE display_name LIKE 'Heron%'").fetchone()[0]
    conn.close()
    html = page(world_client, f"/manager/{heron}")
    assert "Heron Master Fund LP" in html and "Heron Offshore Fund Ltd" in html
    assert "18,000,000" in html and "Bigfirm Capital LLC" in html


def test_person_page_lists_every_fund_the_name_appears_on(world_client):
    html = page(world_client, "/search?q=jane")
    [norm] = re.findall(r'href="/person/([^"]+)">', html)
    html = page(world_client, f"/person/{norm}")
    assert "<h1>Jane Q. Doe</h1>" in html
    assert "Heron Master Fund LP" in html and "Bigfirm Vehicle 0 LP" in html
    assert len(re.findall(r"<td>(Heron|Bigfirm)[^<]*</td>", html)) == 11
    assert "Executive Officer" in html and 'href="/manager/' in html


def test_search_finds_funds_managers_and_people(client):
    html = page(client, "/search?q=axiaris")
    assert f'href="/manager/{AXIARIS_MANAGER}">Axiaris Capital Management LLC</a>' in html
    assert "Axiaris Capital Management Fund, LP" in html
    html = page(client, "/search?q=murtland")
    assert 'href="/person/ANDREW%20CUNNINGHAM%20MURTLAND">ANDREW CUNNINGHAM MURTLAND</a>' in html
    assert "<h2>" not in page(client, "/search")


@pytest.mark.parametrize("url", ["/launch/0000000000-00-000000", "/manager/m9999999999",
                                 "/person/NOBODY%20AT%20ALL"])
def test_unknown_ids_are_a_clean_404(client, url):
    html = page(client, url, status=404)
    assert "<h1>Not found</h1>" in html and 'id="status"' in html
