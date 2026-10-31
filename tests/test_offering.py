"""Offering status: open or lapsed per manager, total_remaining, the filter and the Remaining columns."""

import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from fundtracker import derive, traction
from fundtracker.db import repo
from fundtracker.web.app import create_app

sys.path.insert(0, str(Path(__file__).parent))
from test_derive import fixture, lone, person  # noqa: E402
from test_emerging import TODAY, ago, with_people  # noqa: E402

INDEFINITE = {"total_offering_amount_is_indefinite": True, "total_offering_amount_num": None,
              "total_remaining_num": None}


def definite(offering, sold):
    return {"total_offering_amount_is_indefinite": False, "total_offering_amount_num": offering,
            "total_amount_sold_num": sold, "total_remaining_num": offering - sold}


@pytest.fixture
def offerings(tmp_path):
    """Five hedge managers, each named by its general partner, filed at the ages given."""
    base = fixture("0002158817-26-000001")

    def gp(name):
        return person(base, 1, "N/A", name, entity=True)

    funds = [
        with_people(lone(base, 1, "Alder Fund LP", ago(3), offering=INDEFINITE), gp("Alder Capital LLC")),
        with_people(lone(base, 2, "Birch Fund LP", ago(14), offering=definite(10_000_000, 4_000_000)),
                    gp("Birch Capital LLC")),
        with_people(lone(base, 3, "Cedar Fund LP", ago(2), offering=definite(5_000_000, 5_000_000)),
                    gp("Cedar Capital LLC")),
        # Dogwood: an open indefinite fund and a lapsed definite one, under one general partner.
        with_people(lone(base, 4, "Dogwood Fund LP", ago(15), offering=definite(8_000_000, 5_000_000)),
                    gp("Dogwood Capital LLC")),
        with_people(lone(base, 5, "Dogwood Offshore Ltd", ago(1), offering=INDEFINITE), gp("Dogwood Capital LLC")),
        # Elm: two definite funds, one recent with money left; both remaining amounts count.
        with_people(lone(base, 6, "Elm Fund LP", ago(10), offering=definite(2_000_000, 500_000)),
                    gp("Elm Capital LLC")),
        with_people(lone(base, 7, "Elm Fund II LP", ago(9), offering=definite(1_000_000, 250_000)),
                    gp("Elm Capital LLC")),
    ]
    path = tmp_path / "offering.db"
    conn = repo.connect(path)
    for f in funds:
        repo.upsert_filing(conn, f)
    derive.run(conn, today=TODAY)
    conn.close()
    return str(path)


def manager(path, name):
    conn = repo.connect(path)
    try:
        return dict(conn.execute("SELECT * FROM manager WHERE display_name = ?", (name,)).fetchone())
    finally:
        conn.close()


@pytest.mark.parametrize("name, status, remaining", [
    ("Alder Capital LLC", "open", None),          # indefinite, filed 3 months ago
    ("Birch Capital LLC", "lapsed", 6_000_000),   # money left, but filed 14 months ago
    ("Cedar Capital LLC", "lapsed", 0),           # sold out
    ("Dogwood Capital LLC", "open", 3_000_000),   # one open fund is enough; definite rows only
    ("Elm Capital LLC", "open", 2_250_000),       # remaining summed over definite funds
])
def test_offering_status_and_total_remaining(offerings, name, status, remaining):
    m = manager(offerings, name)
    assert (m["offering_status"], m["total_remaining"]) == (status, remaining)


@pytest.mark.parametrize("start", [date(2026, 1, 1), date(2026, 2, 27), date(2028, 2, 10)])
def test_open_since_is_the_whole_month_age(start):
    """A latest filing is open from open_since(today) on: exactly where age_months is at most 13."""
    for today in (start + timedelta(days=n) for n in range(70)):
        since = derive.open_since(today)
        assert traction.age_months(since.isoformat(), today) <= derive.OPEN_MAX_AGE_MONTHS
        assert traction.age_months((since - timedelta(days=1)).isoformat(), today) > derive.OPEN_MAX_AGE_MONTHS


def test_open_since_on_the_planning_day():
    assert derive.open_since(date(2026, 10, 31)) == date(2025, 9, 1)


def test_derive_twice_gives_identical_manager_rows(offerings):
    conn = repo.connect(offerings)
    try:
        before = [tuple(r) for r in conn.execute("SELECT * FROM manager ORDER BY manager_id")]
        derive.run(conn, today=TODAY)
        assert [tuple(r) for r in conn.execute("SELECT * FROM manager ORDER BY manager_id")] == before
    finally:
        conn.close()


def test_lapsed_is_a_zero_point_label():
    facts = {"today": TODAY, "offering_status": "lapsed", "latest_filing_date": ago(14),
             "total_sold": 0, "total_investors": 0}
    score, reasons = traction.score_manager(facts)
    lapsed = [r for r in reasons if r["code"] == "lapsed"]
    assert lapsed == [{"code": "lapsed", "points": 0, "detail": f"latest filing {ago(14)}"}]
    assert traction.reason_phrase(lapsed[0]) == "no filing in 13 months"
    assert "lapsed" not in [r["code"] for r in traction.score_manager(dict(facts, offering_status="open"))[1]]


@pytest.fixture
def client(offerings):
    return create_app(connect=lambda: repo.connect_readonly(offerings), today=lambda: TODAY).test_client()


WIDE = f"first_seen_from={ago(24)}&first_seen_to={TODAY}"


def names(client, query):
    html = client.get(f"/emerging?{WIDE}&{query}").get_data(as_text=True)
    return sorted(re.findall(r'href="/manager/[^"]+">([^<]+)</a>', html)), html


def test_offering_filter_narrows(client):
    every, html = names(client, "")
    assert len(every) == 5 and "<th class=\"num\">Remaining</th>" in html
    assert ">6,000,000</td>" in html and ">2,250,000</td>" in html
    opened, html = names(client, "offering=open")
    assert opened == ["Alder Capital LLC", "Dogwood Capital LLC", "Elm Capital LLC"]
    assert "; offering open;" in html
    lapsed, html = names(client, "offering=lapsed")
    assert lapsed == ["Birch Capital LLC", "Cedar Capital LLC"]
    assert "no filing in 13 months" in html


def test_launches_table_and_launch_page_show_remaining(client):
    html = client.get(f"/launches?since={ago(24)}&until={TODAY}").get_data(as_text=True)
    assert "<th class=\"num\">Remaining</th>" in html
    assert ">6,000,000</td>" in html  # Birch's definite offering, from its first filing
    launch_id = re.search(r'href="/launch/([^"]+)">Birch Fund LP', html).group(1)
    page = client.get(f"/launch/{launch_id}").get_data(as_text=True)
    assert "<th class=\"num\">Remaining</th>" in page and ">6,000,000</td>" in page
