import dataclasses
from datetime import date
from pathlib import Path

import pytest
import requests

from fundtracker import ingest
from fundtracker.db import repo
from fundtracker.edgar import index
from fundtracker.edgar.formd_xml import parse_formd

FIXTURES = Path(__file__).parent / "fixtures"
INDEX_TEXT = (FIXTURES / "index" / "form.20261027.trimmed.idx").read_text(encoding="utf-8")
DAY = date(2026, 10, 27)

# WP1 XML fixtures listed in the 2026-10-27 index, by accession.
XML_FIXTURES = {p.stem: p.read_bytes() for p in (FIXTURES / "formd").glob("*.xml")}
ON_THE_DAY = {
    "0000919574-26-007205", "0002154220-26-000001", "0001012975-26-000984",
    "0001214659-26-013245", "0002161535-26-000001", "0001437749-26-033645",
    "0001741616-26-000005", "0002124823-26-000002",
}


class FakeResponse:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeClient:
    """Serves form indexes by date and primary_doc.xml for the WP1 fixtures; 404 for the rest."""

    def __init__(self, indexes):
        self.indexes = {index.form_index_url(d): text for d, text in indexes.items()}
        self.xml = {}
        for line in INDEX_TEXT.splitlines():
            for row in index.parse_form_index("---\n" + line):
                if row.accession in XML_FIXTURES:
                    self.xml[index.primary_doc_url(row.cik, row.accession)] = XML_FIXTURES[row.accession]
        self.index_calls, self.xml_calls = [], []

    def get(self, url, stream=False):
        self.index_calls.append(url)
        return FakeResponse(200, self.indexes[url]) if url in self.indexes else FakeResponse(404)

    def get_cached(self, url):
        self.xml_calls.append(url)
        if url not in self.xml:
            raise requests.HTTPError(f"HTTP 404 for {url}")
        return self.xml[url]


def fixture_rows_only(text):
    """The index header plus only the rows whose XML is a WP1 fixture."""
    lines = text.splitlines()
    dash = next(i for i, l in enumerate(lines) if l.startswith("---"))
    keep = [l for l in lines[dash + 1:] if any(a in l for a in ON_THE_DAY)]
    return "\n".join(lines[: dash + 1] + keep) + "\n"


@pytest.fixture
def conn(tmp_path):
    c = repo.connect(tmp_path / "t.db")
    yield c
    c.close()


def test_daily_loads_each_filing_once_and_a_second_run_fetches_nothing(conn):
    client = FakeClient({DAY: fixture_rows_only(INDEX_TEXT)})
    result = ingest.daily(conn, client, DAY)
    assert (result.status, result.listed, result.already_present, result.loaded) == ("ok", 8, 0, 8)
    assert result.errors == []
    assert len(client.xml_calls) == 8  # GEF fetched once although listed twice
    assert conn.execute("SELECT count(*) FROM filing").fetchone()[0] == 8
    gef = repo.get_filing(conn, "0002161535-26-000001")
    assert gef.filing.cik == "0002161535" and gef.filing.source == "xml"
    assert gef.filing.filing_date == "2026-10-27"
    assert len(gef.issuers) == 2
    log_row = repo.ingest_status(conn, "daily", "2026-10-27")
    assert (log_row["status"], log_row["n_rows"], log_row["error"]) == ("ok", 8, None)

    again = ingest.daily(conn, client, DAY)
    assert (again.status, again.already_present, again.loaded) == ("ok", 8, 0)
    assert len(client.xml_calls) == 8
    assert repo.ingest_status(conn, "daily", "2026-10-27")["status"] == "ok"


def test_failed_filings_are_logged_and_the_run_continues(conn):
    client = FakeClient({DAY: INDEX_TEXT})
    result = ingest.daily(conn, client, DAY)
    assert result.listed == 49 and result.loaded == 8
    assert len(result.errors) == 41
    assert all(acc and "404" in msg for acc, msg in result.errors)
    log_row = repo.ingest_status(conn, "daily", "2026-10-27")
    assert log_row["status"] == "ok" and log_row["n_rows"] == 8
    assert log_row["error"].startswith("41 of 49 filings failed: ")
    assert "and 36 more" in log_row["error"]

    # A rerun retries only the failures.
    client.xml_calls.clear()
    ingest.daily(conn, client, DAY)
    assert len(client.xml_calls) == 41


def test_nothing_loadable_is_an_error(conn):
    client = FakeClient({DAY: INDEX_TEXT})
    client.xml.clear()
    result = ingest.daily(conn, client, DAY)
    assert result.status == "error" and result.loaded == 0
    assert repo.ingest_status(conn, "daily", "2026-10-27")["status"] == "error"


