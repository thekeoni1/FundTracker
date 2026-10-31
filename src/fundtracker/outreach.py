"""Rows for the user's outreach sheet, a Google Sheet with one tab named MASTER.

The sheet is the outreach tracker for every field the user recruits in;
FundTracker is one source feeding it. sheet_rows turns emerging managers
into rows in the sheet's column order, one per named person, and
write_sheet_csv writes them without a header so Google Sheets' File, Import,
"Append to current sheet" adds them as they are. FT KEY holds the manager's
EDGAR CIK, the key the read-back matches on.
"""

import csv
import json

from fundtracker.web.links import linkedin_people

SHEET_COLUMNS = ("NAME", "NOTES", "STATUS", "LAST ACTION", "FIELD", "FIRM", "SENIORITY", "HOOK",
                 "LINKEDIN", "CONTACT", "PERSONAL EMAIL", "FT KEY")
# What a new row starts as.
NEW_STATUS = "To Contact"
FIELD = "HF"


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
