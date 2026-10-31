"""Command line: python -m fundtracker <command> (or the fundtracker script).

    backfill [--from 2009q1] [--to 2026q3]   load the quarterly data sets
    ingest daily [--date 2026-10-28]         load one day from the daily form index
    ingest catchup [--until 2026-10-28]      load every weekday not yet loaded
    ingest adv                               load the newest monthly Form ADV adviser files
    stats [--quarter 2026q3]                 what the database holds
    derive                                   rebuild funds, managers and launches
    refresh                                  ingest catchup, ingest adv when a new month
                                             is due, then derive (what the site's Refresh
                                             button and the scheduled task run)
    schedule install|show|remove             a weekday 06:30 refresh in Task Scheduler
    digest [--since 7d] [--out PATH]         the week's launches and emerging changes, as Markdown
    new [--since 7d] [--min-score N] [--type hedge|all] [--xlsx [PATH]] [--csv PATH]
                                             launches since a date, best score first
    emerging [--min-age 6m] [--max-age 18m] [--type hedge|all] [--min-score N]
             [--xlsx [PATH]] [--csv PATH]    managers 6 to 18 months old, by traction
    fund <cik>                               a fund's filings, people and EDGAR links
    manager <id>                             a manager's funds over time
    search "<text>"                          local search over funds, managers, people
    serve [--host 127.0.0.1] [--port 8000] [--no-refresh]
                                             the local site; refreshes first when stale
"""

import argparse
import csv
import json
import logging
import re
import shutil
import sys
import textwrap
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from fundtracker import classify, config, derive, ingest, newness, normalize, traction
from fundtracker.db import repo
from fundtracker.edgar.datasets import Quarter
from fundtracker.edgar.index import filing_folder_url as edgar_folder


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
    q = ingest_sub.add_parser("adv", help="load the newest registered and exempt Form ADV adviser files")
    q.set_defaults(func=cmd_ingest_adv)

    p = sub.add_parser("stats", help="counts by source, form and fund type, and recent ingest steps")
    p.add_argument("--quarter", type=Quarter.parse, metavar="YYYYqN",
                   help="also count one quarter's data set filings and Hedge Fund offerings")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("derive", help="rebuild the derived fund, manager and launch tables")
    p.set_defaults(func=cmd_derive)

    p = sub.add_parser("refresh", help="ingest catchup and adv, then derive: the site's Refresh job")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("schedule", help="add, show or remove a weekday 06:30 refresh task")
    p.add_argument("action", choices=("install", "show", "remove"))
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser("digest", help="write the week's top launches and emerging manager changes as Markdown")
    p.add_argument("--since", default="7d", type=_since_arg, metavar="7d|YYYY-MM-DD",
                   help="launches since days back from today, or a date (default 7d)")
    p.add_argument("--out", metavar="PATH", help="default data/exports/digest-{YYYYMMDD}.md")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("new", help="launches first filed since a date, best newness score first")
    p.add_argument("--since", default="7d", type=_since_arg, metavar="7d|30d|YYYY-MM-DD",
                   help="days back from today, or a date (default 7d)")
    p.add_argument("--min-score", type=int, default=0, metavar="N")
    p.add_argument("--type", choices=("hedge", "all"), default="hedge",
                   help="hedge: launches with a Hedge Fund; all: every pooled fund (default hedge)")
    p.add_argument("--xlsx", nargs="?", const="", metavar="PATH",
                   help="also write an Excel workbook (default path "
                        "data/exports/new-{type}-{since}-{YYYYMMDD}.xlsx)")
    p.add_argument("--csv", metavar="PATH",
                   help="also write the rows with every launch and fund column")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("emerging", help="managers whose first filing is 6 to 18 months old, by traction")
    p.add_argument("--min-age", default=EMERGING_MIN_AGE, type=_age_arg, metavar="6m|180d",
                   help=f"youngest first filing, in months or days (default {EMERGING_MIN_AGE})")
    p.add_argument("--max-age", default=EMERGING_MAX_AGE, type=_age_arg, metavar="18m|540d",
                   help=f"oldest first filing (default {EMERGING_MAX_AGE})")
    p.add_argument("--type", choices=("hedge", "all"), default="hedge",
                   help="hedge: managers with a Hedge Fund vehicle; all: any pooled fund (default hedge)")
    p.add_argument("--min-score", type=int, default=0, metavar="N")
    p.add_argument("--xlsx", nargs="?", const="", metavar="PATH",
                   help="also write an Excel workbook (default path "
                        "data/exports/emerging-{type}-{min}-{max}-{YYYYMMDD}.xlsx)")
    p.add_argument("--csv", metavar="PATH", help="also write the rows with every manager column")
    p.set_defaults(func=cmd_emerging)

    p = sub.add_parser("fund", help="one fund's filings, related persons, signers and EDGAR links")
    p.add_argument("cik")
    p.set_defaults(func=cmd_fund)

    p = sub.add_parser("manager", help="one manager and every fund linked to it")
    p.add_argument("manager_id")
    p.set_defaults(func=cmd_manager)

    p = sub.add_parser("search", help="search fund names, manager names and related persons")
    p.add_argument("text")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("serve", help="run the local site (Launches, Emerging, Refresh)")
    p.add_argument("--host", default="127.0.0.1", help="address to bind (default 127.0.0.1, this machine only)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-refresh", action="store_true",
                   help="do not start a refresh when the last daily day is before the previous weekday")
    p.set_defaults(func=cmd_serve)

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


