"""The Excel workbooks: `new --xlsx` (Launches, Funds, Reasons, About) and
`emerging --xlsx` (Managers, Funds, People, Reasons, About).

The only module that uses openpyxl. It takes rows the CLI has already read
(repo.launches_since and repo.funds_for_launches) and knows nothing about
SQLite. Dates are written as real dates, amounts as numbers with thousands
separators, and Indefinite as text, so the user can sort and filter in Excel.
"""

import json
from datetime import date

from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from fundtracker import classify, derive, newness, traction
from fundtracker.edgar.index import company_filings_url, filing_folder_url

DATE_FORMAT = "yyyy-mm-dd"
AMOUNT_FORMAT = "#,##0"
# Score colour scale: red at 0, yellow at 50, green at 100.
SCORE_COLOURS = ("F8696B", "FFEB84", "63BE7B")

# Fund columns written as dates or as amounts on the Funds sheet.
FUND_DATE_COLUMNS = {"first_filing_date", "latest_filing_date"}
FUND_AMOUNT_COLUMNS = {"total_amount_sold_num", "n_investors", "min_investment_num",
                       "total_offering_amount_num", "latest_total_amount_sold", "latest_n_investors"}
# Fund columns derive keeps for manager stats; the Funds sheets leave them out.
FUND_SKIPPED_COLUMNS = {"latest_sold", "latest_investors", "latest_is_indefinite", "latest_remaining",
                        "latest_is_amendment", "latest_city"}


def _date(text):
    return date.fromisoformat(text) if text else None


def _exemptions(funds):
    """Every exemption code of the funds, Rule 506 first (classify.sort_exemptions)."""
    return classify.sort_exemptions({code for f in funds
                                     for code in json.loads(f.get("exemptions") or "[]")})


def _offering(launch):
    if launch["fund_total_offering_amount_is_indefinite"]:
        return "Indefinite"
    return launch["fund_total_offering_amount_num"]


def _reasons(launch):
    return json.loads(launch["newness_reasons"] or "[]")


def _platform(launch):
    return next((r["detail"] for r in _reasons(launch) if r["code"] == "platform"), "")


# Launches sheet: (header, width, number format, value of a launch row and its funds).
# Fund terms (minimum, offering, sold, investors, state) are the anchor fund's,
# the fund that filed the launch's first D.
LAUNCH_COLUMNS = (
    ("Date", 11, DATE_FORMAT, lambda r, fs: _date(r["window_start"])),
    ("Score", 7, None, lambda r, fs: r["newness_score"]),
    ("Launch", 46, None, lambda r, fs: r["display_name"]),
    ("Manager", 36, None, lambda r, fs: r["manager_name"]),
    ("Funds", 7, None, lambda r, fs: len(json.loads(r["fund_ciks"]))),
    ("Type", 20, None, lambda r, fs: r["fund_type"]),
    ("Exemptions", 16, None, lambda r, fs: ", ".join(_exemptions(fs))),
    ("Rule 506", 15, None, lambda r, fs: classify.rule_506(_exemptions(fs))),
    ("Min investment", 15, AMOUNT_FORMAT, lambda r, fs: r["fund_min_investment_num"]),
    ("Offering amount", 17, AMOUNT_FORMAT, lambda r, fs: _offering(r)),
    ("Amount sold", 15, AMOUNT_FORMAT, lambda r, fs: r["fund_total_amount_sold_num"]),
    ("Investors", 10, AMOUNT_FORMAT, lambda r, fs: r["fund_n_investors"]),
    ("State", 7, None, lambda r, fs: r["fund_state"]),
    ("Platform", 40, None, lambda r, fs: _platform(r)),
    ("Reasons", 80, None, lambda r, fs: "; ".join(newness.reason_phrase(x) for x in _reasons(r))),
    ("EDGAR", 9, None, lambda r, fs: "filing"),
    ("Launch ID", 22, None, lambda r, fs: r["launch_id"]),
)


