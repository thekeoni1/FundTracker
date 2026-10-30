"""edgar/adv.py on the adviser data page saved on 2026-10-30 and the trimmed October 2026 files."""

from pathlib import Path

import pytest

from fundtracker.edgar import adv
from fundtracker.edgar.adv import ERA, RIA

FIXTURES = Path(__file__).parent / "fixtures" / "adv"
FOLDER = ("https://www.sec.gov/files/investment/data/other/"
          "information-about-registered-investment-advisers-exempt-reporting-advisers/")


@pytest.fixture(scope="module")
def files():
    return adv.list_adv_files((FIXTURES / "adviser-data-page.html").read_text(encoding="utf-8"))


def test_real_page_lists_both_october_2026_files(files):
    by_key = {(f.month, f.kind): f.url for f in files}
    assert by_key[("2026-10", RIA)] == FOLDER + "ia100226.zip"
    assert by_key[("2026-10", ERA)] == FOLDER + "ia100226-exempt.zip"
    assert files[-1].month == "2026-10"
    assert files == sorted(files)


@pytest.mark.parametrize("month, kind, name", [
    ("2026-09", RIA, "ia09012026-registered.zip"),
    ("2026-09", ERA, "ia09012026-exempt.zip"),
    ("2026-08", RIA, "ia08032026_1.zip"),
    ("2026-08", ERA, "ia08032026-exempt_1.zip"),
    ("2026-06", RIA, "ia060126_0.zip"),
    ("2026-02", ERA, "ia020226-exemptzip.zip"),
    ("2018-01", ERA, "010118-exempt.zip"),
])
def test_oddly_named_files_are_found(files, month, kind, name):
    url = {(f.month, f.kind): f.url for f in files}[(month, kind)]
    assert url.endswith("/" + name)


def test_no_data_pdfs_are_not_files(files):
    assert not any(f.month == "2025-10" for f in files)


def test_the_latest_file_of_a_month_wins_and_links_resolve():
    html = """
      <a href="/files/data/x-information-about-registered-investment-advisers-y/ia060117.zip">a</a>
      <a href="/files/data/x-information-about-registered-investment-advisers-y/ia063017.zip">b</a>
      <a href="https://www.sec.gov/files/investment/data/information-about-registered-investment-advisers-and-exempt-reporting-advisers/ia06012026-exempt_0.zip">c</a>
      <a href="/files/elsewhere/ia070126.zip">not an adviser folder</a>
      <a href="/files/data/information-about-registered-investment-advisers/ia133126.zip">no such month</a>
    """
    files = adv.list_adv_files(html)
    assert [(f.month, f.kind) for f in files] == [("2017-06", RIA), ("2026-06", ERA)]
    assert files[0].url.endswith("/ia063017.zip") and files[0].url.startswith("https://www.sec.gov/")


def test_fetch_adv_files_reads_the_page():
    class Response:
        text = '<a href="/files/information-about-registered-investment-advisers/ia100226.zip">x</a>'

        def raise_for_status(self):
            pass

    class Client:
        def get(self, url):
            assert url == adv.ADV_PAGE
            return Response()

    assert adv.fetch_adv_files(Client()) == [("2026-10", RIA, "https://www.sec.gov/files/"
                                              "information-about-registered-investment-advisers/ia100226.zip")]


@pytest.fixture(scope="module")
def registered():
    return {r.crd: r for r in adv.read_adv_csv((FIXTURES / "ia100226_trimmed.zip").read_bytes(), RIA)}


@pytest.fixture(scope="module")
def exempt():
    return {r.crd: r for r in adv.read_adv_csv((FIXTURES / "ia100226-exempt_trimmed.zip").read_bytes(), ERA)}


def test_malt_partners(registered):
    malt = registered["337855"]
    assert malt.name == "MALT PARTNERS MANAGEMENT LLC" and malt.kind == RIA
    assert (malt.employees, malt.advisory_employees, malt.raum, malt.accounts) == (2, 2, 36_321_031, 2)
    assert (malt.any_hedge_funds, malt.n_hedge_funds, malt.n_private_funds) == (True, 2, 2)
    assert malt.private_fund_assets == 36_321_030
    assert malt.website is None and malt.cik is None
    assert malt.phone == "646-450-5521" and malt.latest_filing_date == "2026-03-06"
    assert (malt.street1, malt.city, malt.state, malt.postal_code) == (
        "3835 CLEGHORN AVENUE", "NASHVILLE", "TN", "37215")


def test_boxer_capital(registered):
    boxer = registered["330106"]
    assert boxer.legal_name == "BOXER CAPITAL MANAGEMENT, LLC"
    assert boxer.website == "https://www.boxercap.com" and boxer.cik == "2018299"
    assert (boxer.employees, boxer.raum) == (19, 578_221_564)


def test_both_files_read_sixty_rows_and_exempt_rows_have_no_item_5(registered, exempt):
    assert len(registered) == len(exempt) == 60
    assert all(r.firm_type == "ERA" and r.kind == ERA for r in exempt.values())
    assert all(r.employees is None and r.raum is None for r in exempt.values())
    assert any(r.n_private_funds for r in exempt.values())
    assert all(r.advises_private_funds is True for r in exempt.values())


@pytest.mark.parametrize("text, value", [
    ("            36,321,031.00", 36_321_031),
    ("                   2", 2),
    ("1,234.50", 1_234),
    ("", None),
    ("   ", None),
    ("n/a", None),
])
def test_parse_money(text, value):
    assert adv.parse_money(text) == value


def test_reads_a_bare_csv_and_rejects_another_file():
    text = '"Organization CRD#","Primary Business Name","Any Hedge Funds"\r\n"1","A","Y"\r\n"","B","N"\r\n'
    rows = list(adv.read_adv_csv(text.encode("cp1252"), ERA))
    assert [(r.crd, r.name, r.any_hedge_funds, r.employees) for r in rows] == [("1", "A", True, None)]
    with pytest.raises(ValueError):
        list(adv.read_adv_csv(b'"Name"\r\n"x"\r\n', RIA))
    with pytest.raises(ValueError):
        list(adv.read_adv_csv(text.encode(), "other"))
