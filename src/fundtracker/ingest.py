"""Ingestion flows (PLAN.md section 7.5). Every step is idempotent and logged.

backfill loads the quarterly data sets. Each quarter is one transaction that
also writes its ingest_log row, so a quarter is either fully loaded and
marked ok, or absent and marked error.
"""

import logging
import time
from datetime import datetime, timezone

from fundtracker import config
from fundtracker.db import repo
from fundtracker.edgar import datasets
from fundtracker.edgar.datasets import Quarter

log = logging.getLogger(__name__)

DATASET_SOURCE = "dataset"


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