def write_workbook(path, launches, funds, meta):
    """Write the workbook to path.

    launches are repo.launches_since rows, best first; funds are
    repo.funds_for_launches rows for them; meta is a dict with generated_at
    (a datetime), since, type, min_score and last_daily (an ISO date or None),
    and from the site, filters (phrases for the About sheet).
    """
    funds_of = {}
    for f in funds:
        funds_of.setdefault(f["launch_id"], []).append(f)
    wb = Workbook()
    wb.active.title = "Launches"
    _table_sheet(wb.active, [c[:3] for c in LAUNCH_COLUMNS],
                 [[value(r, funds_of.get(r["launch_id"], [])) for _h, _w, _f, value in LAUNCH_COLUMNS]
                  for r in launches],
                 [filing_folder_url(r["anchor_cik"], r["launch_id"]) for r in launches])
    _funds_sheet(wb.create_sheet("Funds"), funds,
                 [("Launch ID", "launch_id", 22), ("Launch score", "launch_score", 12)])
    _table_sheet(wb.create_sheet("Reasons"), REASON_SHEET_COLUMNS[:1] + [("Launch", 46, None)]
                 + REASON_SHEET_COLUMNS[1:],
                 [[r["launch_id"], r["display_name"], x["code"],
                   newness.REASON_LABELS.get(x["code"], x["code"]), x["points"], x["detail"]]
                  for r in launches for x in _reasons(r)], score=False)
    _about_sheet(wb.create_sheet("About"), [
        ("FundTracker new launches", None),
        ("Generated at", meta["generated_at"].strftime("%Y-%m-%d %H:%M")),
        ("Launches first filed since", _date(meta["since"])),
        ("Fund type", "Hedge Fund only" if meta["type"] == "hedge" else "every pooled fund type"),
        ("Minimum score", meta["min_score"]),
        *_filters_row(meta),
        ("Launches", len(launches)),
        ("Funds in them", len(funds)),
        ("Last daily index loaded", _date(meta.get("last_daily"))),
        (None, None),
        ("Launch window, days", derive.LAUNCH_WINDOW_DAYS),
        ("Entity key cap, CIKs", derive.ENTITY_KEY_CAP),
        ("Adviser columns", "Form ADV, shown for high and medium confidence links only"),
        ("Adviser phone and address cap", derive.ADVISER_KEY_CAP),
        ("Phone and address key cap, CIKs", derive.SERVICE_PROVIDER_CAP),
        ("Funds that make a manager established", newness.ESTABLISHED_FUNDS),
        ("Minimum investment band", "{:,} to {:,}".format(*newness.MIN_INVESTMENT_BAND)),
    ], newness.WEIGHTS, newness.REASON_LABELS)
    wb.save(path)


# Reasons sheet columns after the row's ID column; the caller inserts the name column.
REASON_SHEET_COLUMNS = [("Launch ID", 22, None), ("Code", 20, None), ("Reason", 50, None),
                        ("Points", 8, None), ("Detail", 70, None)]


def _people_text(people):
    return "; ".join(f"{p['name']} ({', '.join(p['roles'])})" if p["roles"] else p["name"]
                     for p in people)


# Managers sheet of the emerging workbook: (header, width, number format, value of a
# scored repo.emerging_managers row).
MANAGER_COLUMNS = (
    ("Score", 7, None, lambda m: m["score"]),
    ("Manager", 40, None, lambda m: m["display_name"]),
    ("First seen", 11, DATE_FORMAT, lambda m: _date(m["first_seen_date"])),
    ("Age months", 8, None, lambda m: m["age_months"]),
    ("Funds", 7, None, lambda m: m["n_funds"]),
    ("Raised", 15, AMOUNT_FORMAT, lambda m: m["total_sold"]),
    ("Offering", 9, None, lambda m: m.get("offering_status")),
    ("Remaining", 15, AMOUNT_FORMAT, lambda m: m.get("total_remaining")),
    ("Investors", 10, AMOUNT_FORMAT, lambda m: m["total_investors"]),
    ("Amended", 9, None, lambda m: "yes" if m["has_amendment"] else "no"),
    ("Latest filing", 12, DATE_FORMAT, lambda m: _date(m["latest_filing_date"])),
    ("Types", 24, None, lambda m: ", ".join(json.loads(m["fund_types"] or "[]"))),
    ("Exemptions", 16, None, lambda m: ", ".join(_exemptions(m["funds"]))),
    ("City", 16, None, lambda m: m["city"]),
    ("State", 7, None, lambda m: m["state"]),
    ("Country", 16, None, lambda m: m["country"]),
    ("People", 60, None, lambda m: _people_text(json.loads(m["people"] or "[]"))),
    ("Prior firm", 40, None, lambda m: ", ".join(json.loads(m["prior_firms"] or "[]"))),
    ("Website", 30, None, lambda m: m.get("website")),
    ("Adviser", 12, None, lambda m: f"{m['adviser_kind'].upper()}, {m['adviser_confidence']}"
     if m.get("adviser_kind") else None),
    ("Adviser CRD", 11, None, lambda m: m.get("adviser_crd")),
    ("Employees", 10, AMOUNT_FORMAT, lambda m: m.get("employees")),
    ("RAUM", 15, AMOUNT_FORMAT, lambda m: m.get("raum")),
    ("Same adviser", 9, None, lambda m: m.get("adviser_siblings") or None),
    ("SEC hedge funds", 9, None, lambda m: m.get("adv_hedge_funds")),
    ("Reasons", 80, None, lambda m: "; ".join(traction.reason_phrase(x)
                                               for x in json.loads(m["reasons"]))),
    ("EDGAR", 9, None, lambda m: "filings"),
    ("Manager ID", 14, None, lambda m: m["manager_id"]),
)