previous_weekday = ingest.previous_weekday


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


def cmd_ingest_adv(conn, args):
    from fundtracker.edgar.client import EdgarClient

    _progress_to_stdout(args)
    started = time.monotonic()
    results = ingest.adv(conn, EdgarClient())
    for key, status, n_rows in results:
        print(f"{key}: {n_rows:,} advisers loaded" if status == "ok" else f"{key}: {status}"
              + (" (already loaded)" if status == "skipped" else ""))
    print(f"{sum(n for _k, _s, n in results):,} advisers loaded in {time.monotonic() - started:.0f}s; "
          "run derive to match them to managers")
    return 1 if any(s == "error" for _k, s, _n in results) else 0


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


def cmd_derive(conn, args):
    started = time.monotonic()
    counts = derive.run(conn)
    over = ", ".join(f"{n} {kind}" for kind, n in counts["over_cap"].items())
    print(f"{counts['fund']:,} funds, {counts['manager']:,} managers, {counts['launch']:,} launches, "
          f"{counts['link_key']:,} link keys ({over} over the cap), "
          f"in {time.monotonic() - started:.0f}s")
    tiers = counts["manager_adviser"]
    print(f"advisers: {tiers['high']:,} managers linked high, {tiers['medium']:,} medium, "
          f"{tiers['none']:,} with candidates only")
    for name, timing in counts["timings"].items():
        print(f"  {derive.timing_line(name, timing)}")
    return 0


def cmd_refresh(conn, args):
    from fundtracker.edgar.client import EdgarClient
    from fundtracker.web import jobs

    started = time.monotonic()
    try:
        jobs.run_refresh(conn, EdgarClient(), lambda line: print(f"{datetime.now():%H:%M:%S} {line}"))
    except RuntimeError as exc:
        print(f"failed: {exc}")
        return 1
    print(f"refresh done in {time.monotonic() - started:.0f}s")
    return 0


def cmd_schedule(conn, args):
    from fundtracker import schedule

    return schedule.run(args.action)


def cmd_digest(conn, args):
    from fundtracker import digest

    today = date.today()
    path = Path(args.out) if args.out else digest.default_path(today)
    n_launches, n_changed = digest.write(conn, path, today, parse_since(args.since, today))
    print(f"wrote {path}: {n_launches} launches, {n_changed} emerging managers changed")
    return 0


def parse_since(text, today=None):
    """7d or 30d counts back from today; anything else is an ISO date."""
    m = re.fullmatch(r"(\d+)d", text.strip())
    if m:
        return ((today or date.today()) - timedelta(days=int(m.group(1)))).isoformat()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected 7d, 30d or YYYY-MM-DD, not {text!r}")


