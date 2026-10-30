from pathlib import Path

import pytest
import requests

from fundtracker.edgar import datasets
from fundtracker.edgar.datasets import Quarter

FIXTURES = Path(__file__).parent / "fixtures" / "datasets"


class FakeResponse:
    def __init__(self, status=200, body=b"", text=""):
        self.status_code = status
        self.body = body
        self.text = text
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        for i in range(0, len(self.body), chunk_size):
            yield self.body[i:i + chunk_size]

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, stream=False):
        self.calls.append((url, stream))
        return self.responses[url]


def test_real_page_lists_every_quarter_from_2008q1():
    """The page saved on 2026-10-29: 75 contiguous quarters, two prefixes, _d and _d_0 names."""
    html = (FIXTURES / "form-d-data-sets.html").read_text(encoding="utf-8")
    quarters = datasets.parse_quarter_links(html)
    assert len(quarters) == 75
    assert quarters[0].key == "2008q1" and quarters[-1].key == "2026q3"
    keys = [q.key for q in quarters]
    expected = [f"{y}q{q}" for y in range(2008, 2027) for q in range(1, 5)][:75]
    assert keys == expected
    by_key = {q.key: q.url for q in quarters}
    assert by_key["2026q3"] == ("https://www.sec.gov/files/datastandardsinnovation/data/"
                                "form-d-data-sets/2026q3_d.zip")
    assert by_key["2026q1"] == ("https://www.sec.gov/files/structureddata/data/"
                                "form-d-data-sets/2026q1_d.zip")
    assert by_key["2010q2"].endswith("/form-d-data-sets/2010q2_d_0.zip")


def test_plain_name_wins_over_numbered_and_order_is_ascending():
    html = """
      <a href="/files/a/form-d-data-sets/2020q2_d_1.zip">x</a>
      <a href='https://www.sec.gov/files/b/form-d-data-sets/2020q2_d.zip'>x</a>
      <a href="/files/a/form-d-data-sets/2019Q4_d_0.zip">x</a>
      <a href="/files/a/form-d-data-sets/2020q2_d_0.zip">x</a>
      <a href="/files/a/other/2020q3_d.zip">not a data set</a>
    """
    quarters = datasets.parse_quarter_links(html)
    assert [q.key for q in quarters] == ["2019q4", "2020q2"]
    assert quarters[1].url == "https://www.sec.gov/files/b/form-d-data-sets/2020q2_d.zip"
    assert quarters[0].url == "https://www.sec.gov/files/a/form-d-data-sets/2019Q4_d_0.zip"


def test_list_quarters_fetches_the_page():
    html = '<a href="/files/x/form-d-data-sets/2026q3_d.zip">2026 Q3</a>'
    client = FakeClient({datasets.DATASETS_PAGE: FakeResponse(text=html)})
    assert [q.key for q in datasets.list_quarters(client)] == ["2026q3"]


@pytest.mark.parametrize("text, key", [("2026q3", "2026q3"), ("2009Q1", "2009q1")])
def test_quarter_parse(text, key):
    assert Quarter.parse(text).key == key


@pytest.mark.parametrize("text", ["2026q5", "26q1", "2026-3"])
def test_quarter_parse_rejects(text):
    with pytest.raises(ValueError):
        Quarter.parse(text)


def test_download_streams_to_named_file_and_skips_existing(tmp_path):
    quarter = Quarter(2010, 2, "https://www.sec.gov/files/x/form-d-data-sets/2010q2_d_0.zip")
    body = b"PK" + bytes(range(256)) * 9000
    response = FakeResponse(body=body)
    client = FakeClient({quarter.url: response})
    path = datasets.download_quarter(client, quarter, tmp_path)
    assert path == tmp_path / "2010q2_d.zip"
    assert path.read_bytes() == body
    assert client.calls == [(quarter.url, True)]
    assert response.closed
    assert not list(tmp_path.glob("*.part"))
    datasets.download_quarter(client, quarter, tmp_path)
    assert len(client.calls) == 1


def test_download_refetches_empty_file_and_leaves_nothing_on_error(tmp_path):
    quarter = Quarter(2010, 2, "https://example.test/2010q2_d.zip")
    (tmp_path / "2010q2_d.zip").write_bytes(b"")
    client = FakeClient({quarter.url: FakeResponse(status=404)})
    with pytest.raises(requests.HTTPError):
        datasets.download_quarter(client, quarter, tmp_path)
    assert (tmp_path / "2010q2_d.zip").stat().st_size == 0
    assert not list(tmp_path.glob("*.part"))