def write_emerging_workbook(path, managers, funds, meta):
    """Write the emerging managers workbook to path.

    managers are scored rows from cli.emerging_rows (repo.emerging_managers
    plus score, reasons as JSON and age_months), best first; funds are their
    funds with manager_score; meta is a dict with generated_at, today,
    oldest, newest (dates), min_age, max_age, type, min_score, max_funds and
    last_daily, and from the site, filters.
    """
    names = {m["manager_id"]: m["display_name"] for m in managers}
    fund_names = {f["cik"]: f["name"] for f in funds}
    wb = Workbook()
    wb.active.title = "Managers"
    _table_sheet(wb.active, [c[:3] for c in MANAGER_COLUMNS],
                 [[value(m) for _h, _w, _f, value in MANAGER_COLUMNS] for m in managers],
                 [company_filings_url(m["edgar_cik"]) for m in managers])
    _funds_sheet(wb.create_sheet("Funds"), funds,
                 [("Manager ID", "manager_id", 14), ("Manager score", "manager_score", 12)])
    people = [(m, p) for m in managers for p in json.loads(m["people"] or "[]")]
    _table_sheet(wb.create_sheet("People"),
                 [("Manager", 40, None), ("Name", 30, None), ("Roles", 40, None), ("Fund", 46, None),
                  ("Filing date", 12, DATE_FORMAT), ("EDGAR", 9, None)],
                 [[m["display_name"], p["name"], ", ".join(p["roles"]), fund_names.get(p["cik"]),
                   _date(p["filing_date"]), "filing"] for m, p in people],
                 [filing_folder_url(p["cik"], p["accession"]) for _m, p in people], score=False)
    _table_sheet(wb.create_sheet("Reasons"),
                 [("Manager ID", 14, None), ("Manager", 40, None)] + REASON_SHEET_COLUMNS[1:],
                 [[m["manager_id"], names[m["manager_id"]], x["code"],
                   traction.TRACTION_LABELS.get(x["code"], x["code"]), x["points"], x["detail"]]
                  for m in managers for x in json.loads(m["reasons"])], score=False)
    _about_sheet(wb.create_sheet("About"), [
        ("FundTracker emerging managers", None),
        ("Generated at", meta["generated_at"].strftime("%Y-%m-%d %H:%M")),
        ("First seen from", meta["oldest"]),
        ("First seen to", meta["newest"]),
        ("Age window", f"{meta['min_age']} to {meta['max_age']} before {meta['today']}"),
        ("Fund type", "managers with a Hedge Fund vehicle" if meta["type"] == "hedge"
         else "every pooled fund type"),
        ("Minimum score", meta["min_score"]),
        *_filters_row(meta),
        ("Left out", f"over-cap organisations and managers with {meta['max_funds']} or more funds"),
        ("Managers", len(managers)),
        ("Funds of those managers", len(funds)),
        ("People named", len(people)),
        ("Last daily index loaded", _date(meta.get("last_daily"))),
        (None, None),
        ("Entity key cap, CIKs", derive.ENTITY_KEY_CAP),
        ("People kept per manager", derive.MAX_PEOPLE),
        ("Raised bands", "; ".join(
            f"{traction._band_text(low, high)}: {points:+d}" for low, high, points in traction.RAISED_BANDS)),
        ("Investors for the higher tier", traction.MANY_INVESTORS),
        ("Recent activity, months", traction.RECENT_MONTHS),
        ("Offering open if a fund filed within, whole months", derive.OPEN_MAX_AGE_MONTHS),
        ("Funds that count as large", traction.LARGE_FUNDS),
        ("Adviser employees above which the team counts as large", traction.LARGE_TEAM_MAX),
        ("Adviser RAUM above which the firm counts as large", traction.LARGE_RAUM_MAX),
        ("Adviser RAUM from which it is labelled", traction.MID_RAUM_MIN),
    ], traction.TRACTION_WEIGHTS, traction.TRACTION_LABELS)
    wb.save(path)


