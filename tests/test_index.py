from datetime import date
from pathlib import Path

import pytest

from fundtracker.edgar import index

FIXTURE = Path(__file__).parent / "fixtures" / "index" / "form.20261027.trimmed.idx"


@pytest.fixture(scope="module")
def rows():
    return index.parse_form_index(FIXTURE.read_text(encoding="utf-8"))


def test_every_data_row_parses(rows):
    assert len(rows) == 60
    forms = [r.form_type for r in rows]
    assert forms.count("D") == 40 and forms.count("D/A") == 10


def test_row_fields(rows):
    gef = [r for r in rows if r.company_name.startswith("GEF US Climate")]
    assert [(r.company_name, r.cik) for r in gef] == [
        ("GEF US Climate Solutions Fund III, L.P.", "2161535"),
        ("GEF US Climate Solutions Parallel Fund III, L.P.", "2161347"),
    ]
    assert {r.accession for r in gef} == {"0002161535-26-000001"}
    assert gef[0].date_filed == "2026-10-27"
    assert gef[0].file_name == "edgar/data/2161535/0002161535-26-000001.txt"


def test_form_types_with_spaces_and_truncation(rows):
    forms = {r.form_type for r in rows}
    assert "1-A POS" in forms
    assert "SCHEDULE 13G/A" in forms
    assert "SEC STAFF ACTIO" in forms  # truncated to the column width in the file


def test_date_filed_can_differ_from_the_index_date(rows):
    (ta,) = [r for r in rows if r.form_type == "TA-1/A"]
    assert ta.date_filed == "2026-09-29"


def test_long_company_names_and_both_layouts():
    """Pre-2025 rows (12-wide form column) and current rows (17-wide), and a name that fills its column."""
    text = "\n".join([
        "Form Type   Company Name                                                  CIK",
        "      Date Filed  File Name",
        "-" * 120,
        # 2015 layout, from form.20150616.idx
        "D           AQR OFFSHORE MULTI-STRATEGY FUND XIV, L.P.                    1635511     "
        "20150615    edgar/data/1635511/0001635511-15-000001.txt",
        # 2026 layout, company name running up to a single space before the CIK
        "D                " + "A" * 61 + " 2158817     20261028    "
        "edgar/data/2158817/0002158817-26-000001.txt",
        # double spaces inside a company name survive
        "D/A              Fund  With  Gaps LP                                           123         "
        "20261027    edgar/data/123/0000000123-26-000001.txt",
        "",
    ])
    rows = index.parse_form_index(text)
    assert [(r.form_type, r.cik, r.date_filed) for r in rows] == [
        ("D", "1635511", "2015-06-15"), ("D", "2158817", "2026-10-28"), ("D/A", "123", "2026-10-27")]
    assert rows[0].company_name == "AQR OFFSHORE MULTI-STRATEGY FUND XIV, L.P."
    assert rows[1].company_name == "A" * 61
    assert rows[2].company_name == "Fund  With  Gaps LP"


def test_not_an_index():
    with pytest.raises(ValueError):
        index.parse_form_index("<html>Not Found</html>")


def test_formd_rows_deduplicates_co_issuers(rows):
    formd = index.formd_rows(rows)
    assert len(formd) == 49  # 40 D rows, one of them GEF's second listing, plus 10 D/A
    gef = [r for r in formd if r.accession == "0002161535-26-000001"]
    assert len(gef) == 1 and gef[0].cik == "2161535"
    assert {r.form_type for r in formd} == {"D", "D/A"}
    assert len({r.accession for r in formd}) == len(formd)


@pytest.mark.parametrize("day, url", [
    (date(2026, 10, 27), "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR4/form.20261027.idx"),
    ("2015-06-16", "https://www.sec.gov/Archives/edgar/daily-index/2015/QTR2/form.20150616.idx"),
    ("2026-03-31", "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR1/form.20260331.idx"),
    ("2026-07-01", "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/form.20260701.idx"),
])
def test_form_index_url(day, url):
    assert index.form_index_url(day) == url


def test_primary_doc_url_matches_the_axiaris_folder():
    assert index.primary_doc_url("2158817", "0002158817-26-000001") == (
        "https://www.sec.gov/Archives/edgar/data/2158817/000215881726000001/primary_doc.xml")
    # Agent-filed: the folder is under the issuer CIK, not the accession prefix.
    assert index.primary_doc_url("0002159130", "0000919574-26-007205") == (
        "https://www.sec.gov/Archives/edgar/data/2159130/000091957426007205/primary_doc.xml")
