"""search_index (FTS5) as derive builds it on the WP1 fixtures, and the LIKE fallback."""

import sqlite3
import sys
from pathlib import Path

import pytest

from fundtracker.db import repo

sys.path.insert(0, str(Path(__file__).parent))
from test_web_launches import db_path  # noqa: E402,F401


@pytest.fixture
def conn(db_path):
    conn = repo.connect_readonly(db_path)
    yield conn
    conn.close()


def test_derive_builds_the_index(conn):
    kinds = dict(conn.execute("SELECT kind, count(*) FROM search_index GROUP BY kind").fetchall())
    assert kinds["fund"] == conn.execute("SELECT count(*) FROM fund WHERE name <> ''").fetchone()[0]
    assert kinds["manager"] and kinds["person"]


def test_a_prefix_finds_a_fund_a_manager_and_a_person(conn):
    found = repo.search(conn, "axiar")
    assert [f["name"] for f in found["funds"]] == ["Axiaris Capital Management Fund, LP"]
    assert [m["display_name"] for m in found["managers"]] == ["Axiaris Capital Management LLC"]
    assert [p["name_norm"] for p in found["people"]] == ["AXIARIS CAPITAL MANAGEMENT LLC"]
    assert found["people"][0]["n_filings"] >= 1
    assert [p["name_norm"] for p in repo.search(conn, "murtl")["people"]] == ["ANDREW CUNNINGHAM MURTLAND"]


def test_every_word_must_match(conn):
    assert repo.search(conn, "andrew murtland")["people"]
    assert not repo.search(conn, "andrew axiaris")["people"]


@pytest.fixture
def unindexed(db_path, tmp_path):
    """A copy of the fixture database without search_index, as before derive indexes it."""
    path = tmp_path / "unindexed.db"
    with sqlite3.connect(db_path) as src, sqlite3.connect(path) as dst:
        src.backup(dst)
    with sqlite3.connect(path) as dst:
        dst.execute("DROP TABLE search_index")
    conn = repo.connect_readonly(path)
    yield conn
    conn.close()


@pytest.mark.parametrize("text", ["axiaris", "murtland", "capital", "Jane"])
def test_the_fallback_returns_the_same_rows(conn, unindexed, text):
    assert repo.search(unindexed, text) == repo.search(conn, text)
    assert repo.search_names(unindexed, text) == repo.search_names(conn, text)


def test_text_without_words_uses_the_fallback(conn):
    assert repo.search_names(conn, "%") == repo._search_names_like(conn, "%", 50)