def _filters_row(meta):
    """The site's filters as one About row, when the workbook came from a page."""
    return [("Site filters", "; ".join(meta["filters"]))] if meta.get("filters") else []


def _table_sheet(ws, columns, rows, links=None, score=True):
    """Fill ws with a bold frozen header, one row per value list and an autofilter.

    columns are (header, width, number format); the format applies to every
    non-text value in the column. links, one URL per row, go on the EDGAR
    column. score adds the colour scale to the Score column when there is one.
    """
    headers = [c[0] for c in columns]
    _header(ws, headers, [c[1] for c in columns])
    for row, values in enumerate(rows, start=2):
        for col, ((_h, _w, fmt), v) in enumerate(zip(columns, values), start=1):
            cell = ws.cell(row=row, column=col, value=v)
            if fmt and v is not None and not isinstance(v, str):
                cell.number_format = fmt
    if links is not None and "EDGAR" in headers:
        col = headers.index("EDGAR") + 1
        for row, url in enumerate(links, start=2):
            if url:
                cell = ws.cell(row=row, column=col)
                cell.hyperlink = url
                cell.style = "Hyperlink"
    _finish_table(ws)
    if score and rows and "Score" in headers:
        letter = get_column_letter(headers.index("Score") + 1)
        low, mid, high = SCORE_COLOURS
        ws.conditional_formatting.add(
            f"{letter}2:{letter}{len(rows) + 1}",
            ColorScaleRule(start_type="num", start_value=0, start_color=low,
                           mid_type="num", mid_value=50, mid_color=mid,
                           end_type="num", end_value=100, end_color=high))


def _header(ws, headers, widths):
    ws.append(list(headers))
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"


def _finish_table(ws):
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions


def _funds_sheet(ws, funds, lead):
    """One row per fund: the lead (header, key, width) columns, an EDGAR link to its
    first filing, then every other fund column under its own name except
    FUND_SKIPPED_COLUMNS."""
    keys = {key for _h, key, _w in lead} | {"funds"} | FUND_SKIPPED_COLUMNS
    columns = [c for c in (funds[0] if funds else ()) if c not in keys]
    rows = []
    for f in funds:
        values = [f[key] for _h, key, _w in lead] + ["first filing"]
        for c in columns:
            v = f[c]
            if c in FUND_DATE_COLUMNS:
                v = _date(v)
            elif c == "exemptions":
                v = ", ".join(json.loads(v or "[]"))
            values.append(v)
        rows.append(values)
    formats = [DATE_FORMAT if c in FUND_DATE_COLUMNS else AMOUNT_FORMAT if c in FUND_AMOUNT_COLUMNS
               else None for c in columns]
    _table_sheet(ws, [(h, w, None) for h, _key, w in lead] + [("EDGAR", 9, None)]
                 + [(c, max(12, min(40, len(c) + 4)), fmt) for c, fmt in zip(columns, formats)],
                 rows, [filing_folder_url(f["cik"], f["first_accession"]) for f in funds], score=False)


def _about_sheet(ws, rows, weights, labels):
    """Label and value rows, then the weights table with each reason's label."""
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 60
    ws.column_dimensions["C"].width = 10
    for label, value in rows:
        ws.append([label, value])
        if isinstance(value, date):
            ws.cell(row=ws.max_row, column=2).number_format = DATE_FORMAT
    ws["A1"].font = Font(bold=True)
    ws.append([None, None])
    ws.append(["Weights", "Reason", "Points"])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for code, points in weights.items():
        ws.append([code, labels.get(code, ""), points])
