"""Outbound links for the site. Built from text, never fetched.

Form D has no website field. The SEC's Investment Adviser Public Disclosure
(IAPD) search leads to a firm's Form ADV, which does. The IAPD firm search
URL below was checked on 2026-10-30: it answers 200, and the search API the
page calls returns the matching firms
(https://adviserinfo.sec.gov/search/genericsearch/firmgrid?query=bridgewater).
"""

from urllib.parse import quote_plus

from fundtracker.edgar.index import company_filings_url, filing_folder_url


def edgar_company(cik):
    """The company's Form D filings on EDGAR."""
    return company_filings_url(cik)


def edgar_filing(cik, accession):
    """The filing's folder on EDGAR."""
    return filing_folder_url(cik, accession)


def web_search(text):
    """A web search on text, quoted so the firm name is searched as a phrase."""
    return f"https://www.google.com/search?q={quote_plus(_phrase(text))}"


def linkedin_people(name, firm=None):
    """A LinkedIn people search on a person's name, with the firm name when given."""
    keywords = " ".join(t for t in (name, firm) if t)
    return f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(keywords)}"


def iapd_search(firm):
    """An SEC IAPD firm search on the firm name."""
    return f"https://adviserinfo.sec.gov/search/genericsearch/firmgrid?query={quote_plus(firm or '')}"


def _phrase(text):
    text = " ".join((text or "").split())
    return f'"{text}"' if text else ""
