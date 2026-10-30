from pathlib import Path

import pytest
import requests

from fundtracker import ingest
from fundtracker.db import repo
from fundtracker.edgar import datasets

FIXTURES = Path(__file__).parent / "fixtures" / "datasets"
BASE = "https://www.sec.gov/files/structureddata/data/form-d-data-sets/"
PAGE = f"""
<a href="/files/structureddata/data/form-d-data-sets/2010q2_d_0.zip">2010 Q2</a>
<a href="/files/structureddata/data/form-d-data-sets/2015q2_d.zip">2015 Q2</a>
"""


class FakeResponse:
    def __init__(self, status=200, body=b"", text=""):
        self.status_code, self.body, self.text = status, body, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        pass


class FakeClient:
    """Serves the data sets page and the trimmed fixture zips."""

    def __init__(self, bodies=None):
        self.bodies = bodies or {
            BASE + "2010q2_d_0.zip": (FIXTURES / "2010q2_trimmed.zip").read_bytes(),
            BASE + "2015q2_d.zip": (FIXTURES / "2015q2_trimmed.zip").read_bytes(),
        }
        self.calls = []

    def get(self, url, stream=False):
        self.calls.append(url)
        if url == datasets.DATASETS_PAGE:
            return FakeResponse(text=PAGE)
        return FakeResponse(body=self.bodies[url])


def counts(conn):
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("filing", "issuer", "offering", "related_person", "recipient", "signature")}


@pytest.fixture
def conn(tmp_path):
    c = repo.connect(tmp_path / "t.db")
    yield c
    c.close()


def test_backfill_twice_second_run_is_a_no_op(conn, tmp_path):
    client = FakeClient()
    first = ingest.backfill(conn, client, dest_dir=tmp_path / "zips")
    assert first == [("2010q2", "ok", 201), ("2015q2", "ok", 201)]
    after_first = counts(conn)
    assert after_first["filing"] == 402
    log_after_first = [tuple(r) for r in conn.execute("SELECT * FROM ingest_log ORDER BY key")]
    zip_fetches = [u for u in client.calls if u.endswith(".zip")]
    assert len(zip_fetches) == 2

    second = ingest.backfill(conn, client, dest_dir=tmp_path / "zips")
    assert second == [("2010q2", "skipped", 0), ("2015q2", "skipped", 0)]
    assert counts(conn) == after_first
    assert [tuple(r) for r in conn.execute("SELECT * FROM ingest_log ORDER BY key")] == log_after_first
    assert [u for u in client.calls if u.endswith(".zip")] == zip_fetches

    rows = conn.execute("SELECT source, key, status, n_rows, started_at, finished_at, error "
                        "FROM ingest_log ORDER BY key").fetchall()
    assert [(r["source"], r["key"], r["status"], r["n_rows"]) for r in rows] == [
        ("dataset", "2010q2", "ok", 201), ("dataset", "2015q2", "ok", 201)]
    assert all(r["started_at"] and r["finished_at"] and r["error"] is None for r in rows)


def test_from_and_to_bounds(conn, tmp_path):
    assert ingest.backfill(conn, FakeClient(), from_quarter="2011q1",
                           dest_dir=tmp_path) == [("2015q2", "ok", 201)]
    assert ingest.backfill(conn, FakeClient(), to_quarter="2010q2",
                           dest_dir=tmp_path) == [("2010q2", "ok", 201)]


def test_failed_quarter_leaves_nothing_and_is_retried(conn, tmp_path):
    good = (FIXTURES / "2015q2_trimmed.zip").read_bytes()
    broken = FakeClient({BASE + "2010q2_d_0.zip": good[: len(good) // 2],  # truncated zip
                         BASE + "2015q2_d.zip": good})
    result = ingest.backfill(conn, broken, dest_dir=tmp_path / "a")
    assert result == [("2010q2", "error", 0), ("2015q2", "ok", 201)]
    status = repo.ingest_status(conn, "dataset", "2010q2")
    assert status["status"] == "error" and "BadZipFile" in status["error"]
    assert counts(conn)["filing"] == 201  # only 2015q2

    result = ingest.backfill(conn, FakeClient(), dest_dir=tmp_path / "b")
    assert result == [("2010q2", "ok", 201), ("2015q2", "skipped", 0)]
    assert repo.ingest_status(conn, "dataset", "2010q2")["error"] is None


def test_error_inside_the_quarter_rolls_back_the_whole_quarter(conn, tmp_path, monkeypatch):
    real = datasets.read_quarter

    def fails_halfway(path):
        for i, filing in enumerate(real(path)):
            if i == 100:
                raise RuntimeError("disk full")
            yield filing

    monkeypatch.setattr(datasets, "read_quarter", fails_halfway)
    result = ingest.backfill(conn, FakeClient(), to_quarter="2010q2", dest_dir=tmp_path)
    assert result == [("2010q2", "error", 0)]
    assert counts(conn)["filing"] == 0
    assert "disk full" in repo.ingest_status(conn, "dataset", "2010q2")["error"]


def test_write_filings_matches_upsert_filing(tmp_path):
    filings = list(datasets.read_quarter(FIXTURES / "2015q2_trimmed.zip"))
    a, b = repo.connect(tmp_path / "a.db"), repo.connect(tmp_path / "b.db")
    with a:
        repo.write_filings(a, filings)
    for f in filings:
        repo.upsert_filing(b, f)
    for t in ("filing", "issuer", "offering", "related_person", "recipient", "signature"):
        cols = "*" if t != "filing" else "accession, cik, filing_date, submission_type, file_num"
        assert (sorted(map(tuple, a.execute(f"SELECT {cols} FROM {t}")))
                == sorted(map(tuple, b.execute(f"SELECT {cols} FROM {t}")))), t


def test_accessions_present(conn):
    filings = list(datasets.read_quarter(FIXTURES / "2010q2_trimmed.zip"))
    with conn:
        repo.write_filings(conn, filings[:3])
    wanted = [f.accession for f in filings[:5]] + ["0000000000-00-000000"]
    assert repo.accessions_present(conn, wanted) == {f.accession for f in filings[:3]}