def test_weekend_404_is_no_index(conn):
    client = FakeClient({})
    result = ingest.daily(conn, client, "2026-10-25")
    assert result.status == "no_index"
    row = repo.ingest_status(conn, "daily", "2026-10-25")
    assert (row["status"], row["n_rows"]) == ("no_index", 0)
    assert client.xml_calls == []


def test_server_error_on_the_index_is_logged(conn):
    class Broken(FakeClient):
        def get(self, url, stream=False):
            return FakeResponse(500)

    result = ingest.daily(conn, Broken({}), DAY)
    assert result.status == "error"
    assert "HTTP 500" in repo.ingest_status(conn, "daily", "2026-10-27")["error"]


def test_day_already_covered_by_a_data_set_fetches_nothing(conn):
    # Store every accession the day lists, as if a data set had loaded them.
    for row in index.formd_rows(index.parse_form_index(fixture_rows_only(INDEX_TEXT))):
        xml = XML_FIXTURES[row.accession]
        f = parse_formd(xml, row.accession, filing_date=row.date_filed)
        repo.upsert_filing(conn, dataclasses.replace(
            f, filing=dataclasses.replace(f.filing, source="dataset")))
    client = FakeClient({DAY: fixture_rows_only(INDEX_TEXT)})
    result = ingest.daily(conn, client, DAY)
    assert (result.listed, result.already_present, result.loaded) == (8, 8, 0)
    assert client.xml_calls == []
    assert conn.execute("SELECT count(*) FROM filing WHERE source = 'xml'").fetchone()[0] == 0


def test_filing_date_is_the_rows_date_filed_not_the_index_date(conn):
    text = fixture_rows_only(INDEX_TEXT).replace(
        "2154220     20261027", "2154220     20261026")  # Armada listed a day late
    client = FakeClient({DAY: text})
    ingest.daily(conn, client, DAY)
    assert repo.get_filing(conn, "0002154220-26-000001").filing.filing_date == "2026-10-26"
    assert repo.ingest_status(conn, "daily", "2026-10-27")["status"] == "ok"


# ingest_log rows are totals across runs: a rerun must not erase earlier counts.

@pytest.fixture
def clock(monkeypatch):
    """Make ingest._now return a new timestamp on every call."""
    ticks = iter(f"2026-10-30T00:00:{s:02d}+00:00" for s in range(60))
    monkeypatch.setattr(ingest, "_now", lambda: next(ticks))


def log_row(conn, day="2026-10-27"):
    return repo.ingest_status(conn, "daily", day)


def test_a_rerun_that_loads_nothing_leaves_the_row_identical(conn, clock):
    client = FakeClient({DAY: fixture_rows_only(INDEX_TEXT)})
    ingest.daily(conn, client, DAY)
    first = log_row(conn)
    assert (first["status"], first["n_rows"]) == ("ok", 8)
    ingest.daily(conn, client, DAY)
    assert log_row(conn) == first


def test_a_rerun_that_loads_more_adds_to_the_count_and_keeps_started_at(conn, clock):
    full = fixture_rows_only(INDEX_TEXT)
    lines = full.splitlines()
    early = "\n".join(l for l in lines if "0002124823-26-000002" not in l
                      and "0001741616-26-000005" not in l) + "\n"
    ingest.daily(conn, FakeClient({DAY: early}), DAY)
    first = log_row(conn)
    assert first["n_rows"] == 6

    # Two filings appear in the index after the first fetch.
    again = ingest.daily(conn, FakeClient({DAY: full}), DAY)
    assert again.loaded == 2
    second = log_row(conn)
    assert (second["status"], second["n_rows"]) == ("ok", 8)
    assert second["started_at"] == first["started_at"]
    assert second["finished_at"] > first["finished_at"]


def test_a_failed_rerun_keeps_the_earlier_ok_row(conn, clock):
    class Broken(FakeClient):
        def get(self, url, stream=False):
            return FakeResponse(500)

    ingest.daily(conn, FakeClient({DAY: fixture_rows_only(INDEX_TEXT)}), DAY)
    first = log_row(conn)
    assert ingest.daily(conn, Broken({}), DAY).status == "error"
    assert log_row(conn) == first


def test_a_no_index_rerun_leaves_the_row_and_an_error_row_is_replaced(conn, clock):
    ingest.daily(conn, FakeClient({}), "2026-10-25")
    weekend = log_row(conn, "2026-10-25")
    ingest.daily(conn, FakeClient({}), "2026-10-25")
    assert log_row(conn, "2026-10-25") == weekend

    failing = FakeClient({DAY: INDEX_TEXT})
    failing.xml.clear()
    ingest.daily(conn, failing, DAY)
    failed = log_row(conn)
    assert (failed["status"], failed["n_rows"]) == ("error", 0)
    ingest.daily(conn, FakeClient({DAY: fixture_rows_only(INDEX_TEXT)}), DAY)
    row = log_row(conn)
    assert (row["status"], row["n_rows"], row["error"]) == ("ok", 8, None)