def _since_arg(text):
    """--since as typed, after checking parse_since accepts it; it also names the workbook."""
    parse_since(text)
    return text.strip()


def _launch_name(row):
    n = len(json.loads(row["fund_ciks"]))
    return (row["display_name"] or "") + (f" (+{n - 1})" if n > 1 else "")


def _amount(value):
    return "" if value is None else f"{value:,}"


def _reasons(row):
    return json.loads(row["newness_reasons"] or "[]")


def _short_type(fund_type):
    return (fund_type or "").replace(" Fund", "").replace("Investment", "Inv")


def _offering(row):
    if row["fund_total_offering_amount_is_indefinite"]:
        return "Indefinite"
    return _amount(row["fund_total_offering_amount_num"])


def _rule_506(row):
    return classify.rule_506(json.loads(row["fund_exemptions"] or "[]"))


# The columns new prints, as (header, width, value of a launches_since row).
# An int width is fixed; a float is that share of the space the fixed columns
# leave. Every cell wraps to its width rather than being cut. Fund columns
# come from the launch's anchor fund.
NEW_COLUMNS = (
    ("Date", 10, lambda r: r["window_start"]),
    ("Score", 5, lambda r: "" if r["newness_score"] is None else str(r["newness_score"])),
    ("Launch", 0.55, _launch_name),
    ("Manager", 0.45, lambda r: r["manager_name"] or ""),
    ("Type", 9, lambda r: _short_type(r["fund_type"])),
    ("506", 6, _rule_506),
    ("Min", 9, lambda r: _amount(r["fund_min_investment_num"])),
    ("State", 5, lambda r: r["fund_state"] or ""),
)
MIN_FLEX_WIDTH = 16
NOTE_INDENT = " " * 6
GAP = "  "


def _widths(columns, total):
    fixed = sum(w for _h, w, _v in columns if isinstance(w, int))
    n_flex = sum(1 for _h, w, _v in columns if not isinstance(w, int))
    flex = max(n_flex * MIN_FLEX_WIDTH, total - fixed - len(GAP) * (len(columns) - 1))
    return [w if isinstance(w, int) else max(MIN_FLEX_WIDTH, int(flex * w)) for _h, w, _v in columns]


def _table_lines(cells, widths):
    """One row of cells as printed lines, each cell wrapped to its column width."""
    wrapped = [textwrap.wrap(str(c), w, break_on_hyphens=False) or [""] for c, w in zip(cells, widths)]
    return [GAP.join((w[i] if i < len(w) else "").ljust(width)
                     for w, width in zip(wrapped, widths)).rstrip()
            for i in range(max(len(w) for w in wrapped))]


def table(columns, rows, total_width, notes):
    """A table as lines: a header, then per row its wrapped columns and, under it, each
    non-empty text notes(row) returns, indented and wrapped to the full width."""
    widths = _widths(columns, total_width)
    lines = _table_lines([h for h, _w, _v in columns], widths)
    for r in rows:
        lines.extend(_table_lines([value(r) for _h, _w, value in columns], widths))
        for note in notes(r):
            if note:
                lines.extend(textwrap.wrap(note, max(40, total_width), initial_indent=NOTE_INDENT,
                                           subsequent_indent=NOTE_INDENT, break_on_hyphens=False))
    return lines


def launch_table(rows, total_width):
    """The new table: per launch its columns and a line of reason phrases."""
    return table(NEW_COLUMNS, rows, total_width,
                 lambda r: ["; ".join(newness.reason_phrase(x) for x in _reasons(r))])


def default_xlsx_path(fund_type, since_text, today=None):
    stamp = (today or date.today()).strftime("%Y%m%d")
    return Path(config.EXPORT_DIR) / f"new-{fund_type}-{since_text}-{stamp}.xlsx"


