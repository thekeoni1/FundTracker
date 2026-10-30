"""The site's export routes: the workbook holds exactly the rows the filtered page shows."""

import io
import re
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

from fundtracker.db import repo
from fundtracker.web.app import XLSX_TYPE, create_app

sys.path.insert(0, str(Path(__file__).parent))
from test_emerging import TODAY as EMERGING_TODAY, world  # noqa: E402,F401
from test_web_launches import TODAY, db_path, launch_ids  # noqa: E402,F401


def column(ws, header):
    headers = [c.value for c in ws[1]]
    i = headers.index(header)
    return [row[i] for row in ws.iter_rows(min_row=2, values_only=True)]


def download(client, url):
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["Content-Type"] == XLSX_TYPE
    filename = re.search(r'filename=([^;]+)', response.headers["Content-Disposition"]).group(1).strip('"')
    return filename, load_workbook(io.BytesIO(response.data))


@pytest.mark.parametrize("query", ["", "type=all", "type=all&state=FL&min_score=70", "sort=raised",
                                   "since=2026-10-01&rule=b"])
def test_launches_export_matches_the_page(db_path, query):
    client = create_app(connect=lambda: repo.connect_readonly(db_path), today=lambda: TODAY).test_client()
    page = client.get(f"/launches?{query}").get_data(as_text=True)
    assert f'href="/launches/export.xlsx{"?" + query.replace("&", "&amp;") if query else ""}"' in page
    filename, wb = download(client, f"/launches/export.xlsx?{query}")
    assert wb.sheetnames == ["Launches", "Funds", "Reasons", "About"]
    assert column(wb["Launches"], "Launch ID") == launch_ids(page)
    kind = "all" if "type=all" in query else "hedge"
    since = "2026-10-01" if "since=2026-10-01" in query else "2026-10-23"
    assert filename == f"launches-{kind}-{since}-to-2026-10-30-20261030.xlsx"
    about = {r[0]: r[1] for r in wb["About"].iter_rows(values_only=True) if r[0]}
    assert about["Site filters"].startswith(f"filed {since} to 2026-10-30; ")


@pytest.mark.parametrize("query", ["", "type=all", "prior_firm=without&type=all", "sort=raised&type=all"])
def test_emerging_export_matches_the_page(world, query):
    client = create_app(connect=lambda: repo.connect_readonly(world),
                        today=lambda: EMERGING_TODAY).test_client()
    page = client.get(f"/emerging?{query}").get_data(as_text=True)
    filename, wb = download(client, f"/emerging/export.xlsx?{query}")
    assert wb.sheetnames == ["Managers", "Funds", "People", "Reasons", "About"]
    assert column(wb["Managers"], "Manager ID") == re.findall(r'href="/manager/([^"]+)"', page)
    assert filename.startswith("emerging-") and filename.endswith(f"-{EMERGING_TODAY:%Y%m%d}.xlsx")
    about = {r[0]: r[1] for r in wb["About"].iter_rows(values_only=True) if r[0]}
    assert about["Site filters"].startswith("first seen ")
    assert about["Managers"] == len(column(wb["Managers"], "Manager ID"))
