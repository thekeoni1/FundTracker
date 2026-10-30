import dataclasses
from datetime import date

import pytest

from fundtracker import ingest
from fundtracker.db import repo
from fundtracker.edgar import index
from fundtracker.edgar.formd_xml import parse_formd
from test_ingest_daily import INDEX_TEXT, XML_FIXTURES, FakeClient, fixture_rows_only

TODAY = date(2026, 10, 29)  # a Thursday


def requested_days(client):
    return [url.rsplit("form.", 1)[1][:8] for url in client.index_calls]


@pytest.fixture
def conn(tmp_path):
    c = repo.connect(tmp_path / "t.db")
    yield c
    c.close()


def log_days(conn, days, status="ok"):
    with conn:
        for d in days:
            repo.log_ingest(conn, "daily", d, status, n_rows=0)


def test_refills_exactly_the_deleted_day(conn):
    log_days(conn, ["2026-10-26", "2026-10-27", "2026-10-28"])
    with conn:
        conn.execute("DELETE FROM ingest_log WHERE key = '2026-10-27'")
    client = FakeClient({})
    results = ingest.catchup(conn, client, today=TODAY)
    assert requested_days(client) == ["20261027"]
    assert [r.day for r in results] == ["2026-10-27"]
    assert repo.ingest_status(conn, "daily", "2026-10-27")["status"] == "no_index"


def test_refills_three_deleted_days_and_skips_weekends(conn):
    days = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23",
            "2026-10-26", "2026-10-27", "2026-10-28"]
    log_days(conn, days)
    with conn:
        conn.execute("DELETE FROM ingest_log WHERE key IN ('2026-10-22', '2026-10-23', '2026-10-26')")
    client = FakeClient({})
    ingest.catchup(conn, client, today=TODAY)
    assert requested_days(client) == ["20261022", "20261023", "20261026"]  # 24th and 25th are a weekend


def test_starts_after_the_last_data_set_filing_and_stops_at_yesterday(conn):
    acc = "0002158817-26-000001"
    f = parse_formd(XML_FIXTURES[acc], acc, filing_date="2026-10-21")
    repo.upsert_filing(conn, dataclasses.replace(f, filing=dataclasses.replace(f.filing, source="dataset")))
    client = FakeClient({})
    ingest.catchup(conn, client, today=TODAY)
    assert requested_days(client) == ["20261022", "20261023", "20261026", "20261027", "20261028"]
    # Nothing new to do on a second run.
    client.index_calls.clear()
    assert ingest.catchup(conn, client, today=TODAY) == []
    assert client.index_calls == []


def test_empty_database_starts_30_days_back(conn):
    client = FakeClient({})
    ingest.catchup(conn, client, today=TODAY)
    days = requested_days(client)
    assert days[0] == "20260929" and days[-1] == "20261028"
    assert all(date(int(d[:4]), int(d[4:6]), int(d[6:])).weekday() < 5 for d in days)
    assert "20261029" not in days  # never today by default


def test_error_days_are_retried_and_until_is_honoured(conn):
    log_days(conn, ["2026-10-26"])
    log_days(conn, ["2026-10-27"], status="error")
    client = FakeClient({})
    ingest.catchup(conn, client, until="2026-10-27", today=TODAY)
    assert requested_days(client) == ["20261027"]


def test_catchup_loads_filings_through_daily(conn):
    log_days(conn, ["2026-10-26"])
    client = FakeClient({date(2026, 10, 27): fixture_rows_only(INDEX_TEXT)})
    results = ingest.catchup(conn, client, today=TODAY)
    assert [(r.day, r.status, r.loaded) for r in results] == [
        ("2026-10-27", "ok", 8), ("2026-10-28", "no_index", 0)]
    assert conn.execute("SELECT count(*) FROM filing").fetchone()[0] == 8
    assert index.form_index_url("2026-10-27") in client.index_calls
