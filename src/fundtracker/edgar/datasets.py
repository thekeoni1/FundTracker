"""The SEC's quarterly Form D data sets: discover, download, read.

Reference: docs/edgar-formd-reference.md section 5. The data sets page links
one zip per quarter. Links use more than one URL prefix and, for 2008 Q2 to
2013 Q4, a "_d_0.zip" file name instead of "_d.zip", so quarters are found
by pattern rather than by building URLs.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

DATASETS_PAGE = "https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets"

_ZIP_LINK = re.compile(
    r"""href=["']([^"']*form-d-data-sets/(\d{4})q([1-4])_d(?:_(\d+))?\.zip)["']""", re.I
)


@dataclass(frozen=True, order=True)
class Quarter:
    year: int
    q: int
    url: str = ""

    @property
    def key(self):
        """The ingest_log key and file stem, e.g. 2026q3."""
        return f"{self.year}q{self.q}"

    @classmethod
    def parse(cls, text):
        """Quarter from "2026q3" (case-insensitive); url is left empty."""
        m = re.fullmatch(r"(\d{4})[qQ]([1-4])", text.strip())
        if not m:
            raise ValueError(f"not a quarter: {text!r} (expected e.g. 2026q3)")
        return cls(int(m.group(1)), int(m.group(2)))


def parse_quarter_links(html, base_url=DATASETS_PAGE):
    """Quarters linked from the data sets page, ascending, one per quarter.

    When a quarter is linked more than once, the plain "_d.zip" name wins
    over a numbered "_d_N.zip" one.
    """
    best = {}
    for m in _ZIP_LINK.finditer(html):
        href, year, q, suffix = m.groups()
        rank = -1 if suffix is None else int(suffix)
        key = (int(year), int(q))
        if key not in best or rank < best[key][0]:
            best[key] = (rank, urljoin(base_url, href))
    return [Quarter(y, q, url) for (y, q), (_rank, url) in sorted(best.items())]


def list_quarters(client):
    """Fetch the data sets page and return every quarter it links, ascending."""
    response = client.get(DATASETS_PAGE)
    response.raise_for_status()
    return parse_quarter_links(response.text)


def zip_path(quarter, dest_dir):
    return Path(dest_dir) / f"{quarter.key}_d.zip"


def download_quarter(client, quarter, dest_dir):
    """Stream the quarter's zip to dest_dir/{yyyy}q{n}_d.zip and return the path.

    A file already there with non-zero size is kept and not fetched again;
    the folder is the cache. Writes to a .part file first so an interrupted
    download never looks complete.
    """
    target = zip_path(quarter, dest_dir)
    if target.is_file() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    response = client.get(quarter.url, stream=True)
    try:
        response.raise_for_status()
        with open(part, "wb") as out:
            for chunk in response.iter_content(chunk_size=1 << 20):
                out.write(chunk)
    finally:
        response.close()
    part.replace(target)
    return target
