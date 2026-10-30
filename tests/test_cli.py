import logging

import pytest

from fundtracker import cli
from fundtracker.edgar import client as client_module
from test_ingest_backfill import FakeClient


@pytest.fixture(autouse=True)
def reset_ingest_logger():
    yield
    progress = logging.getLogger("fundtracker.ingest")
    progress.handlers.clear()
    progress.propagate = True


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(client_module, "EdgarClient", lambda: FakeClient())
    monkeypatch.setattr(cli.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cli.config, "DATASETS_DIR", tmp_path / "datasets")
    return str(tmp_path / "cli.db")


def test_backfill_then_stats(db, capsys, monkeypatch, tmp_path):
    assert cli.main(["--db", db, "backfill"]) == 0
    out = capsys.readouterr().out
    assert "2 quarters loaded (402 filings), 0 already loaded, 0 failed" in out

    assert cli.main(["--db", db, "backfill", "--from", "2015q2"]) == 0
    assert "0 quarters loaded (0 filings), 1 already loaded" in capsys.readouterr().out

    assert cli.main(["--db", db, "stats", "--quarter", "2015q2"]) == 0
    out = capsys.readouterr().out
    assert "filings: 402" in out
    assert "dataset" in out and "data set quarters loaded: 2, 2010q2 to 2015q2" in out
    assert "2015q2: 201 data set filings" in out
    assert "ingest_log says 201" in out


def test_bad_quarter_is_rejected(db, capsys):
    with pytest.raises(SystemExit):
        cli.main(["--db", db, "backfill", "--from", "2015-2"])


def test_stats_on_empty_database(db, capsys):
    assert cli.main(["--db", db, "stats"]) == 0
    out = capsys.readouterr().out
    assert "filings: 0" in out and "data set quarters loaded: 0" in out


def test_previous_weekday():
    from datetime import date
    assert cli.previous_weekday(date(2026, 10, 29)) == date(2026, 10, 28)  # Thursday
    assert cli.previous_weekday(date(2026, 10, 26)) == date(2026, 10, 23)  # Monday -> Friday
    assert cli.previous_weekday(date(2026, 10, 25)) == date(2026, 10, 23)  # Sunday -> Friday


def test_ingest_daily_and_catchup_commands(tmp_path, monkeypatch, capsys):
    from datetime import date
    from test_ingest_daily import INDEX_TEXT, FakeClient as DailyClient, fixture_rows_only

    client = DailyClient({date(2026, 10, 27): fixture_rows_only(INDEX_TEXT)})
    monkeypatch.setattr(client_module, "EdgarClient", lambda: client)
    db = str(tmp_path / "d.db")

    assert cli.main(["--db", db, "ingest", "daily", "--date", "2026-10-27"]) == 0
    assert "2026-10-27: ok, 8 listed, 0 already stored, 8 loaded, 0 failed" in capsys.readouterr().out
    assert cli.main(["--db", db, "ingest", "daily", "--date", "2026-10-27"]) == 0
    assert "8 already stored, 0 loaded" in capsys.readouterr().out

    assert cli.main(["--db", db, "ingest", "catchup", "--until", "2026-10-28"]) == 0
    out = capsys.readouterr().out
    assert "2026-10-28: no_index" in out and "1 days fetched (0 ok, 1 no index, 0 error)" in out

    assert cli.main(["--db", db, "stats"]) == 0
    out = capsys.readouterr().out
    assert "daily index: last day 2026-10-28, 1 no_index days, 0 error days" in out
    assert "xml" in out
