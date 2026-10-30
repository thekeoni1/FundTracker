"""Ingestion flows (PLAN.md section 7.5). Every step is idempotent and logged.

backfill loads the quarterly data sets. Each quarter is one transaction that
also writes its ingest_log row, so a quarter is either fully loaded and
marked ok, or absent and marked error.

daily loads one day's Form D filings from the daily form index. Each filing
commits on its own, so a run that stops halfway keeps what it loaded and the
next run fetches only the rest.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from fundtracker import config
from fundtracker.db import repo
from fundtracker.edgar import datasets
from fundtracker.edgar import index as daily_index
from fundtracker.edgar.datasets import Quarter
from fundtracker.edgar.formd_xml import parse_formd

log = logging.getLogger(__name__)

DATASET_SOURCE = "dataset"
DAILY_SOURCE = "daily"


@dataclass
class DailyResult:
    day: str
    status: str  # ok | no_index | error
    listed: int = 0  # distinct D and D/A accessions in the index
    already_present: int = 0
    loaded: int = 0
    errors: list = field(default_factory=list)  # (accession, message)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def backfill(conn, client, from_quarter=None, to_quarter=None, dest_dir=None):
    """Download and load every data set quarter not already marked ok.

    from_quarter and to_quarter are inclusive, as Quarter or "2026q3" text.
    Returns a list of (quarter key, status, rows loaded) for the quarters
    looked at; skipped quarters report status "skipped".
    """
    dest_dir = dest_dir if dest_dir is not None else config.DATA_DIR / "datasets"
    low = _bound(from_quarter)
    high = _bound(to_quarter)
    results = []
    for quarter in datasets.list_quarters(client):
        position = (quarter.year, quarter.q)
        if (low and position < low) or (high and position > high):
            continue
        status = repo.ingest_status(conn, DATASET_SOURCE, quarter.key)
        if status and status["status"] == "ok":
            results.append((quarter.key, "skipped", 0))
            continue
        results.append(load_quarter(conn, client, quarter, dest_dir))
    return results


def load_quarter(conn, client, quarter, dest_dir):
    """Download (if needed) and load one quarter; return (key, status, rows)."""
    started = _now()
    clock = time.monotonic()
    try:
        path = datasets.download_quarter(client, quarter, dest_dir)
        with conn:
            n_rows = repo.write_filings(conn, datasets.read_quarter(path))
            repo.log_ingest(conn, DATASET_SOURCE, quarter.key, "ok", n_rows=n_rows,
                            started_at=started, finished_at=_now())
    except Exception as exc:  # recorded, then the next quarter is tried
        log.exception("loading %s failed", quarter.key)
        with conn:
            repo.log_ingest(conn, DATASET_SOURCE, quarter.key, "error", started_at=started,
                            finished_at=_now(), error=f"{type(exc).__name__}: {exc}")
        return quarter.key, "error", 0
    log.info("loaded %s: %d filings in %.1fs", quarter.key, n_rows, time.monotonic() - clock)
    return quarter.key, "ok", n_rows


def _bound(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = Quarter.parse(value)
    return (value.year, value.q)


def daily(conn, client, day):
    """Load every D and D/A filing listed in day's form index that is not already stored.

    day is a date or ISO string and is the ingest_log key. Each filing's
    filing_date is the index row's Date Filed, which can be earlier than day.
    A 404 for the index (weekend, holiday) is logged as no_index. A filing
    that fails to fetch or parse is logged and skipped; the day is ok if
    anything loaded or nothing needed loading, and error otherwise.
    """
    if isinstance(day, str):
        day = date.fromisoformat(day)
    key = day.isoformat()
    started = _now()
    result = DailyResult(day=key, status="ok")

    try:
        response = client.get(daily_index.form_index_url(day))
        if response.status_code == 404:
            result.status = "no_index"
            with conn:
                repo.log_ingest(conn, DAILY_SOURCE, key, "no_index", n_rows=0,
                                started_at=started, finished_at=_now())
            return result
        response.raise_for_status()
        rows = daily_index.formd_rows(daily_index.parse_form_index(response.text))
    except Exception as exc:
        result.status = "error"
        result.errors.append((None, f"{type(exc).__name__}: {exc}"))
        with conn:
            repo.log_ingest(conn, DAILY_SOURCE, key, "error", n_rows=0, started_at=started,
                            finished_at=_now(), error=f"index: {type(exc).__name__}: {exc}")
        return result

    result.listed = len(rows)
    present = repo.accessions_present(conn, [r.accession for r in rows])
    result.already_present = len(present)
    for row in rows:
        if row.accession in present:
            continue
        try:
            xml = client.get_cached(daily_index.primary_doc_url(row.cik, row.accession))
            filing = parse_formd(xml, row.accession, cik_hint=row.cik, filing_date=row.date_filed)
            repo.upsert_filing(conn, filing)
            result.loaded += 1
        except Exception as exc:  # one bad filing must not stop the day
            log.warning("%s: %s %s failed: %s", key, row.form_type, row.accession, exc)
            result.errors.append((row.accession, f"{type(exc).__name__}: {exc}"))

    wanted = result.listed - result.already_present
    if result.errors and result.loaded == 0 and wanted > 0:
        result.status = "error"
    note = None
    if result.errors:
        shown = "; ".join(f"{a} {m}" for a, m in result.errors[:5])
        more = f"; and {len(result.errors) - 5} more" if len(result.errors) > 5 else ""
        note = f"{len(result.errors)} of {wanted} filings failed: {shown}{more}"
    with conn:
        repo.log_ingest(conn, DAILY_SOURCE, key, result.status, n_rows=result.loaded,
                        started_at=started, finished_at=_now(), error=note)
    log.info("%s: %d listed, %d already stored, %d loaded, %d failed", key, result.listed,
             result.already_present, result.loaded, len(result.errors))
    return result
