"""fundtracker digest: launches on the WP1 fixtures, emerging changes on synthetic managers."""

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

from fundtracker import cli, digest
from fundtracker.db import repo

sys.path.insert(0, str(Path(__file__).parent))
from test_emerging import TODAY as EMERGING_TODAY, world  # noqa: E402,F401
from test_web_launches import TODAY, db_path  # noqa: E402,F401


@pytest.fixture
def fixture_conn(db_path, tmp_path):
    """A writable copy of the derived fixture database (the digest records its state)."""
    path = tmp_path / "digest.db"
    with sqlite3.connect(db_path) as src, sqlite3.connect(path) as dst:
        src.backup(dst)
    conn = repo.connect(path)
    yield conn
    conn.close()


def test_launches_scoring_70_or_more_with_edgar_links(fixture_conn, tmp_path):
    out = tmp_path / "d.md"
    assert digest.write(fixture_conn, out, TODAY, "2026-10-23") == (2, 0)
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# FundTracker digest, 2026-10-30\n")
    assert "## Hedge fund launches since 2026-10-23, score 70 or more" in text
    assert "| 2026-10-28 | 100 | Axiaris Capital Management Fund, LP |" in text
    assert "Arrogate Capital Fund, LP" in text and "Callodine" not in text  # Callodine scores 60
    assert "https://www.sec.gov/Archives/edgar/data/" in text
    assert "First digest: recorded" in text
    assert "Data: daily index loaded to none" in text and "Stale: run fundtracker refresh." in text


def test_default_path_is_dated_in_the_export_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(digest.config, "EXPORT_DIR", tmp_path)
    assert digest.default_path(date(2026, 10, 30)) == tmp_path / "digest-20261030.md"


def manager_id(conn, fund_name):
    return conn.execute("SELECT manager_id FROM fund_manager JOIN fund USING (cik) WHERE name = ?",
                        (fund_name,)).fetchone()[0]


def test_emerging_changes_since_the_last_digest(world, tmp_path):
    conn = repo.connect(world)
    first = tmp_path / "first.md"
    digest.write(conn, first, EMERGING_TODAY, EMERGING_TODAY.isoformat())
    assert "First digest: recorded" in first.read_text(encoding="utf-8")
    second = tmp_path / "second.md"
    digest.write(conn, second, EMERGING_TODAY, EMERGING_TODAY.isoformat())
    assert "None since the last digest." in second.read_text(encoding="utf-8")

    # Teal raises 6,000,000 and Heron files a third vehicle; Fresh (3 months old) changes too
    # but is not yet in the emerging window.
    teal, heron, fresh = (manager_id(conn, n) for n in ("Teal Fund LP", "Heron Master Fund LP",
                                                        "Fresh Fund LP"))
    with conn:
        conn.execute("UPDATE manager SET total_sold = 6000000 WHERE manager_id = ?", (teal,))
        conn.execute("UPDATE manager SET n_funds = n_funds + 1 WHERE manager_id IN (?, ?)", (heron, fresh))
    third = tmp_path / "third.md"
    assert digest.write(conn, third, EMERGING_TODAY, EMERGING_TODAY.isoformat())[1] == 2
    text = third.read_text(encoding="utf-8")
    assert "| 0 to 6,000,000 | 1 to 1 |" in text and "raised passed 5,000,000" in text
    assert "| 18,000,000 to 18,000,000 | 2 to 3 |" in text and "1 new vehicle" in text
    assert "Fresh" not in text
    conn.close()


def test_digest_command_writes_the_file(world, tmp_path, capsys):
    out = tmp_path / "cli.md"
    assert cli.main(["--db", world, "digest", "--out", str(out)]) == 0
    assert out.is_file()
    assert capsys.readouterr().out.startswith(f"wrote {out}: ")
