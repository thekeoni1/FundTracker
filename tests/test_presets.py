"""Filter presets: links above the filters on both pages, the active one marked."""

import html as html_lib
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

import pytest
from werkzeug.datastructures import MultiDict

from fundtracker.db import repo
from fundtracker.web import filters, presets
from fundtracker.web.app import create_app

sys.path.insert(0, str(Path(__file__).parent))
from test_emerging import TODAY as EMERGING_TODAY, world  # noqa: E402,F401
from test_web_launches import TODAY as LAUNCH_TODAY, db_path  # noqa: E402,F401


@pytest.fixture
def launches(db_path):
    return create_app(connect=lambda: repo.connect_readonly(db_path), today=lambda: LAUNCH_TODAY).test_client()


@pytest.fixture
def emerging(world):
    return create_app(connect=lambda: repo.connect_readonly(world), today=lambda: EMERGING_TODAY).test_client()


PAGES = {
    "launches": (presets.LAUNCH_PRESETS, filters.launch_filters, filters.describe_launches, LAUNCH_TODAY),
    "emerging": (presets.EMERGING_PRESETS, filters.emerging_filters, filters.describe_emerging, EMERGING_TODAY),
}


def active(page_html):
    """The labels of the presets marked active."""
    nav = re.search(r'<nav id="presets".*?</nav>', page_html, re.S).group(0)
    return re.findall(r'class="active" aria-current="true">([^<]+)</a>', nav)


def summary(page_html):
    return html_lib.unescape(re.search(r"</strong>[^:]*: (.*?)\.</p>", page_html, re.S).group(1))


def preset_cases():
    for page, (table, *_rest) in PAGES.items():
        for label, query in table:
            yield page, label, query


@pytest.mark.parametrize("page, label, query", list(preset_cases()))
def test_each_preset_renders_with_its_filters_and_marks_itself(request, page, label, query):
    client = request.getfixturevalue(page)
    _table, parse, describe, today = PAGES[page]
    html = client.get(f"/{page}?" + urlencode(query)).get_data(as_text=True)
    assert label in active(html)
    expected = describe(parse(MultiDict(query), today))
    assert summary(html) == "; ".join(expected)
    assert f'href="/{page}?{html_lib.escape(urlencode(query))}"' in html


def test_the_links_carry_only_the_presets_keys():
    assert [p["query"] for p in presets.marked(presets.EMERGING_PRESETS, filters.emerging_filters(
        MultiDict(), EMERGING_TODAY), filters.emerging_filters, EMERGING_TODAY)] == [
        "adviser=linked&employees=1-5", "offering=open&outreach=untouched", "prior_firm=with"]


def test_defaults_mark_the_default_preset_only(launches, emerging):
    assert active(launches.get("/launches").get_data(as_text=True)) == ["Hedge launches this week"]
    assert active(emerging.get("/emerging").get_data(as_text=True)) == []


def test_a_preset_is_not_marked_on_another_presets_page(launches, emerging):
    raised = active(launches.get("/launches?raised=%3E0").get_data(as_text=True))
    assert raised == ["Raised already"]  # the defaults preset matches too, but sets nothing
    assert active(launches.get("/launches?type=all&min_inv=%3C100k").get_data(as_text=True)) == ["Small minimums"]
    assert active(emerging.get("/emerging?prior_firm=with").get_data(as_text=True)) == ["Spin-outs"]
    # Half a preset is not the preset.
    assert active(emerging.get("/emerging?offering=open").get_data(as_text=True)) == []


def test_the_table_partial_redraws_the_presets_out_of_band(emerging):
    response = emerging.get("/emerging/table?prior_firm=with", headers={"HX-Request": "true"})
    text = response.get_data(as_text=True)
    assert 'id="presets" class="presets" aria-label="Presets" hx-swap-oob="true"' in text
    assert active(text) == ["Spin-outs"]
    assert "hx-swap-oob" not in emerging.get("/emerging/table?prior_firm=with").get_data(as_text=True)


def test_one_preset_marked_the_one_setting_most_filters():
    current = filters.launch_filters(MultiDict({"raised": ">0", "min_inv": "<100k"}), LAUNCH_TODAY)
    marks = presets.marked(presets.LAUNCH_PRESETS, current, filters.launch_filters, LAUNCH_TODAY)
    # Raised already and Small minimums both match and set one filter each: the first wins.
    assert [p["label"] for p in marks if p["active"]] == ["Raised already"]
