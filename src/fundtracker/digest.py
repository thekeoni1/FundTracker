"""fundtracker digest: the week in one Markdown file.

Three parts: hedge launches since a date with a newness score of at least
70; emerging managers (first seen 6 to 18 months ago, as on the Emerging
page) whose raised total crossed 5,000,000 or who added a vehicle since the
last digest; and a line on how current the data is.

What each manager looked like at the last digest is kept in digest_state.
Every hedge manager first seen in the last 18 months is recorded, so one
that changes while still younger than six months is compared from its own
earlier values once it enters the window. A manager with no earlier row,
and every manager on the first digest, sets a baseline and is not listed.
"""

import json
from datetime import timedelta
from pathlib import Path

from fundtracker import config, traction
from fundtracker.db import repo
from fundtracker.edgar.index import filing_folder_url
from fundtracker.web import jobs

MIN_SCORE = 70
RAISED_LINE = 5_000_000
MIN_AGE_MONTHS = 6
MAX_AGE_MONTHS = 18


def default_path(today):
    return Path(config.EXPORT_DIR) / f"digest-{today:%Y%m%d}.md"


def launches(conn, since):
    return repo.launches_since(conn, since, MIN_SCORE, ["Hedge Fund"])


def tracked_managers(conn, today):
    """Hedge managers first seen from 18 months ago to today, as the Emerging page filters them."""
    return repo.emerging_managers(conn, today, timedelta(days=0), MAX_AGE_MONTHS, ["Hedge Fund"],
                                  max_funds=traction.EMERGING_MAX_FUNDS)


def changes(managers, state, today):
    """The managers in the emerging window that crossed the raised line or added a vehicle
    since their digest_state row, each with was_sold, was_funds and what (a list of phrases)."""
    newest = repo.age_cutoff(today, MIN_AGE_MONTHS).isoformat()
    found = []
    for m in managers:
        was = state.get(m["manager_id"])
        if was is None or m["first_seen_date"] > newest:
            continue
        sold, was_sold = m["total_sold"] or 0, was["total_sold"] or 0
        what = []
        if was_sold < RAISED_LINE <= sold:
            what.append(f"raised passed {RAISED_LINE:,}")
        added = m["n_funds"] - was["n_funds"]
        if added > 0:
            what.append(f"{added} new vehicle{'s' if added > 1 else ''}")
        if what:
            found.append(dict(m, was_sold=was["total_sold"], was_funds=was["n_funds"], what=what))
    return traction.score_managers(found, today, "hedge")


def read_state(conn):
    return {r["manager_id"]: dict(r) for r in conn.execute("SELECT * FROM digest_state")}


def save_state(conn, managers, today):
    conn.executemany(
        """
        INSERT INTO digest_state (manager_id, total_sold, n_funds, digest_date) VALUES (?, ?, ?, ?)
        ON CONFLICT (manager_id) DO UPDATE SET total_sold = excluded.total_sold,
            n_funds = excluded.n_funds, digest_date = excluded.digest_date
        """, [(m["manager_id"], m["total_sold"], m["n_funds"], today.isoformat()) for m in managers])


def _cell(value):
    return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def _amount(value):
    return "" if value is None else f"{value:,}"


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return lines


def render(today, since, launch_rows, changed, first_digest, n_tracked, status, stale):
    lines = [f"# FundTracker digest, {today.isoformat()}", ""]
    lines += [f"## Hedge fund launches since {since}, score {MIN_SCORE} or more", ""]
    if launch_rows:
        lines += _table(("Date", "Score", "Launch", "Manager", "Raised", "State", "EDGAR"), [
            (r["window_start"], r["newness_score"], _launch_name(r), r["manager_name"],
             _amount(r["fund_total_amount_sold_num"]), r["fund_state"],
             f"[filing]({filing_folder_url(r['anchor_cik'], r['launch_id'])})") for r in launch_rows])
    else:
        lines.append("None.")
    lines += ["", f"## Emerging managers that passed {RAISED_LINE:,} raised or added a vehicle", ""]
    if first_digest:
        lines.append(f"First digest: recorded {n_tracked:,} managers to compare against next time.")
    elif changed:
        lines += _table(("Score", "Manager", "First seen", "Raised", "Funds", "City and state", "Change"), [
            (m["score"], m["display_name"], m["first_seen_date"],
             f"{_amount(m['was_sold'] or 0)} to {_amount(m['total_sold'] or 0)}",
             f"{m['was_funds']} to {m['n_funds']}",
             ", ".join(p for p in (m["city"], m["state"] or m["country"]) if p),
             "; ".join(m["what"])) for m in changed])
    else:
        lines.append("None since the last digest.")
    lines += ["", f"Data: daily index loaded to {status['last_daily'] or 'none'}, "
                  f"derive last run {status['last_derive'] or 'never'}, {status['filings']:,} filings"
                  + (". Stale: run fundtracker refresh." if stale else "."), ""]
    return "\n".join(lines)


def _launch_name(row):
    n = len(json.loads(row["fund_ciks"]))
    return (row["display_name"] or "") + (f" (+{n - 1})" if n > 1 else "")


def write(conn, path, today, since):
    """Write the digest to path, record the managers seen in digest_state, and return
    (number of launches, number of managers changed)."""
    state = read_state(conn)
    managers = tracked_managers(conn, today)
    launch_rows = launches(conn, since)
    changed = changes(managers, state, today) if state else []
    text = render(today, since, launch_rows, changed, not state, len(managers),
                  repo.site_status(conn), jobs.is_stale(conn, today))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    with conn:
        save_state(conn, managers, today)
    return len(launch_rows), len(changed)
