"""The Excel workbook `new --xlsx` writes: Launches, Funds, Reasons, About.

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

from fundtracker import derive, newness
from fundtracker.edgar.index import filing_folder_url

DATE_FORMAT = "yyyy-mm-dd"
AMOUNT_FORMAT = "#,##0"
# Score colour scale: red at 0, yellow at 50, green at 100.
SCORE_COLOURS = ("F8696B", "FFEB84", "63BE7B")
RULE_506 = {"06b": "506(b)", "06c": "506(c)"}

# Fund columns written as dates or as amounts on the Funds sheet.
FUND_DATE_COLUMNS = {"first_filing_date", "latest_filing_date"}
FUND_AMOUNT_COLUMNS = {"total_amount_sold_num", "n_investors", "min_investment_num",
                       "total_offering_amount_num"}


def _date(text):
    return date.fromisoformat(text) if text else None


def _exemptions(funds):
    """Every exemption code of the funds, in first-seen order."""
    seen = []
    for f in funds:
        for code in json.loads(f.get("exemptions") or "[]"):
            if code not in seen:
                seen.append(code)
    return seen


def _rule_506(codes):
    return ", ".join(RULE_506[c] for c in sorted(RULE_506) if c in codes)


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
    ("Rule 506", 15, None, lambda r, fs: _rule_506(_exemptions(fs))),
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
    (a datetime), since, type, min_score and last_daily (an ISO date or None).
    """
    funds_of = {}
    for f in funds:
        funds_of.setdefault(f["launch_id"], []).append(f)
    wb = Workbook()
    _launches_sheet(wb.active, launches, funds_of)
    _funds_sheet(wb.create_sheet("Funds"), funds)
    _reasons_sheet(wb.create_sheet("Reasons"), launches)
    _about_sheet(wb.create_sheet("About"), launches, funds, meta)
    wb.save(path)


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


def _launches_sheet(ws, launches, funds_of):
    ws.title = "Launches"
    _header(ws, [c[0] for c in LAUNCH_COLUMNS], [c[1] for c in LAUNCH_COLUMNS])
    edgar = [c[0] for c in LAUNCH_COLUMNS].index("EDGAR") + 1
    for row, launch in enumerate(launches, start=2):
        launch_funds = funds_of.get(launch["launch_id"], [])
        for col, (_h, _w, fmt, value) in enumerate(LAUNCH_COLUMNS, start=1):
            cell = ws.cell(row=row, column=col, value=value(launch, launch_funds))
            if fmt and not isinstance(cell.value, str):
                cell.number_format = fmt
        link = ws.cell(row=row, column=edgar)
        link.hyperlink = filing_folder_url(launch["anchor_cik"], launch["launch_id"])
        link.style = "Hyperlink"
    _finish_table(ws)
    if launches:
        score = get_column_letter([c[0] for c in LAUNCH_COLUMNS].index("Score") + 1)
        low, mid, high = SCORE_COLOURS
        ws.conditional_formatting.add(
            f"{score}2:{score}{len(launches) + 1}",
            ColorScaleRule(start_type="num", start_value=0, start_color=low,
                           mid_type="num", mid_value=50, mid_color=mid,
                           end_type="num", end_value=100, end_color=high))


def _funds_sheet(ws, funds):
    columns = [c for c in (funds[0] if funds else ()) if c not in ("launch_id", "launch_score")]
    headers = ["Launch ID", "Launch score", "EDGAR"] + columns
    _header(ws, headers, [22, 12, 9] + [max(12, min(40, len(c) + 4)) for c in columns])
    for row, f in enumerate(funds, start=2):
        values = [f["launch_id"], f["launch_score"], "first filing"]
        for c in columns:
            v = f[c]
            if c in FUND_DATE_COLUMNS:
                v = _date(v)
            elif c == "exemptions":
                v = ", ".join(json.loads(v or "[]"))
            values.append(v)
        for col, v in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col, value=v)
            name = headers[col - 1]
            if name in FUND_DATE_COLUMNS and v is not None:
                cell.number_format = DATE_FORMAT
            elif name in FUND_AMOUNT_COLUMNS and v is not None:
                cell.number_format = AMOUNT_FORMAT
        link = ws.cell(row=row, column=3)
        link.hyperlink = filing_folder_url(f["cik"], f["first_accession"])
        link.style = "Hyperlink"
    _finish_table(ws)


def _reasons_sheet(ws, launches):
    _header(ws, ["Launch ID", "Launch", "Code", "Reason", "Points", "Detail"],
            [22, 46, 20, 50, 8, 70])
    for launch in launches:
        for r in _reasons(launch):
            ws.append([launch["launch_id"], launch["display_name"], r["code"],
                       newness.REASON_LABELS.get(r["code"], r["code"]), r["points"], r["detail"]])
    _finish_table(ws)


def _about_sheet(ws, launches, funds, meta):
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 60
    ws.column_dimensions["C"].width = 10
    rows = [
        ("FundTracker new launches", None),
        ("Generated at", meta["generated_at"].strftime("%Y-%m-%d %H:%M")),
        ("Launches first filed since", _date(meta["since"])),
        ("Fund type", "Hedge Fund only" if meta["type"] == "hedge" else "every pooled fund type"),
        ("Minimum score", meta["min_score"]),
        ("Launches", len(launches)),
        ("Funds in them", len(funds)),
        ("Last daily index loaded", _date(meta.get("last_daily"))),
        (None, None),
        ("Launch window, days", derive.LAUNCH_WINDOW_DAYS),
        ("Entity key cap, CIKs", derive.ENTITY_KEY_CAP),
        ("Phone and address key cap, CIKs", derive.SERVICE_PROVIDER_CAP),
        ("Funds that make a manager established", newness.ESTABLISHED_FUNDS),
        ("Minimum investment band", "{:,} to {:,}".format(*newness.MIN_INVESTMENT_BAND)),
        (None, None),
    ]
    for label, value in rows:
        ws.append([label, value])
        if isinstance(value, date):
            ws.cell(row=ws.max_row, column=2).number_format = DATE_FORMAT
    ws["A1"].font = Font(bold=True)
    ws.append(["Weights", "Reason", "Points"])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for code, points in newness.WEIGHTS.items():
        ws.append([code, newness.REASON_LABELS.get(code, ""), points])