def cmd_new(conn, args):
    since = parse_since(args.since)
    fund_types = ["Hedge Fund"] if args.type == "hedge" else None
    rows = repo.launches_since(conn, since, args.min_score, fund_types)
    if not rows and not conn.execute("SELECT 1 FROM launch LIMIT 1").fetchone():
        print("no launches yet: run fundtracker derive first")
        return 1
    for line in launch_table(rows, shutil.get_terminal_size((120, 24)).columns):
        print(line)
    print(f"{len(rows)} launches since {since}"
          + (f" with score >= {args.min_score}" if args.min_score else "")
          + (", Hedge Fund only" if args.type == "hedge" else ", every pooled fund type"))
    if args.xlsx is not None:
        from fundtracker import export

        path = Path(args.xlsx) if args.xlsx else default_xlsx_path(args.type, args.since)
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = {"generated_at": datetime.now(), "since": since, "type": args.type,
                "min_score": args.min_score,
                "last_daily": repo.last_ingest_key(conn, ingest.DAILY_SOURCE)}
        export.write_workbook(path, rows, repo.funds_for_launches(conn, rows), meta)
        print(f"wrote {path}")
    if args.csv:
        _write_new_csv(args.csv, rows)
        print(f"wrote {args.csv}")
    return 0


# The leading CSV columns, as (header, value of a launches_since row); every
# launch column and every anchor fund column (fund_*) follow them.
CSV_COLUMNS = (
    ("date", lambda r: r["window_start"]),
    ("score", lambda r: r["newness_score"]),
    ("launch", _launch_name),
    ("manager", lambda r: r["manager_name"] or ""),
    ("type", lambda r: r["fund_type"] or ""),
    ("rule_506", _rule_506),
    ("min", lambda r: r["fund_min_investment_num"]),
    ("offering", _offering),
    ("state", lambda r: r["fund_state"] or ""),
    ("reasons", lambda r: "; ".join(newness.reason_phrase(x) for x in _reasons(r))),
    ("edgar", lambda r: edgar_folder(r["anchor_cik"], r["launch_id"])),
)


