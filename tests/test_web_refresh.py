"""The Refresh job and its routes, with fake runners: no network, no real catchup or derive."""

import sys
import threading
from pathlib import Path

import pytest

from fundtracker.db import repo
from fundtracker.web import jobs
from fundtracker.web.app import create_app

sys.path.insert(0, str(Path(__file__).parent))
from test_web_launches import TODAY, db_path  # noqa: E402,F401

HX = {"HX-Request": "true"}


class Gate:
    """A runner that logs a line, then waits until released."""

    def __init__(self, fail=None):
        self.release = threading.Event()
        self.entered = threading.Event()
        self.fail = fail

    def __call__(self, log):
        log("catchup: 0 days fetched")
        self.entered.set()
        assert self.release.wait(5)
        if self.fail:
            raise RuntimeError(self.fail)
        log("derive: 12 funds")


def test_start_runs_once_at_a_time_and_ends_done_with_its_lines():
    gate = Gate()
    refresher = jobs.Refresher(gate)
    assert refresher.snapshot()["state"] == "idle"
    assert refresher.start() is True
    assert gate.entered.wait(5)
    assert refresher.snapshot()["state"] == "running"
    assert refresher.start() is False
    gate.release.set()
    refresher.wait(5)
    job = refresher.snapshot()
    assert job["state"] == "done" and job["error"] is None and job["finished_at"] >= job["started_at"]
    assert [line.split(" ", 1)[1] for line in job["lines"]] == ["catchup: 0 days fetched", "derive: 12 funds"]


def test_a_runner_exception_shows_failed_with_the_message():
    gate = Gate(fail="EDGAR answered 503")
    gate.release.set()
    refresher = jobs.Refresher(gate)
    refresher.start()
    refresher.wait(5)
    job = refresher.snapshot()
    assert job["state"] == "failed" and job["error"] == "RuntimeError: EDGAR answered 503"
    assert job["lines"][-1].endswith("failed: RuntimeError: EDGAR answered 503")
    assert refresher.start() is True  # a failed job can be run again
    refresher.wait(5)


def test_lines_are_capped():
    refresher = jobs.Refresher(lambda log: [log(f"day {n}") for n in range(jobs.MAX_LINES + 50)])
    refresher.start()
    refresher.wait(5)
    lines = refresher.snapshot()["lines"]
    assert len(lines) == jobs.MAX_LINES and lines[-1].endswith(f"day {jobs.MAX_LINES + 49}")


@pytest.fixture
def gated(db_path):
    gate = Gate()
    refresher = jobs.Refresher(gate)
    app = create_app(connect=lambda: repo.connect_readonly(db_path), today=lambda: TODAY,
                     refresher=refresher)
    yield app.test_client(), gate, refresher
    gate.release.set()
    refresher.wait(5)


def test_refresh_route_starts_the_job_and_the_partial_polls_while_running(gated):
    client, gate, refresher = gated
    html = client.get("/launches").get_data(as_text=True)
    assert 'hx-post="/refresh"' in html and "every 2s" not in html
    response = client.post("/refresh", headers=HX)
    assert response.status_code == 200 and gate.entered.wait(5)
    status = client.get("/refresh/status").get_data(as_text=True)
    assert status.startswith('<div class="status" id="status" hx-get="/refresh/status" hx-trigger="every 2s"')
    assert "catchup: 0 days fetched" in status and " disabled" in status
    # A second click while running changes nothing.
    client.post("/refresh", headers=HX)
    assert refresher.snapshot()["state"] == "running"
    gate.release.set()
    refresher.wait(5)
    status = client.get("/refresh/status").get_data(as_text=True)
    assert "every 2s" not in status and "Refresh finished" in status and "derive: 12 funds" in status


def test_refresh_shows_a_failure(db_path):
    gate = Gate(fail="no route to host")
    gate.release.set()
    refresher = jobs.Refresher(gate)
    client = create_app(connect=lambda: repo.connect_readonly(db_path), refresher=refresher).test_client()
    client.post("/refresh", headers=HX)
    refresher.wait(5)
    status = client.get("/refresh/status").get_data(as_text=True)
    assert "Refresh failed: RuntimeError: no route to host" in status and "every 2s" not in status


def test_the_real_runner_does_catchup_then_derive(db_path, monkeypatch):
    from fundtracker.edgar import client as edgar_client

    calls = []

    def catchup(conn, client):
        calls.append("catchup")
        jobs.logging.getLogger("fundtracker.ingest").info("2026-10-29: ok, 40 listed")
        return []

    def run(conn):
        calls.append("derive")
        return {"fund": 12, "manager": 12, "launch": 10}

    monkeypatch.setattr(edgar_client, "EdgarClient", lambda: object())
    monkeypatch.setattr(jobs.ingest, "catchup", catchup)
    monkeypatch.setattr(jobs.derive, "run", run)
    lines = []
    jobs.refresh_runner(db_path)(lines.append)
    assert calls == ["catchup", "derive"]
    assert lines == ["catchup: loading every weekday not yet loaded", "2026-10-29: ok, 40 listed",
                     "catchup: 0 days fetched, 0 filings loaded, 0 days failed",
                     "derive: rebuilding funds, managers and launches (about four minutes)",
                     "derive: 12 funds, 12 managers, 10 launches"]
    assert not jobs.logging.getLogger("fundtracker.ingest").handlers


def test_refresh_needs_the_htmx_header(gated):
    client, _gate, refresher = gated
    assert client.post("/refresh").status_code == 400
    assert refresher.snapshot()["state"] == "idle"


def test_serve_runs_the_site_on_localhost_against_the_given_database(db_path, monkeypatch, capsys):
    from flask import Flask

    from fundtracker import cli

    seen = {}

    def run(app, **kwargs):
        seen.update(kwargs)
        seen["page"] = app.test_client().get("/launches").status_code

    monkeypatch.setattr(Flask, "run", run)
    assert cli.main(["--db", str(db_path), "serve", "--port", "8123", "--no-refresh"]) == 0
    assert seen == {"host": "127.0.0.1", "port": 8123, "debug": False, "threaded": True,
                    "use_reloader": False, "page": 200}
    assert capsys.readouterr().out.startswith("FundTracker on http://127.0.0.1:8123/ (database ")
