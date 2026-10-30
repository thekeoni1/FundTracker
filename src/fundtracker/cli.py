"""Command line: python -m fundtracker <command> (or the fundtracker script).

    backfill [--from 2009q1] [--to 2026q3]   load the quarterly data sets
    ingest daily [--date 2026-10-28]         load one day from the daily form index
    ingest catchup [--until 2026-10-28]      load every weekday not yet loaded
    stats [--quarter 2026q3]                 what the database holds
"""

import argparse
import logging
import sys
import time
from datetime import date, timedelta

from fundtracker import config, ingest
from fundtracker.db import repo
from fundtracker.edgar.datasets import Quarter


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fundtracker", description="Track new hedge funds from Form D.")
    parser.add_argument("--db", default=None, help=f"database path (default {config.DB_PATH})")
    parser.add_argument("-v", "--verbose", action="store_true", help="log each request and step")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("backfill", help="download and load the quarterly Form D data sets")
    p.add_argument("--from", dest="from_quarter", type=Quarter.parse, metavar="YYYYqN")
    p.add_argument("--to", dest="to_quarter", type=Quarter.parse, metavar="YYYYqN")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("ingest", help="load new filings from the daily form index")
    ingest_sub = p.add_subparsers(dest="ingest_command", required=True)
    q = ingest_sub.add_parser("daily", help="load one day's D and D/A filings")
    q.add_argument("--date", type=date.fromisoformat, metavar="YYYY-MM-DD",
                   help="index date (default: the last weekday before today)")
    q.set_defaults(func=cmd_ingest_daily)
    q = ingest_sub.add_parser("catchup", help="load every weekday not yet loaded, up to yesterday")
    q.add_argument("--until", type=date.fromisoformat, metavar="YYYY-MM-DD",
                   help="last day to load (default: yesterday)")
    q.set_defaults(func=cmd_ingest_catchup)

    p = sub.add_parser("stats", help="counts by source, form and fund type, and recent ingest steps")
    p.add_argument("--quarter", type=Quarter.parse, metavar="YYYYqN",
                   help="also count one quarter's data set filings and Hedge Fund offerings")
    p.set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    conn = repo.connect(args.db or config.DB_PATH)
    try:
        return args.func(conn, args)
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    finally:
        conn.close()


def cmd_backfill(conn, args):
    from fundtracker.edgar.client import EdgarClient

    client = EdgarClient()
    _progress_to_stdout(args)
    started = time.monotonic()
    results = ingest.backfill(conn, client, args.from_quarter, args.to_quarter)
    loaded = [r for r in results if r[1] == "ok"]
    skipped = [r for r in results if r[1] == "skipped"]
    failed = [r for r in results if r[1] == "error"]
    print(f"{len(loaded)} quarters loaded ({sum(r[2] for r in loaded)} filings), "
          f"{len(skipped)} already loaded, {len(failed)} failed, "
          f"in {time.monotonic() - started:.0f}s")
    for key, _status, _n in failed:
        print(f"  {key}: {repo.ingest_status(conn, ingest.DATASET_SOURCE, key)['error']}")
    return 1 if failed else 0


def previous_weekday(today=None):
    day = (today or date.today()) - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _progress_to_stdout(args):
    progress = logging.getLogger("fundtracker.ingest")
    progress.setLevel(logging.INFO)
    if not args.verbose:  # the root logger shows warnings only; still print progress lines
        progress.addHandler(_stdout_handler())
        progress.propagate = False


def _print_daily(r):
    print(f"{r.day}: {r.status}, {r.listed} listed, {r.already_present} already stored, "
          f"{r.loaded} loaded, {len(r.errors)} failed")
    for accession, message in r.errors[:10]:
        print(f"  {accession or 'index'}: {message}")


def cmd_ingest_daily(conn, args):
    from fundtracker.edgar.client import EdgarClient

    _progress_to_stdout(args)
    result = ingest.daily(conn, EdgarClient(), args.date or previous_weekday())
    _print_daily(result)
    return 1 if result.status == "error" else 0


