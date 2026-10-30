"""The daily form index: form.YYYYMMDD.idx (docs/edgar-formd-reference.md section 3).

Rows are parsed from the right, not by the header's column offsets. The
header is the same in every file since 2009, but from 2025 the data columns
sit five characters further right than it says (the Form Type column widened
from 12 to 17). Every row ends with CIK, an 8-digit Date Filed and an
edgar/ path, and form types never contain two spaces in a row, so a
right-anchored match reads both layouts.
"""

import re
from dataclasses import dataclass
from datetime import date

from fundtracker.normalize import parse_date

FORMD_TYPES = ("D", "D/A")

_ROW = re.compile(
    r"^(?P<form_type>\S(?:\S| (?! ))*?)\s{2,}(?P<company>.*?)\s+(?P<cik>\d+)\s+"
    r"(?P<date>\d{8})\s+(?P<file>edgar/\S+)\s*$"
)
_ACCESSION = re.compile(r"(\d{10}-\d{2}-\d{6})\.txt$")


@dataclass(frozen=True)
class IndexRow:
    form_type: str
    company_name: str
    cik: str  # as listed, no zero padding
    date_filed: str  # ISO; can be earlier than the index file's own date
    file_name: str

    @property
    def accession(self):
        m = _ACCESSION.search(self.file_name)
        return m.group(1) if m else None


def form_index_url(day):
    """URL of the form index for day (a date or an ISO string)."""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    quarter = (day.month - 1) // 3 + 1
    return (f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{quarter}/"
            f"form.{day:%Y%m%d}.idx")


def parse_form_index(text):
    """Every data row after the line of dashes. Lines that do not parse are skipped."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith("---")) + 1
    except StopIteration:
        raise ValueError("no line of dashes: not a form index") from None
    rows = []
    for line in lines[start:]:
        m = _ROW.match(line.strip())
        if m:
            rows.append(IndexRow(
                form_type=m["form_type"],
                company_name=m["company"].strip(),
                cik=m["cik"],
                date_filed=parse_date(m["date"]),
                file_name=m["file"],
            ))
    return rows


def formd_rows(rows):
    """D and D/A rows, one per accession, in index order.

    A co-issued filing is listed once per issuer CIK; the first listing is
    kept and its CIK serves as the cik_hint for parsing.
    """
    seen = set()
    result = []
    for row in rows:
        if row.form_type in FORMD_TYPES and row.accession and row.accession not in seen:
            seen.add(row.accession)
            result.append(row)
    return result


def filing_folder_url(cik, accession):
    """The filing's folder on EDGAR, built from the issuer CIK, not the accession prefix."""
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"


def company_filings_url(cik):
    """The company's Form D filings on EDGAR."""
    return ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&CIK={int(cik):010d}&type=D&dateb=&owner=include&count=40")


def primary_doc_url(cik, accession):
    """The filing's primary_doc.xml."""
    return filing_folder_url(cik, accession) + "primary_doc.xml"