def _write_new_csv(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    raw = list(rows[0]) if rows else []
    with open(path, "w", newline="", encoding="utf-8") as fh:
        out = csv.writer(fh)
        out.writerow([h for h, _v in CSV_COLUMNS] + raw)
        for r in rows:
            out.writerow([value(r) for _h, value in CSV_COLUMNS] + [r[c] for c in raw])


# emerging: managers first seen 6 to 18 months ago, ranked by traction (PLAN.md 7.6).

EMERGING_MIN_AGE = "6m"
EMERGING_MAX_AGE = "18m"
EMERGING_MAX_FUNDS = traction.EMERGING_MAX_FUNDS


def parse_age(text):
    """6m is six months, 180d is 180 days (a timedelta)."""
    m = re.fullmatch(r"(\d+)([md])", text.strip().lower())
    if not m:
        raise argparse.ArgumentTypeError(f"expected an age such as 6m or 180d, not {text!r}")
    n = int(m.group(1))
    return n if m.group(2) == "m" else timedelta(days=n)


def _age_arg(text):
    """--min-age and --max-age as typed, after checking parse_age accepts them."""
    parse_age(text)
    return text.strip().lower()


def _people_text(row):
    people = json.loads(row["people"] or "[]")
    return "; ".join(f"{p['name']} ({', '.join(p['roles'])})" if p["roles"] else p["name"] for p in people)


def _prior_text(row):
    firms = json.loads(row["prior_firms"] or "[]")
    return f"prior firm: {', '.join(firms)}" if firms else ""


def _place_text(row):
    return ", ".join(p for p in (row["city"], row["state"] or row["country"]) if p)


EMERGING_COLUMNS = (
    ("Score", 5, lambda r: str(r["score"])),
    ("Manager", 1.0, lambda r: r["display_name"] or ""),
    ("First seen", 10, lambda r: r["first_seen_date"] or ""),
    ("Age", 3, lambda r: str(r["age_months"])),
    ("Funds", 5, lambda r: str(r["n_funds"])),
    ("Raised", 13, lambda r: _amount(r["total_sold"])),
    ("Investors", 9, lambda r: _amount(r["total_investors"])),
    ("Latest", 10, lambda r: r["latest_filing_date"] or ""),
    ("City and state", 18, _place_text),
)


def emerging_rows(conn, args, today):
    """The managers emerging lists, scored, filtered by --min-score and sorted by score,
    then total sold, then first seen."""
    managers = repo.emerging_managers(
        conn, today, parse_age(args.min_age), parse_age(args.max_age),
        ["Hedge Fund"] if args.type == "hedge" else None, max_funds=EMERGING_MAX_FUNDS)
    return traction.score_managers(managers, today, args.type, args.min_score)


def emerging_table(rows, total_width):
    """The emerging table: per manager its columns, then lines for its people, its prior
    firm when it has one, and its reasons."""
    return table(EMERGING_COLUMNS, rows, total_width, lambda r: [
        _people_text(r),
        _prior_text(r),
        "; ".join(traction.reason_phrase(x) for x in json.loads(r["reasons"]))])


def default_emerging_path(fund_type, min_age, max_age, today=None):
    stamp = (today or date.today()).strftime("%Y%m%d")
    return Path(config.EXPORT_DIR) / f"emerging-{fund_type}-{min_age}-{max_age}-{stamp}.xlsx"


def cmd_emerging(conn, args):
    today = date.today()
    if not conn.execute("SELECT 1 FROM manager WHERE n_filings IS NOT NULL LIMIT 1").fetchone():
        print("no manager traction yet: run fundtracker derive first")
        return 1
    rows = emerging_rows(conn, args, today)
    for line in emerging_table(rows, shutil.get_terminal_size((120, 24)).columns):
        print(line)
    oldest = repo.age_cutoff(today, parse_age(args.max_age))
    newest = repo.age_cutoff(today, parse_age(args.min_age))
    print(f"{len(rows)} managers first seen {oldest} to {newest}"
          + (f" with score >= {args.min_score}" if args.min_score else "")
          + (", with a Hedge Fund vehicle" if args.type == "hedge" else ", every pooled fund type")
          + f", fewer than {EMERGING_MAX_FUNDS} funds, no over-cap organisations")
    if args.xlsx is not None:
        from fundtracker import export

        path = Path(args.xlsx) if args.xlsx else default_emerging_path(args.type, args.min_age, args.max_age)
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = {"generated_at": datetime.now(), "today": today, "oldest": oldest, "newest": newest,
                "min_age": args.min_age, "max_age": args.max_age, "type": args.type,
                "min_score": args.min_score, "max_funds": EMERGING_MAX_FUNDS,
                "last_daily": repo.last_ingest_key(conn, ingest.DAILY_SOURCE)}
        funds = [dict(f, manager_score=m["score"]) for m in rows for f in m["funds"]]
        export.write_emerging_workbook(path, rows, funds, meta)
        print(f"wrote {path}")
    if args.csv:
        _write_emerging_csv(args.csv, rows)
        print(f"wrote {args.csv}")
    return 0


def _write_emerging_csv(path, rows):
    """The printed columns, then people, reasons and every manager column."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    raw = [c for c in (rows[0] if rows else ()) if c not in ("funds", "reasons", "score", "age_months")]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        out = csv.writer(fh)
        out.writerow([h.lower().replace(" ", "_") for h, _w, _v in EMERGING_COLUMNS]
                     + ["people_text", "prior_firm", "reason_text"] + raw)
        for r in rows:
            out.writerow([value(r) for _h, _w, value in EMERGING_COLUMNS]
                         + [_people_text(r), ", ".join(json.loads(r["prior_firms"] or "[]")),
                            "; ".join(traction.reason_phrase(x)
                                                       for x in json.loads(r["reasons"]))]
                         + [r[c] for c in raw])


def cmd_fund(conn, args):
    fund = repo.get_fund(conn, args.cik)
    filings = repo.filings_for_cik(conn, repo.pad_cik(args.cik))
    if fund is None and not filings:
        print(f"no fund with CIK {args.cik}")
        return 1
    if fund:
        candidate = f", hedge candidate ({fund['candidate_reason']})" if fund["is_hedge_candidate"] else ""
        print(f"{fund['name']} (CIK {fund['cik']}), {fund['fund_type'] or fund['industry_group']}"
              f"{candidate}")
        linked = f", linked by {fund['evidence']}" if fund["evidence"] else ""
        name = f" ({fund['manager_name']})" if fund["manager_name"] else ""
        print(f"manager {fund['manager_id']}{name}{linked}")
    print("filings")
    for f in filings:
        o = f.offering
        offering = "Indefinite" if o.total_offering_amount_is_indefinite else _amount(o.total_offering_amount_num)
        investors = "-" if o.n_investors is None else o.n_investors
        print(f"  {f.filing.filing_date}  {f.filing.submission_type:<4} {f.accession}  "
              f"{','.join(o.exemptions):<14} offering {offering or '-'}, "
              f"sold {_amount(o.total_amount_sold_num) or '-'}, "
              f"min {_amount(o.min_investment_num) or '-'}, investors {investors}")
    print("related persons")
    people = {}  # name_norm -> (display name, relationships, is_entity), first filing's wording
    for f in filings:
        for p in f.related_persons:
            display = normalize.entity_display_name if p.is_entity else normalize.person_display_name
            name = display(p.first, p.middle, p.last) or p.name_norm
            people.setdefault(p.name_norm, (name, p.relationships, p.is_entity))
    for name, relationships, entity in people.values():
        print(f"  {name}{' (entity)' if entity else ''}: {', '.join(relationships)}")
    print("signers")
    signers = {}
    for f in filings:
        for sig in f.signatures:
            signers.setdefault((sig.signer_norm or "", sig.title or ""), " ".join((sig.signer or "").split()))
    for (_norm, title), signer in sorted(signers.items()):
        print(f"  {signer}, {title}")
    print("EDGAR")
    for f in filings:
        print(f"  {edgar_folder(f.filing.cik, f.accession)}")
    return 0


def cmd_manager(conn, args):
    m = repo.get_manager(conn, args.manager_id)
    if m is None:
        print(f"no manager {args.manager_id}")
        return 1
    linked = f", linked by {m['key_kind']}" if m["key_kind"] else ""
    print(f"{m['display_name']} ({m['manager_id']}), first seen {m['first_seen_date']}, "
          f"{m['n_funds']} funds{linked}")
    for f in m["funds"]:
        print(f"  {f['first_filing_date']} to {f['latest_filing_date']}  {f['cik']}  "
              f"{f['name'] or '':<50}  {f['fund_type'] or f['industry_group'] or ''}")
    return 0


def cmd_search(conn, args):
    rows = repo.search_names(conn, args.text)
    for r in rows:
        ident = "" if r["kind"] == "person" else r["id"]
        where = f"  (on fund {r['cik']})" if r["kind"] == "person" else ""
        print(f"{r['kind']:<8} {ident:<12} {r['name']}{where}")
    print(f"{len(rows)} matches" + (" (first 50)" if len(rows) == 50 else ""))
    return 0


def cmd_serve(conn, args):
    """Serve the site until interrupted. The site reads through its own read-only
    connections; conn has only made sure the schema is current."""
    from fundtracker.web import jobs
    from fundtracker.web.app import create_app

    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    refresher = jobs.Refresher(jobs.refresh_runner(path))
    app = create_app(connect=lambda: repo.connect_readonly(path), refresher=refresher)
    if not args.no_refresh and jobs.is_stale(conn):
        refresher.start()
        print(f"last daily day {repo.last_ingest_key(conn, ingest.DAILY_SOURCE) or 'none'} is before "
              f"{previous_weekday()}: refreshing in the background (--no-refresh skips this)")
    print(f"FundTracker on http://{args.host}:{args.port}/ (database {path}); Ctrl+C stops it")
    app.run(host=args.host, port=args.port, debug=False, threaded=True, use_reloader=False)
    return 0


def _stdout_handler():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    return handler


if __name__ == "__main__":
    sys.exit(main())
