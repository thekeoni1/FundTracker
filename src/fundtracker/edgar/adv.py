"""The SEC's monthly Form ADV data files: discover and read.

Reference: docs/edgar-formd-reference.md section 8. The page links one zip
of registered investment advisers (RIA) and one of exempt reporting
advisers (ERA) per month. Names vary month to month (ia100226.zip,
ia09012026-registered.zip, ia08032026_1.zip, 020118-exempt.zip), so files
are found by pattern: the date in the name gives the month, "exempt" in the
name the kind. Each zip holds one CSV whose headers are Form ADV item labels.
"""

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import NamedTuple
from urllib.parse import urljoin

from fundtracker.normalize import none_to_null

ADV_PAGE = ("https://www.sec.gov/data-research/sec-markets-data/"
            "information-about-registered-investment-advisers-exempt-reporting-advisers")
RIA, ERA = "ria", "era"
KINDS = (RIA, ERA)

# A zip under one of the page's folders (they all contain this phrase), named
# with an optional "ia", the date as MMDDYY or MMDDYYYY, and any suffix.
_ZIP_LINK = re.compile(
    r"""href=["']([^"']*information-about-registered-investment-advisers[^"'/]*/"""
    r"""((?:ia)?(\d{8}|\d{6})(?!\d)[^"'/]*\.zip))["']""", re.I)
# The CSVs are Windows-1252 (Ä, non-breaking spaces in names).
CSV_ENCODING = "cp1252"


class AdvFile(NamedTuple):
    month: str  # 2026-10, the ingest_log key with the kind
    kind: str   # ria or era
    url: str


def _file_date(digits):
    """The date in a file name: MMDDYY or MMDDYYYY. None if it is not a date."""
    year = int(digits[4:]) + (2000 if len(digits) == 6 else 0)
    try:
        return date(year, int(digits[:2]), int(digits[2:4]))
    except ValueError:
        return None


def list_adv_files(html, base_url=ADV_PAGE):
    """[(month, kind, url)] for every adviser file the page links, ascending.

    When a month has more than one file of a kind (mid-month extras in 2010,
    2011 and 2017), the one with the latest date in its name wins.
    """
    best = {}
    for m in _ZIP_LINK.finditer(html):
        href, name, digits = m.groups()
        day = _file_date(digits)
        if day is None:
            continue
        kind = ERA if "exempt" in name.lower() else RIA
        key = (f"{day:%Y-%m}", kind)
        if key not in best or day > best[key][0]:
            best[key] = (day, urljoin(base_url, href))
    return [AdvFile(month, kind, url) for (month, kind), (_day, url) in sorted(best.items())]


def fetch_adv_files(client):
    """Fetch the page and return every file it links, ascending."""
    response = client.get(ADV_PAGE)
    response.raise_for_status()
    return list_adv_files(response.text)


# ---------------------------------------------------------------------------
# Reading a file

@dataclass(frozen=True)
class AdviserRow:
    crd: str
    kind: str
    sec_number: str = None
    firm_type: str = None
    cik: str = None
    name: str = None
    legal_name: str = None
    street1: str = None
    city: str = None
    state: str = None
    country: str = None
    postal_code: str = None
    phone: str = None
    website: str = None
    status: str = None
    latest_filing_date: str = None
    employees: int = None            # 5A, registered only
    advisory_employees: int = None   # 5B(1), registered only
    raum: int = None                 # 5F(2)(c), registered only
    accounts: int = None             # 5F(2)(d), registered only
    advises_private_funds: bool = None  # 7B
    n_private_funds: int = None
    any_hedge_funds: bool = None
    n_hedge_funds: int = None
    private_fund_assets: int = None


# CSV header -> (AdviserRow field, conversion). Columns missing from a file
# (Item 5 in the exempt file) give None.
COLUMNS = {
    "Organization CRD#": ("crd", "text"),
    "SEC#": ("sec_number", "text"),
    "Firm Type": ("firm_type", "text"),
    "CIK#": ("cik", "text"),
    "Primary Business Name": ("name", "text"),
    "Legal Name": ("legal_name", "text"),
    "Main Office Street Address 1": ("street1", "text"),
    "Main Office City": ("city", "text"),
    "Main Office State": ("state", "text"),
    "Main Office Country": ("country", "text"),
    "Main Office Postal Code": ("postal_code", "text"),
    "Main Office Telephone Number": ("phone", "text"),
    "Website Address": ("website", "text"),
    "SEC Current Status": ("status", "text"),
    "Latest ADV Filing Date": ("latest_filing_date", "date"),
    "5A": ("employees", "int"),
    "5B(1)": ("advisory_employees", "int"),
    "5F(2)(c)": ("raum", "money"),
    "5F(2)(d)": ("accounts", "int"),
    "7B": ("advises_private_funds", "flag"),
    "Count of Private Funds - 7B(1)": ("n_private_funds", "int"),
    "Any Hedge Funds": ("any_hedge_funds", "flag"),
    "Total number of Hedge funds": ("n_hedge_funds", "int"),
    "Total Gross Assets of Private Funds": ("private_fund_assets", "money"),
}


def parse_money(text):
    """An amount as the files write it, padded and with commas: "    36,321,031.00" -> 36321031.
    Cents are rounded to the dollar. None when empty or not a number."""
    text = none_to_null(text)
    if text is None:
        return None
    try:
        return int(Decimal(text.replace(",", "").replace("$", "")).to_integral_value())
    except InvalidOperation:
        return None


def parse_int(text):
    """A padded count: "                   2" -> 2. None when empty or not a number."""
    return parse_money(text)


def parse_flag(text):
    """Y -> True, N -> False, anything else None."""
    return {"Y": True, "N": False}.get((text or "").strip().upper())


def parse_us_date(text):
    """MM/DD/YYYY as an ISO date, else None."""
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", (text or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
    except ValueError:
        return None


_CONVERT = {"text": none_to_null, "int": parse_int, "money": parse_money, "flag": parse_flag,
            "date": parse_us_date}


def csv_bytes(data):
    """The CSV inside a downloaded zip, or data itself when it is already a CSV."""
    if not data.startswith(b"PK"):
        return data
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV in the zip, found {names}")
        return z.read(names[0])


def read_adv_csv(data, kind):
    """Yield an AdviserRow per adviser in a monthly file (the zip or its CSV, as bytes).

    Rows without a CRD are skipped. The file's own name says nothing reliable
    about its month (ia100226.zip holds ia09012026.csv), so the caller keeps
    the month from the link.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}: {kind!r}")
    text = csv_bytes(data).decode(CSV_ENCODING, errors="replace")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader)
    positions = [(header.index(col), field, _CONVERT[conv])
                 for col, (field, conv) in COLUMNS.items() if col in header]
    if not any(field == "crd" for _i, field, _c in positions):
        raise ValueError("no Organization CRD# column; not a Form ADV adviser file")
    for values in reader:
        fields = {field: convert(values[i]) if i < len(values) else None
                  for i, field, convert in positions}
        if fields["crd"]:
            yield AdviserRow(kind=kind, **fields)
