# FundTracker progress

A dated working log, not documentation of the current code. Every entry records what was true and what was decided on its date and is not edited afterwards, except the decisions log, where a wrong decision is changed in place with a dated note saying why.

PLAN.md is the plan and the single source of truth. docs/SESSIONS.md holds the session prompts. This file records what actually happened.

## Decisions log

Settled decisions. A later session should not re-litigate these.

| Date | Decision | Detail |
| --- | --- | --- |
| 2026-10-29 | Stack is Python, requests, SQLite, Flask and HTMX, venv with pinned requirements | Matches ../edgardly so both projects are run the same way. FastAPI, httpx and uv were considered first and dropped for consistency, not on merit. |
| 2026-10-29 | Ingest every Form D of every industry; hedge-fund filtering is a query | Manager linking needs a filer's PE, VC and other filings too, and the whole corpus is small. Classification rules can change without re-ingesting. |
| 2026-10-29 | Backfill from the earliest quarterly data set available | "Never seen before" only means something against full history. Cost is a few MB per quarter. |
| 2026-10-29 | The nightly daily form index is the source of truth for new filings | The Latest Filings Atom feed is noisy (mixes DEF 14A forms, repeats filings per CIK) and shallow. It is optional for intraday refresh only. |
| 2026-10-29 | Quarterly data sets and per-filing XML load into the same tables through the same dataclasses | Both carry the same fields with different encodings. The rest of the app never knows the source. |
| 2026-10-29 | The inbox unit is a launch, not a filing | Master, feeder and offshore vehicles file together and would otherwise show as three rows. |
| 2026-10-29 | Newness is rule-based with visible reasons and tunable weights, no ML | About 8 hedge fund new notices a day. The user must be able to trust and tune the ranking. Weights live in one dict. |
| 2026-10-29 | Every ingestion step is idempotent and recorded in ingest_log | Running anything twice yields the same database. Missed nights heal through catchup. |
| 2026-10-29 | Tests never touch the network | Real filings are saved as fixtures; HTTP is mocked. Same rule as edgardly. |
| 2026-10-29 | User-Agent is read from .env and never committed | SEC policy requires a real name and email. |

## Session log

One entry per work session. Record what shipped, the test state after the session, and any open questions the next session needs to know about.

| Date | Session | Shipped | Tests | Open questions |
| --- | --- | --- | --- | --- |
| 2026-10-29 | Planning | PLAN.md, docs/edgar-formd-reference.md (all EDGAR facts verified live), this file, docs/SESSIONS.md with the WP0 prompt. No code. | n/a | None |
| 2026-10-29 | Session 0 (WP0) | pyproject.toml (src layout), requirements.txt and requirements-dev.txt pinned to the edgardly versions (requests 2.32.3, pytest 8.2.2, pytest-timeout 2.3.1), .gitignore, venv on Python 3.13.2 (the only interpreter installed; requires-python is >=3.12). config.py with user_agent() validation, DATA_DIR, RAW_DIR, DB_PATH, RATE_LIMIT_PER_SEC and .env.example. edgar/client.py: EdgarClient with a token bucket (capacity 1, so requests are at least 1/rate apart), retries on 403, 429, 5xx and connection errors with 1, 2, 4, 8 second backoff and Retry-After, 5 tries then EdgarHTTPError, 30 second timeout, get() returning non-retryable responses such as 404 to the caller, and get_cached() storing 200 bodies under RAW_DIR as <sha256 prefix>_<last two path segments>. Module entry point prints the body to stdout and the cache status to stderr. | 18 passed, no network (a conftest fixture fails any unpatched request) | 4 and 5 below. Live smoke check not yet run, so WP0 exit criteria are not fully met. |
| 2026-10-29 | Session 1 (WP1) | scripts/fetch_fixture.py and twelve real primary_doc.xml fixtures with a README recording what each covers and its filing date (the XML has none). model.py frozen dataclasses (Filing, Issuer, Offering, RelatedPerson, Recipient, Signature, FormDFiling) with *_text, *_num and *_is_indefinite amount shadows, tuples for lists, child seq from 1. normalize.py pure helpers per PLAN.md 7.2. edgar/formd_xml.py parse_formd(xml_bytes, accession, cik_hint=None, filing_date=None), every element optional, namespaces stripped. db/schema.sql (raw tables, ingest_log, indexes) and db/repo.py (connect with WAL and foreign keys, upsert_filing in one transaction with ON CONFLICT DO UPDATE and child rows rewritten per accession, loaded_at kept from first load, get_filing, filings_for_cik covering co-issuers). Quirks found in the fixtures and handled: previous-name lists using previousName instead of value, entity first names as lowercase n/a, recipient names as none. | 143 passed, no network. Loading all fixtures twice leaves every table identical and each filing round-trips exactly. | Question 1 closed; 6 to 10 below. |

