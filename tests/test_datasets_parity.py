"""The same filing loaded from a data set zip and from its XML writes the same rows."""

from pathlib import Path

import pytest

from fundtracker.db import repo
from fundtracker.edgar import datasets
from fundtracker.edgar.formd_xml import parse_formd

FIXTURES = Path(__file__).parent / "fixtures"
TABLES = ("filing", "issuer", "offering", "related_person", "recipient", "signature")

# Columns allowed to differ between the two sources, with the reason.
ALLOWED_DIFFERENCES = {
    "filing": {
        "source": "records which loader wrote the row: 'dataset' or 'xml'",
        "loaded_at": "time of the first load, different by construction",
        "file_num": "only the data set carries the file number; primary_doc.xml has none",
    },
}

CASES = [
    # quarter, accession, filing date from the daily index (the XML has none)
    ("2015q2", "0001635511-15-000001", "2015-06-15"),
    ("2010q2", "0000919574-10-003893", "2010-06-15"),
]


def rows_for(conn, accession):
    result = {}
    for table in TABLES:
        allowed = ALLOWED_DIFFERENCES.get(table, {})
        rows = conn.execute(f"SELECT * FROM {table} WHERE accession = ? ORDER BY rowid",
                            (accession,)).fetchall()
        result[table] = [{k: r[k] for k in r.keys() if k not in allowed} for r in rows]
    return result


@pytest.mark.parametrize("quarter, accession, filing_date", CASES)
def test_dataset_and_xml_write_the_same_rows(tmp_path, quarter, accession, filing_date):
    from_dataset = repo.connect(tmp_path / "dataset.db")
    for filing in datasets.read_quarter(FIXTURES / "datasets" / f"{quarter}_trimmed.zip"):
        repo.upsert_filing(from_dataset, filing)

    from_xml = repo.connect(tmp_path / "xml.db")
    xml = (FIXTURES / "formd" / f"{accession}.xml").read_bytes()
    repo.upsert_filing(from_xml, parse_formd(xml, accession, filing_date=filing_date))

    a, b = rows_for(from_dataset, accession), rows_for(from_xml, accession)
    assert a["filing"], "accession missing from the trimmed zip"
    for table in TABLES:
        assert a[table] == b[table], table

    # The allowed differences are real differences, not leftovers.
    ds_row = from_dataset.execute("SELECT source, file_num FROM filing WHERE accession = ?",
                                  (accession,)).fetchone()
    xml_row = from_xml.execute("SELECT source, file_num FROM filing WHERE accession = ?",
                               (accession,)).fetchone()
    assert tuple(ds_row) == ("dataset", ds_row["file_num"]) and ds_row["file_num"]
    assert tuple(xml_row) == ("xml", None)


@pytest.mark.parametrize("zip_path", sorted((FIXTURES / "datasets").glob("*_trimmed.zip")),
                         ids=lambda p: p.name)
def test_fixture_zip_headers_are_all_mapped_or_ignored(zip_path):
    for table, cols in datasets.tsv_headers(zip_path).items():
        unknown = [c for c in cols
                   if c not in datasets.COLUMNS[table] and c not in datasets.IGNORED_COLUMNS[table]]
        assert unknown == [], f"{zip_path.name} {table}: new columns {unknown}"


@pytest.mark.parametrize("quarter, size", [("2015q2", 201), ("2010q2", 201)])
def test_trimmed_zip_loads_whole(tmp_path, quarter, size):
    conn = repo.connect(tmp_path / "t.db")
    filings = list(datasets.read_quarter(FIXTURES / "datasets" / f"{quarter}_trimmed.zip"))
    assert len(filings) == size
    for f in filings:
        repo.upsert_filing(conn, f)
    assert conn.execute("SELECT count(*) FROM filing").fetchone()[0] == size
    assert conn.execute("SELECT count(*) FROM offering").fetchone()[0] == size
