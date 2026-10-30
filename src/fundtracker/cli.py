"""Command line: python -m fundtracker <command> (or the fundtracker script).

    backfill [--from 2009q1] [--to 2026q3]   load the quarterly data sets
    stats [--quarter 2026q3]                 what the database holds
"""

import argparse
import logging
import sys
import time
from datetime import date

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
    progress = logging.getLogger("fundtracker.ingest")
    progress.setLevel(logging.INFO)
    if not args.verbose:  # the root logger shows warnings only; still print one line per quarter
        progress.addHandler(_stdout_handler())
        progress.propagate = False
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