## Open questions

Numbered, dated, closed in place with the answer and the date.

1. (2026-10-29) Which schema versions other than X0708 appear in Form D XML filed since 2009, and which elements differ? Only matters for the XML path, which handles recent filings; the data sets flatten older ones. Resolve in WP1 by sampling. Closed 2026-10-29: sampled the first D filings on one mid-June day per year from 2009 to 2025, plus 8 each on 2010-06-15 and 2015-06-16 and all 225 X0708 filings on 2026-10-27. Versions seen: X0401 (2009), X0704 (2010, 2011), X0705 (2012), X0706 (2013), X0707 (2015 to 2017), X0708 (2018 onward). No older version uses an element name or nesting that X0708 lacks; the differences between files are only which optional elements a filer filled in. All of them parse with the X0708 parser, and the X0704 and X0707 fixture tests list every field that comes back None, the same set an X0708 filing gives when it leaves the same elements out. One difference in meaning, not structure: filings before the 2013 rule change use exemption code `06` (Rule 506), where later ones use `06b` or `06c`. WP4 scoring should treat `06` as 506(b). 2014 was not sampled.
2. (2026-10-29) Earliest quarter on the Form D data sets page. Resolve in WP2 by scraping the page.
3. (2026-10-29) Maximum depth of the Latest Filings Atom feed (`start` parameter). Only matters if WP7 intraday refresh is built.
4. (2026-10-29) The WP0 live smoke check is pending: there is no .env yet and the user chose to create it later. Once FUNDTRACKER_USER_AGENT is set, run `python -m fundtracker.edgar.client https://www.sec.gov/Archives/edgar/data/2158817/000215881726000001/index.json` twice; the first run should report "fetched from EDGAR and cached" and the second "served from cache". Record the result here before starting WP1. Closed 2026-10-29: with .env in place, the first run printed the folder JSON and reported "fetched from EDGAR and cached" to data/raw/b30d6f6c8fc32112_000215881726000001_index.json; the second run printed identical JSON and reported "served from cache". Both exited 0. WP0 exit criteria are met.
5. (2026-10-29) PLAN.md section 5 repository layout still describes edgar/client.py as a "rate-limited httpx client". The decisions log settles on requests, which is what was built. The planning session should correct the wording. Closed 2026-10-29: PLAN.md section 5 now reads "rate-limited requests client" and the architecture diagram reads "Flask + HTMX". No other httpx, FastAPI or uv wording remains outside the decisions note that records the change.
6. (2026-10-29) A third encoding of entity related persons: AQR (fixture 0001635511-15-000001) files firstName="Management, LLC", lastName="AQR Capital", so name_norm is "MANAGEMENT LLC AQR CAPITAL". is_entity still says True, but entity_stem gives "MANAGEMENT AQR CAPITAL" and would not match "AQR CAPITAL MANAGEMENT". Kept as filed in WP1. WP4 should decide whether linking compares stems as sorted token sets or tries both orders. How common this is was not measured.
7. (2026-10-29) For WP3: the daily form index lists a co-issued filing once per issuer CIK (GEF, 0002161535-26-000001, appears under 2161535 and 2161347 on 2026-10-27), so daily ingest must de-duplicate on accession before fetching. primary_doc.xml carries no filing date; pass the index date to parse_formd as filing_date.
8. (2026-10-29) For WP2: model.py numbers child rows from 1 per accession in document order, while the data set SEQ_KEY values start at 101. The TSV loader should renumber from 1 in SEQ_KEY order, so the same filing loaded from either source writes the same rows.
9. (2026-10-29) No fixture has yearOfInc/yetToBeFormed; none of the 225 filings on 2026-10-27 used it. The parser handles it the same way as the other two choices, but it is untested against a real filing. Add one if a later session comes across it.
10. (2026-10-29) docs/edgar-formd-reference.md section 2 should gain the quirks found in WP1: previousName children in previous-name lists, lowercase "n/a" and "none" placeholders, the reversed AQR entity encoding (question 6), the schema version timeline (question 1), and the pre-2013 `06` exemption code. Left for the planning session, which owns that document.
