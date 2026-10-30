"""ingest.adv and fundtracker ingest adv with a fake client serving the saved page and trimmed zips."""

import logging
from pathlib import Path

import pytest
import requests

from fundtracker import cli, ingest
from fundtracker.db import repo
from fundtracker.edgar import adv
from fundtracker.edgar import client as client_module

FIXTURES = Path(__file__).parent / "fixtures" / "adv"
FOLDER = ("https://www.sec.gov/files/investment/data/other/"
          "information-about-registered-investment-advisers-exempt-reporting-advisers/")
PAGE = (FIXTURES / "adviser-data-page.html").read_text(encoding="utf-8")
REGISTERED = (FIXTURES / "ia100226_trimmed.zip").read_bytes()
EXEMPT = (FIXTURES / "ia100226-exempt_trimmed.zip").read_bytes()


class FakeResponse:
    def __init__(self, status=200, content=b"", text=""):
        self.status_code = status
        self.content = content
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, page=PAGE, files=None):
        self.page = page
        self.files = files if files is not None else {FOLDER + "ia100226.zip": REGISTERED,
                                                      FOLDER + "ia100226-exempt.zip": EXEMPT}
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if url == adv.ADV_PAGE:
            return FakeResponse(text=self.page)
        if url in self.files:
            return FakeResponse(content=self.files[url])
        return FakeResponse(status=404)


@pytest.fixture
def conn():
    c = repo.connect(":memory:")
    yield c
    c.close()


def counts(conn):
    return dict(conn.execute("SELECT kind, count(*) FROM adviser GROUP BY kind").fetchall())


def test_first_run_loads_both_files_and_the_second_loads_nothing(conn):
    client = FakeClient()
    assert ingest.adv(conn, client) == [("2026-10 ria", "ok", 60), ("2026-10 era", "ok", 60)]
    assert counts(conn) == {"ria": 60, "era": 60}
    malt = conn.execute("SELECT * FROM adviser WHERE crd = '337855'").fetchone()
    assert (malt["kind"], malt["as_of"], malt["employees"], malt["raum"], malt["any_hedge_funds"]) == (
        "ria", "2026-10", 2, 36_321_031, 1)
    log_row = repo.ingest_status(conn, ingest.ADV_SOURCE, "2026-10 era")
    assert (log_row["status"], log_row["n_rows"]) == ("ok", 60)

    again = FakeClient()
    assert ingest.adv(conn, again) == [("2026-10 ria", "skipped", 0), ("2026-10 era", "skipped", 0)]
    assert again.calls == [adv.ADV_PAGE]
    assert counts(conn) == {"ria": 60, "era": 60}


def test_a_month_with_only_one_file_replaces_that_kind_only(conn):
    ingest.adv(conn, FakeClient())
    november = FOLDER + "ia110226.zip"
    page = PAGE + f'<a href="{november}">November registered</a>'
    smaller = b"".join(  # the registered file with only its header and MALT's row
        line for line in _csv_lines(REGISTERED) if line.startswith(b'"SEC Region"') or b'"337855"' in line)
    client = FakeClient(page=page, files={november: smaller})
    assert ingest.adv(conn, client) == [("2026-11 ria", "ok", 1), ("2026-10 era", "skipped", 0)]
    assert counts(conn) == {"ria": 1, "era": 60}
    assert {r[0] for r in conn.execute("SELECT DISTINCT as_of || ' ' || kind FROM adviser")} == {
        "2026-11 ria", "2026-10 era"}


def test_a_failed_download_is_logged_and_keeps_the_old_rows(conn):
    ingest.adv(conn, FakeClient())
    november = FOLDER + "ia110226-exempt.zip"
    client = FakeClient(page=PAGE + f'<a href="{november}">x</a>', files={})
    assert ingest.adv(conn, client)[1] == ("2026-11 era", "error", 0)
    assert counts(conn) == {"ria": 60, "era": 60}
    assert repo.ingest_status(conn, ingest.ADV_SOURCE, "2026-11 era")["status"] == "error"


def test_adv_due_until_both_kinds_of_this_month_are_loaded(conn):
    from datetime import date

    ingest.adv(conn, FakeClient())
    assert not ingest.adv_due(conn, date(2026, 10, 30))
    assert ingest.adv_due(conn, date(2026, 11, 2))


def _csv_lines(zipped):
    return adv.csv_bytes(zipped).splitlines(keepends=True)


@pytest.fixture(autouse=True)
def reset_ingest_logger():
    yield
    progress = logging.getLogger("fundtracker.ingest")
    progress.handlers.clear()
    progress.propagate = True


def test_ingest_adv_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(client_module, "EdgarClient", FakeClient)
    db = str(tmp_path / "cli.db")
    assert cli.main(["--db", db, "ingest", "adv"]) == 0
    out = capsys.readouterr().out
    assert "2026-10 ria: 60 advisers loaded" in out and "2026-10 era: 60 advisers loaded" in out
    assert "120 advisers loaded" in out
    assert cli.main(["--db", db, "ingest", "adv"]) == 0
    out = capsys.readouterr().out
    assert "2026-10 ria: skipped (already loaded)" in out and "0 advisers loaded" in out