def cmd_ingest_catchup(conn, args):
    from fundtracker.edgar.client import EdgarClient

    _progress_to_stdout(args)
    started = time.monotonic()
    results = ingest.catchup(conn, EdgarClient(), until=args.until)
    for r in results:
        _print_daily(r)
    by_status = {s: sum(1 for r in results if r.status == s) for s in ("ok", "no_index", "error")}
    print(f"{len(results)} days fetched ({by_status['ok']} ok, {by_status['no_index']} no index, "
          f"{by_status['error']} error), {sum(r.loaded for r in results)} filings loaded, "
          f"in {time.monotonic() - started:.0f}s")
    return 1 if by_status["error"] else 0


def cmd_stats(conn, args):
    def table(title, rows):
        print(title)
        for label, count in rows:
            print(f"  {label if label is not None else '(none)':<30} {count:>10,}")

    total = conn.execute("SELECT count(*) FROM filing").fetchone()[0]
    print(f"database: {conn.execute('PRAGMA database_list').fetchone()['file']}")
    print(f"filings: {total:,}")
    table("by source", conn.execute(
        "SELECT source, count(*) FROM filing GROUP BY source ORDER BY 2 DESC"))
    table("by submission type", conn.execute(
        "SELECT submission_type, count(*) FROM filing GROUP BY 1 ORDER BY 2 DESC"))
    table("by fund type (Pooled Investment Fund offerings)", conn.execute(
        "SELECT fund_type, count(*) FROM offering WHERE industry_group = 'Pooled Investment Fund' "
        "GROUP BY 1 ORDER BY 2 DESC"))

    quarters = [r["key"] for r in conn.execute(
        "SELECT key FROM ingest_log WHERE source = 'dataset' AND status = 'ok'")]
    quarters.sort(key=lambda k: (int(k[:4]), int(k[5])))
    span = f", {quarters[0]} to {quarters[-1]}" if quarters else ""
    print(f"data set quarters loaded: {len(quarters)}{span}")

    daily = conn.execute(
        """
        SELECT max(CASE WHEN status IN ('ok', 'no_index') THEN key END) AS last_day,
               sum(status = 'no_index') AS no_index,
               sum(status = 'error') AS errors
        FROM ingest_log WHERE source = 'daily'
        """).fetchone()
    print(f"daily index: last day {daily['last_day'] or 'none'}, "
          f"{daily['no_index'] or 0} no_index days, {daily['errors'] or 0} error days")

    if args.quarter:
        q = args.quarter
        first = date(q.year, 3 * q.q - 2, 1)
        after = date(q.year + (q.q == 4), 1 if q.q == 4 else 3 * q.q + 1, 1)
        row = conn.execute(
            """
            SELECT count(*) AS filings,
                   sum(o.fund_type = 'Hedge Fund') AS hedge
            FROM filing f LEFT JOIN offering o USING (accession)
            WHERE f.source = 'dataset' AND f.filing_date >= ? AND f.filing_date < ?
            """, (first.isoformat(), after.isoformat())).fetchone()
        logged = repo.ingest_status(conn, ingest.DATASET_SOURCE, q.key)
        print(f"{q.key}: {row['filings']:,} data set filings, {row['hedge'] or 0:,} Hedge Fund "
              f"offering rows; ingest_log says {logged['n_rows'] if logged else 'nothing'}")

    print("last ingest_log rows")
    for r in conn.execute("SELECT * FROM ingest_log ORDER BY finished_at DESC, key DESC LIMIT 5"):
        note = f" ({r['error']})" if r["error"] else ""
        print(f"  {r['source']:<8} {r['key']:<10} {r['status']:<8} {r['n_rows'] or 0:>7,} rows  "
              f"{r['finished_at']}{note}")
    return 0


def _stdout_handler():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    return handler


if __name__ == "__main__":
    sys.exit(main())
