"""Write trimmed copies of the monthly Form ADV adviser zips as test fixtures.

Usage:
    python scripts/make_adv_fixture.py <registered zip> <exempt zip>

Keeps 60 rows of each file: in the registered file MALT PARTNERS MANAGEMENT
LLC (CRD 337855) and BOXER CAPITAL MANAGEMENT, LLC (CRD 330106) plus 58
chosen at random, in the exempt file 60 at random (fixed seed, so a rerun
gives the same files). Every column is kept, with the header row and the
CSV name inside the zip as in the source. Writes
tests/fixtures/adv/<zip stem>_trimmed.zip. Run by hand; tests never call this.
"""

import argparse
import csv
import io
import random
import sys
import zipfile
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "adv"
SEED = 20261030
ROWS = 60
REGISTERED_KEEP = ("337855", "330106")
ENCODING = "cp1252"


def trim(source, keep_crds):
    with zipfile.ZipFile(source) as src:
        (name,) = src.namelist()
        text = src.read(name).decode(ENCODING)
    rows = list(csv.reader(io.StringIO(text, newline="")))
    header, body = rows[0], rows[1:]
    crd = header.index("Organization CRD#")
    kept = [r for r in body if r[crd] in keep_crds]
    missing = set(keep_crds) - {r[crd] for r in kept}
    if missing:
        sys.exit(f"not in {source.name}: {sorted(missing)}")
    others = [r for r in body if r[crd] not in keep_crds]
    kept += random.Random(SEED).sample(others, ROWS - len(kept))
    kept.sort(key=lambda r: int(r[crd]))

    out = io.StringIO(newline="")
    csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerows([header] + kept)
    target = FIXTURE_DIR / f"{source.stem}_trimmed.zip"
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, out.getvalue().encode(ENCODING))
    print(f"wrote {target} ({target.stat().st_size} bytes, {len(kept)} of {len(body)} rows)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("registered", type=Path)
    parser.add_argument("exempt", type=Path)
    args = parser.parse_args(argv)
    trim(args.registered, REGISTERED_KEEP)
    trim(args.exempt, ())
    return 0


if __name__ == "__main__":
    sys.exit(main())
