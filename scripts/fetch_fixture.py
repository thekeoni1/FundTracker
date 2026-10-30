"""Save one filing's primary_doc.xml as a test fixture.

Usage:
    python scripts/fetch_fixture.py <cik> <accession> "<what it exercises>"

Fetches https://www.sec.gov/Archives/edgar/data/<cik>/<accession without
dashes>/primary_doc.xml through EdgarClient.get_cached, writes it to
tests/fixtures/formd/<accession>.xml and adds a one-line entry to the README
in that folder. Run by hand; tests never call this.
"""

import argparse
import re
import sys
from pathlib import Path

from fundtracker.edgar.client import EdgarClient
from fundtracker.edgar.index import primary_doc_url

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "formd"
README = FIXTURE_DIR / "README.md"
README_HEADER = (
    "# Form D fixtures\n\n"
    "Real primary_doc.xml files from EDGAR, saved by scripts/fetch_fixture.py. "
    "One line per fixture: accession, issuer CIK, and what it exercises.\n\n"
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cik")
    parser.add_argument("accession", help="with dashes, e.g. 0002158817-26-000001")
    parser.add_argument("note", help="what the fixture exercises, one line")
    args = parser.parse_args(argv)

    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", args.accession):
        parser.error("accession must look like 0002158817-26-000001")

    body = EdgarClient().get_cached(primary_doc_url(args.cik, args.accession))
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    target = FIXTURE_DIR / f"{args.accession}.xml"
    target.write_bytes(body)

    text = README.read_text(encoding="utf-8") if README.exists() else README_HEADER
    lines = [l for l in text.splitlines() if not l.startswith(f"- `{args.accession}.xml`")]
    lines.append(f"- `{args.accession}.xml` (CIK {int(args.cik)}): {args.note}")
    README.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"saved {target} ({len(body)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
