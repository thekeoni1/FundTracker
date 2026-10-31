"""The user's outreach sheet, a Google Sheet with one tab named MASTER: rows out, status back.

The sheet is the outreach tracker for every field the user recruits in;
FundTracker is one source feeding it. sheet_rows turns emerging managers
into rows in the sheet's column order, one per named person, and
write_sheet_csv writes them without a header so Google Sheets' File, Import,
"Append to current sheet" adds them as they are. FT KEY holds the manager's
EDGAR CIK, the key pull matches on when it reads the tab back.

The sheet holds other people's names and personal emails. pull keeps only
FT KEY, NAME, STATUS and LAST ACTION and never logs a row.
"""

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from fundtracker.db.repo import pad_cik
from fundtracker.web.links import linkedin_people

SHEET_COLUMNS = ("NAME", "NOTES", "STATUS", "LAST ACTION", "FIELD", "FIRM", "SENIORITY", "HOOK",
                 "LINKEDIN", "CONTACT", "PERSONAL EMAIL", "FT KEY")
# What a new row starts as.
NEW_STATUS = "To Contact"
FIELD = "HF"
# The columns pull keeps, by header, and the outreach table column each goes to.
PULLED_COLUMNS = {"FT KEY": "ft_key", "NAME": "name", "STATUS": "status", "LAST ACTION": "last_action"}
# Seconds to wait for a published sheet.
FETCH_TIMEOUT = 30


def sheet_rows(managers):
    """One row per person in each manager's people JSON, or one row with NAME blank when
    there are none. managers are manager rows as repo.get_manager returns them."""
    rows = []
    for m in managers:
        firm = m.get("display_name") or m["manager_id"]
        names = [p["name"] for p in json.loads(m.get("people") or "[]")] or [""]
        for name in names:
            row = dict.fromkeys(SHEET_COLUMNS, "")
            row.update({"NAME": name, "NOTES": notes(m), "STATUS": NEW_STATUS, "FIELD": FIELD,
                        "FIRM": firm, "LINKEDIN": linkedin_people(name, firm),
                        "FT KEY": m.get("edgar_cik") or ""})
            rows.append([row[c] for c in SHEET_COLUMNS])
    return rows


def notes(m):
    """A one-line summary: "FundTracker: raised 12,000,000; first seen 2025-06-03;
    website example.com; RIA, 3 employees", each part only when present."""
    parts = []
    if m.get("total_sold"):
        parts.append(f"raised {m['total_sold']:,}")
    if m.get("first_seen_date"):
        parts.append(f"first seen {m['first_seen_date']}")
    website = (m.get("website") or "").strip()
    for scheme in ("https://", "http://"):
        if website.lower().startswith(scheme):
            website = website[len(scheme):]
    if website:
        parts.append(f"website {website.rstrip('/').lower()}")
    if m.get("adviser_kind"):
        adviser = m["adviser_kind"].upper()
        if m.get("employees") is not None:
            adviser += f", {m['employees']:,} employees"
        parts.append(adviser)
    return "FundTracker: " + "; ".join(parts) if parts else "FundTracker"


def write_sheet_csv(buffer, rows):
    """Write rows to a text buffer as CSV, with no header row."""
    csv.writer(buffer).writerows(rows)


def pull(conn, source, fetch=None):
    """Replace the outreach table with the sheet's keyed rows; return (rows kept, rows skipped).

    source is a path to the MASTER tab downloaded as CSV, or an https URL of the tab
    published as CSV, read through fetch(url) -> bytes (default requests.get). Columns
    are matched by header name. Rows with a blank FT KEY are skipped. A manager with
    several people has several rows under one key: the table keeps one row per key,
    the names joined, with the status of the last row past To Contact (or the last
    row when none is). Statuses outside the sheet's list are kept as written. An FT KEY
    that Sheets turned into a number is padded back to ten digits.
    """
    text = _read(source, fetch)
    reader = csv.reader(io.StringIO(text))
    header = [h.strip().upper() for h in next(reader, [])]
    if "FT KEY" not in header:
        raise ValueError("the outreach CSV has no FT KEY column; add it as the sheet's twelfth column")
    index = {column: header.index(h) for h, column in PULLED_COLUMNS.items() if h in header}
    by_key, names, kept, skipped = {}, {}, 0, 0
    for values in reader:
        row = {c: (values[i].strip() if i < len(values) else "") for c, i in index.items()}
        if not row["ft_key"]:
            skipped += 1
            continue
        kept += 1
        # Sheets reads a pasted CIK as a number and drops its leading zeros.
        key = pad_cik(row["ft_key"])
        if row.get("name"):
            names.setdefault(key, {})[row["name"]] = None
        earlier = by_key.get(key)
        if earlier is None or _contacted(row) or not _contacted(earlier):
            by_key[key] = row
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        conn.execute("DELETE FROM outreach")
        conn.executemany(
            "INSERT INTO outreach (ft_key, name, status, last_action, fetched_at) VALUES (?, ?, ?, ?, ?)",
            [(key, ", ".join(names.get(key, ())) or None, r.get("status") or None,
              r.get("last_action") or None, fetched_at) for key, r in by_key.items()])
    return kept, skipped


def _contacted(row):
    """True for a row whose status is past To Contact."""
    return row.get("status") not in ("", None, NEW_STATUS)


def _read(source, fetch):
    if isinstance(source, str) and source.lower().startswith("https://"):
        data = (fetch or _fetch)(source)
    else:
        data = Path(source).read_bytes()
    return data.decode("utf-8-sig")


def _fetch(url):
    """The only network call in FundTracker that is not to the SEC."""
    import requests

    response = requests.get(url, timeout=FETCH_TIMEOUT)
    response.raise_for_status()
    return response.content
