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
from datetime import date, datetime, timedelta, timezone

from fundtracker import config
from fundtracker.db import repo
from fundtracker.edgar import datasets
from fundtracker.edgar import index as daily_index
from fundtracker.edgar.client import is_not_found
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
    dest_dir = dest_dir if dest_dir is not None else config.DATASETS_DIR
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
    A missing index (weekend, holiday) is logged as no_index; EDGAR answers
    it with an AccessDenied 403, and a 404 is handled the same way in case
    that changes. A filing that fails to fetch or parse is logged and
    skipped; the day is ok if anything loaded or nothing needed loading, and
    error otherwise.
    """
    if isinstance(day, str):
        day = date.fromisoformat(day)
    key = day.isoformat()
    started = _now()
    result = DailyResult(day=key, status="ok")

    try:
        response = client.get(daily_index.form_index_url(day))
        if is_not_found(response):
            result.status = "no_index"
            _log_day(conn, key, "no_index", 0, started)
            return result
        response.raise_for_status()
        rows = daily_index.formd_rows(daily_index.parse_form_index(response.text))
    except Exception as exc:
        result.status = "error"
        result.errors.append((None, f"{type(exc).__name__}: {exc}"))
        _log_day(conn, key, "error", 0, started, f"index: {type(exc).__name__}: {exc}")
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
    _log_day(conn, key, result.status, result.loaded, started, note)
    log.info("%s: %d listed, %d already stored, %d loaded, %d failed", key, result.listed,
             result.already_present, result.loaded, len(result.errors))
    return result


DONE_STATUSES = ("ok", "no_index")


def _log_day(conn, key, status, loaded, started, error=None):
    """Write the ingest_log row for one day as a total across all runs.

    A rerun that loads nothing leaves an ok or no_index row untouched, and a
    failed rerun is reported in the log output only, so a harmless rerun
    cannot erase what an earlier run recorded. A rerun that loads something
    adds to n_rows and keeps the first run's started_at. An error row is
    replaced by the next run's outcome.
    """
    with conn:
        prior = repo.ingest_status(conn, DAILY_SOURCE, key)
        if prior and prior["status"] in DONE_STATUSES and loaded == 0:
            if status == "error":
                log.warning("%s: rerun failed, keeping the earlier %s row: %s",
                            key, prior["status"], error)
            return
        n_rows = loaded
        if prior:
            n_rows += prior["n_rows"] or 0
            started = prior["started_at"] or started
        repo.log_ingest(conn, DAILY_SOURCE, key, status, n_rows=n_rows,
                        started_at=started, finished_at=_now(), error=error)


def previous_weekday(today=None):
    """The last Monday to Friday before today."""
    day = (today or date.today()) - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def catchup_start(conn, today=None):
    """The first day catchup considers.

    The day after the last filing date loaded from the data sets; failing
    that, the earliest day the daily path has logged; failing that (empty
    database), 30 days before today.
    """
    today = today or date.today()
    last_dataset = conn.execute(
        "SELECT max(filing_date) FROM filing WHERE source = 'dataset'").fetchone()[0]
    if last_dataset:
        return date.fromisoformat(last_dataset) + timedelta(days=1)
    first_daily = conn.execute(
        "SELECT min(key) FROM ingest_log WHERE source = ?", (DAILY_SOURCE,)).fetchone()[0]
    if first_daily:
        return date.fromisoformat(first_daily)
    return today - timedelta(days=30)


def catchup(conn, client, until=None, today=None):
    """Run daily for every weekday from catchup_start to until (default yesterday).

    Days already logged ok or no_index are skipped without a request, so a
    normal run fetches only new days, a deleted or failed day in the middle
    is fetched again, and holidays are recorded as no_index like weekends
    would be. Today's index is never fetched unless until says so, because
    it is incomplete before the nightly build. Returns the DailyResults of
    the days fetched.
    """
    today = today or date.today()
    if isinstance(until, str):
        until = date.fromisoformat(until)
    until = until or today - timedelta(days=1)
    done = {r[0] for r in conn.execute(
        f"SELECT key FROM ingest_log WHERE source = ? AND status IN ({', '.join('?' * len(DONE_STATUSES))})",
        (DAILY_SOURCE, *DONE_STATUSES))}
    results = []
    day = catchup_start(conn, today)
    while day <= until:
        if day.weekday() < 5 and day.isoformat() not in done:
            results.append(daily(conn, client, day))
        day += timedelta(days=1)
    return results
