"""fundtracker refresh, the staleness rule and serve's refresh on launch, with fakes: no network."""

from datetime import date

import flask
import pytest

from fundtracker import cli, derive, ingest
from fundtracker.db import repo
from fundtracker.edgar import client as client_module
from fundtracker.web import jobs


@pytest.fixture
def conn(tmp_path):
    conn = repo.connect(tmp_path / "refresh.db")
    yield conn
    conn.close()


def loaded(conn, *days, status="ok"):
    for day in days:
        repo.log_ingest(conn, ingest.DAILY_SOURCE, day, status)
    conn.commit()


def test_an_empty_database_is_stale(conn):
    assert jobs.is_stale(conn, date(2026, 10, 30))


def test_stale_until_the_previous_weekday_is_loaded(conn):
    loaded(conn, "2026-10-28")
    assert jobs.is_stale(conn, date(2026, 10, 30))  # Friday needs Thursday
    loaded(conn, "2026-10-29")
    assert not jobs.is_stale(conn, date(2026, 10, 30))


def test_friday_is_current_across_the_weekend(conn):
    loaded(conn, "2026-10-30")
    for today in (date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2)):  # Saturday to Monday
        assert not jobs.is_stale(conn, today)
    assert jobs.is_stale(conn, date(2026, 11, 3))


def test_a_holiday_logged_no_index_counts_as_current(conn):
    loaded(conn, "2026-11-25")
    assert jobs.is_stale(conn, date(2026, 11, 27))  # Thanksgiving, 26th, not yet logged
    loaded(conn, "2026-11-26", status="no_index")
    assert not jobs.is_stale(conn, date(2026, 11, 27))


def test_a_failed_day_does_not_count(conn):
    loaded(conn, "2026-10-28")
    loaded(conn, "2026-10-29", status="error")
    assert jobs.is_stale(conn, date(2026, 10, 30))


def test_run_refresh_runs_catchup_then_derive_and_logs_both(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(ingest, "catchup", lambda c, client: calls.append(("catchup", client)) or [])
    monkeypatch.setattr(derive, "run", lambda c: calls.append(("derive", None))
                        or {"fund": 3, "manager": 2, "launch": 1})
    lines = []
    jobs.run_refresh(conn, "client", lines.append)
    assert calls == [("catchup", "client"), ("derive", None)]
    assert lines[1] == "catchup: 0 days fetched, 0 filings loaded, 0 days failed"
    assert lines[-1] == "derive: 3 funds, 2 managers, 1 launches"


def test_refresh_command_calls_the_shared_function(tmp_path, monkeypatch, capsys):
    seen = []

    def fake(conn, client, log):
        seen.append(client)
        log("catchup: 0 days fetched")

    monkeypatch.setattr(jobs, "run_refresh", fake)
    monkeypatch.setattr(client_module, "EdgarClient", lambda: "fake client")
    assert cli.main(["--db", str(tmp_path / "cli.db"), "refresh"]) == 0
    assert seen == ["fake client"]
    out = capsys.readouterr().out
    assert "catchup: 0 days fetched" in out and "refresh done" in out


def test_refresh_command_fails_when_a_day_fails(tmp_path, monkeypatch, capsys):
    def fake(conn, client, log):
        raise RuntimeError("catchup failed on 2026-10-29")

    monkeypatch.setattr(jobs, "run_refresh", fake)
    monkeypatch.setattr(client_module, "EdgarClient", lambda: None)
    assert cli.main(["--db", str(tmp_path / "cli.db"), "refresh"]) == 1
    assert "failed: catchup failed on 2026-10-29" in capsys.readouterr().out


@pytest.fixture
def serve(tmp_path, monkeypatch):
    """Run cmd_serve without a server: record refresher starts instead of starting threads."""
    starts = []
    monkeypatch.setattr(jobs.Refresher, "start", lambda self: starts.append(self) or True)
    monkeypatch.setattr(flask.Flask, "run", lambda self, **kwargs: None)
    path = tmp_path / "serve.db"

    def run(*extra, last_daily=None):
        if last_daily:
            c = repo.connect(path)
            loaded(c, last_daily)
            c.close()
        assert cli.main(["--db", str(path), "serve", *extra]) == 0
        return starts

    return run


def test_serve_refreshes_a_stale_database(serve, capsys):
    assert len(serve()) == 1
    assert "refreshing in the background" in capsys.readouterr().out


def test_serve_no_refresh_skips_it(serve):
    assert serve("--no-refresh") == []


def test_serve_does_not_refresh_a_current_database(serve):
    assert serve(last_daily=date.today().isoformat()) == []
