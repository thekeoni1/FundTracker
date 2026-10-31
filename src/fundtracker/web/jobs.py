"""The site's Refresh job: ingest catchup and adv, derive, then the outreach pull, in a
background thread.

The only place the site touches the network. One job runs at a time; a
second start while one runs is refused. The job keeps its state (idle,
running, done or failed), its log lines and its timestamps for the status
bar to show. Tests pass a fake runner, a function that takes log (a
callable for one line) and does the work.
"""

import logging
import threading
from datetime import datetime

from fundtracker import config, derive, ingest, outreach
from fundtracker.db import repo

# Lines kept for the status bar; a catchup over many days logs one line per day.
MAX_LINES = 200


class Refresher:
    def __init__(self, runner):
        self.runner = runner
        self._lock = threading.Lock()
        self._thread = None
        self.state = "idle"
        self.lines = []
        self.started_at = None
        self.finished_at = None
        self.error = None

    def start(self):
        """Start the job in a thread and return True, or return False if one is running."""
        with self._lock:
            if self.state == "running":
                return False
            self.state, self.lines, self.error = "running", [], None
            self.started_at, self.finished_at = datetime.now(), None
            self._thread = threading.Thread(target=self._run, name="fundtracker-refresh", daemon=True)
            self._thread.start()
        return True

    def _run(self):
        try:
            self.runner(self.log)
            state, error = "done", None
        except Exception as exc:  # the status bar shows any failure; the server keeps running
            state, error = "failed", f"{type(exc).__name__}: {exc}"
            self.log(f"failed: {error}")
        with self._lock:
            self.state, self.error, self.finished_at = state, error, datetime.now()

    def log(self, line):
        with self._lock:
            self.lines.append(f"{datetime.now():%H:%M:%S} {line}")
            del self.lines[:-MAX_LINES]

    def snapshot(self):
        """The job's state as a dict, safe to render while the thread runs."""
        with self._lock:
            return {"state": self.state, "lines": list(self.lines), "started_at": self.started_at,
                    "finished_at": self.finished_at, "error": self.error}

    def wait(self, timeout=None):
        """Block until the current job ends (for tests and the command line)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)


def refresh_runner(db_path):
    """The real job: run_refresh on its own connection with the live EDGAR client."""

    def run(log):
        from fundtracker.edgar.client import EdgarClient

        conn = repo.connect(db_path)
        try:
            run_refresh(conn, EdgarClient(), log)
        finally:
            conn.close()

    return run


def run_refresh(conn, client, log):
    """Catchup from EDGAR's daily index, the Form ADV files when a new month is due
    (ingest.adv_due), derive, then outreach.pull when FUNDTRACKER_OUTREACH_CSV is set,
    logging each step through log.

    The one code path behind the site's Refresh button, fundtracker refresh
    and the scheduled task. Raises RuntimeError at the end if any day or adviser
    file failed. A failed outreach pull is logged and does not fail the refresh.
    """
    progress = logging.getLogger("fundtracker.ingest")
    handler = _LineHandler(log)
    level = progress.level
    progress.addHandler(handler)
    progress.setLevel(logging.INFO)
    try:
        log("catchup: loading every weekday not yet loaded")
        results = ingest.catchup(conn, client)
        failed = [r for r in results if r.status == "error"]
        log(f"catchup: {len(results)} days fetched, {sum(r.loaded for r in results)} filings loaded, "
            f"{len(failed)} days failed")
        adv_failed = []
        if ingest.adv_due(conn):
            log("adv: looking for a new month of Form ADV adviser files")
            try:
                adv_results = ingest.adv(conn, client)
                adv_failed = [key for key, status, _n in adv_results if status == "error"]
            except Exception as exc:  # the page itself failed; derive still runs
                adv_results, adv_failed = [], [f"the adviser page ({type(exc).__name__}: {exc})"]
            log("adv: " + ("; ".join(f"{key} {status}" + (f" ({n:,} advisers)" if n else "")
                                     for key, status, n in adv_results) or "no files listed"))
        log("derive: rebuilding funds, managers and launches (about four minutes)")
        counts = derive.run(conn)
        for name, timing in counts.get("timings", {}).items():
            log(f"derive: {derive.timing_line(name, timing)}")
        log(f"derive: {counts['fund']:,} funds, {counts['manager']:,} managers, "
            f"{counts['launch']:,} launches")
        if config.OUTREACH_CSV:
            try:
                kept, skipped = outreach.pull(conn, config.OUTREACH_CSV)
                log(f"outreach: {kept:,} rows kept, {skipped:,} without a key")
            except Exception as exc:  # the rankings are rebuilt; a missing sheet should not fail them
                log(f"outreach: pull failed ({type(exc).__name__}: {exc})")
        problems = []
        if failed:
            problems.append(f"catchup failed on {', '.join(str(r.day) for r in failed)}")
        if adv_failed:
            problems.append(f"adv failed on {', '.join(adv_failed)}")
        if problems:
            raise RuntimeError("; ".join(problems))
    finally:
        progress.removeHandler(handler)
        progress.setLevel(level)


def is_stale(conn, today=None):
    """True when the last daily day loaded (ok or no_index) is before the previous weekday.

    Holidays count as current once catchup has logged them no_index.
    """
    last = repo.last_ingest_key(conn, ingest.DAILY_SOURCE)
    return last is None or last < ingest.previous_weekday(today).isoformat()


class _LineHandler(logging.Handler):
    def __init__(self, log):
        super().__init__(logging.INFO)
        self._log = log

    def emit(self, record):
        self._log(record.getMessage())
