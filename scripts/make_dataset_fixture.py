"""Write a trimmed copy of a quarterly Form D data set zip as a test fixture.

Usage:
    python scripts/make_dataset_fixture.py <quarter zip> <count> <accession> [<accession> ...]

Keeps the named accessions plus <count> other submissions chosen at random
(fixed seed, so a rerun gives the same file), with every child row of each,
and writes tests/fixtures/datasets/<yyyy>q<n>_trimmed.zip. Lines are copied
byte for byte with the same folder, file names and header rows. FormD_readme.html
is left out to keep the fixture small. Run by hand; tests never call this.
"""

import argparse
import random
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "datasets"
SEED = 20261029


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("zip", type=Path)
    parser.add_argument("count", type=int, help="random submissions to keep besides the named ones")
    parser.add_argument("accessions", nargs="+")
    args = parser.parse_args(argv)

    m = re.match(r"(\d{4}q[1-4])", args.zip.name, re.I)
    if not m:
        parser.error("zip name must start with the quarter, e.g. 2015q2_d.zip")
    target = FIXTURE_DIR / f"{m.group(1).lower()}_trimmed.zip"

    with zipfile.ZipFile(args.zip) as src:
        members = [n for n in src.namelist() if not n.lower().endswith("readme.html")]
        submission = next(n for n in members if PurePosixPath(n).name.upper() == "FORMDSUBMISSION.TSV")
        lines = src.read(submission).splitlines(keepends=True)
        all_accessions = [line.split(b"\t", 1)[0].decode() for line in lines[1:]]
        missing = set(args.accessions) - set(all_accessions)
        if missing:
            sys.exit(f"not in {args.zip.name}: {sorted(missing)}")
        others = sorted(set(all_accessions) - set(args.accessions))
        keep = set(args.accessions) | set(random.Random(SEED).sample(others, args.count))

        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
            for name in members:
                data = src.read(name)
                if name.lower().endswith(".tsv"):
                    rows = data.splitlines(keepends=True)
                    kept = [rows[0]] + [r for r in rows[1:] if r.split(b"\t", 1)[0].decode() in keep]
                    data = b"".join(kept)
                    print(f"{PurePosixPath(name).name}: {len(kept) - 1} of {len(rows) - 1} rows")
                out.writestr(name, data)
    print(f"wrote {target} ({target.stat().st_size} bytes, {len(keep)} submissions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
