# FundTracker: design and implementation plan

Status: planning complete, no code written. Written 2026-10-29 by a planning-only session. Implementation is handed out as the work packages in section 9, one at a time, to separate implementation sessions.

Companion documents:
- `docs/edgar-formd-reference.md` holds every verified fact about the EDGAR endpoints, the Form D XML schema, and the quarterly data set layout. Implementers should read it before touching any ingestion code and should not need to re-research EDGAR.
- `PROGRESS.md` is the append-only record of decisions taken and what each session shipped. Every session reads it first and updates it last.
- `docs/SESSIONS.md` holds the self-contained prompt for each work session, written when the session is handed out.

Process rules, borrowed from the sibling project `../edgardly`, which the same user runs:
- Do not start work package N+1 until N's exit criteria are met and recorded in PROGRESS.md.
- Settled decisions live in the PROGRESS.md decisions log. A later session does not re-litigate them; if one is wrong, it changes it there with a dated note saying why.
- One commit per numbered step in a session prompt, in order.
- Plain prose in all docs and user-visible text. No em dashes.

## 1. Goal

A small, local, single-user app that watches SEC EDGAR Form D filings and surfaces **newly launched hedge funds**, with enough context to decide which ones are worth a closer look. The user opens it a few times a week, sees what is new since last time, triages, and exports.

What "new hedge fund" means here, in decreasing order of value:

1. **New manager**: a fund whose general partner, investment manager, signer, and address have never appeared in any prior Form D. These are emerging managers, the real prize.
2. **New fund from a known manager**: first Form D for a new CIK, but related persons match earlier filings.
3. **New offering notice**: a `D` (not `D/A`) filing by a fund that self-classifies as Hedge Fund, regardless of history.

The app should rank by (1) > (2) > (3) and let the user filter.

## 2. Non-goals

- No multi-user accounts, no hosting, no auth. Runs on the user's PC.
- No real-time alerts in v1. Nightly is enough; Form D is filed up to 15 days after first sale anyway.
- No scraping of fund websites or news. EDGAR only, with Form ADV as a possible later enrichment.
- No attempt to compute AUM. Form D rarely discloses it for funds.

## 3. Why Form D works for this

Every private fund that sells interests under Regulation D must file Form D within 15 days of first sale, and many file before first sale. The filing is structured XML with the fund's self-declared type (Hedge Fund / Private Equity / Venture Capital / Other Investment Fund), its Investment Company Act exclusion (3(c)(1) or 3(c)(7)), whether it is a new notice or amendment, whether first sale has occurred, minimum investment, offering size, amount sold, investor count, year of formation, and the names and addresses of executive officers, directors, promoters, and the signer. That is enough to detect launches and to link funds to managers.

Measured on 2026 Q3: about 8 new-notice Hedge Fund filings per business day, 505 for the quarter, 174 of them filed before first sale. Volumes are small. The app's value is in the newness scoring and manager linking, not in moving data.

## 4. Data sources and how each is used

| Source | Role | Cadence | Cost |
|---|---|---|---|
| Quarterly Form D data sets (TSV zips) | Backfill all history so "has this person filed before" can be answered locally | Once, then each quarter as a reconciliation | About 70 zips, a few MB each |
| Daily form index `form.YYYYMMDD.idx` | Discover the day's `D` and `D/A` filings | Nightly, with catch-up for missed days | 1 request per day |
| `primary_doc.xml` per filing | Full structured record for each new filing | Per filing discovered by the daily index | About 500 requests per day at 5/s, under 2 minutes |
| Latest Filings Atom feed | Optional intraday "refresh now" | On demand | A handful of requests |
| Submissions API | Fallback check of a CIK's full filing history | Per candidate, only when local history is thin | 1 request per fund |
| Full-text search | Ad hoc name search box | On demand | 1 request per search |

Both the quarterly TSVs and the XML carry the same fields with different encodings. Both loaders normalise into one set of tables so the rest of the app never knows which source a row came from.

## 5. Architecture

Single Python process, SQLite database, CLI for ingestion, small local web UI for triage. No queue, no background daemon, no external services.

```
            +------------------+        +------------------+
 EDGAR ---> | ingest (CLI)     | -----> | SQLite           | <----- web UI (Flask + HTMX)
            |  backfill        |        |  raw tables      |         inbox / fund / manager
            |  daily / catchup |        |  derived tables  |         review status, CSV export
            |  derive          |        |  review state    |
            +------------------+        +------------------+
```

### Stack (recommended; see section 10 for the decision)

- Python 3.12 (3.9+ compatible is not required), `venv` plus pinned `requirements.txt` and `requirements-dev.txt`, matching `../edgardly`.
- `requests` for HTTP, wrapped in a client with a token-bucket rate limiter and an on-disk cache of every fetched XML. Edgardly's `app/edgar_api.py` has the minimal version of this (`_rate_limited_get`: fixed sleep, one 429 retry); FundTracker's client should be the same idea done properly.
- Stdlib `xml.etree` for parsing. Documents are about 6 KB.
- SQLite via stdlib `sqlite3`, schema in a `.sql` file, hand-written queries. No ORM.
- `argparse` or `typer` for the CLI (typer if the implementer prefers; keep dependencies few).
- `flask` + `jinja2` + HTMX from a CDN for the UI, matching edgardly. No JavaScript build step.
- `pytest` plus `pytest-timeout` for tests. No network in tests; fixtures on disk.

### Repository layout

```
FundTracker/
  PLAN.md                         this file
  PROGRESS.md                     decisions log, session log, open questions
  docs/edgar-formd-reference.md   verified data-source facts
  docs/SESSIONS.md                one self-contained prompt per work session
  pyproject.toml                  package metadata only; deps are in requirements*.txt
  requirements.txt, requirements-dev.txt
  src/fundtracker/
    config.py        user agent, data dir, rate limit, db path (env + .env file)
    edgar/client.py  rate-limited requests client, retries, disk cache
    edgar/index.py   daily form index + Atom feed discovery
    edgar/datasets.py quarterly zip discovery + TSV readers
    edgar/formd_xml.py primary_doc.xml -> FormDFiling dataclass
    model.py         dataclasses shared by both loaders
    db/schema.sql
    db/repo.py       upserts and queries
    ingest.py        backfill, daily, catchup orchestration + ingest_log
    normalize.py     names, entity detection, amounts, dates, addresses
    classify.py      hedge-fund candidate rules
    newness.py       manager linking and newness score
    cli.py
    web/app.py, web/templates/*.html, web/static/   (Flask)
  tests/
    fixtures/        a dozen real primary_doc.xml files + a trimmed quarterly zip
  data/              gitignored: fundtracker.db, raw/{accession}.xml, datasets/*.zip
```

## 6. Data model

### Raw tables (one row per source record, both loaders write here)

- `filing` — `accession` PK, `cik`, `filing_date`, `submission_type` (D or D/A), `previous_accession`, `file_num`, `schema_version`, `test_or_live`, `source` (dataset or xml), `loaded_at`.
- `issuer` — `accession`, `seq`, `is_primary`, `cik`, `entity_name`, `street1`, `street2`, `city`, `state_or_country`, `state_or_country_desc`, `zip`, `phone`, `jurisdiction`, `entity_type`, `entity_type_other`, `year_of_inc_choice`, `year_of_inc_value`, `previous_names` (JSON array). PK `(accession, seq)`.
- `offering` — `accession` PK plus every field in the OFFERING table of the data set, with text kept verbatim and numeric shadows: `total_offering_amount_text`, `total_offering_amount_num` (NULL when Indefinite), same for sold and remaining, `min_investment_num`, `exemptions` (JSON array of codes), `is_amendment`, `sale_date`, `sale_yet_to_occur`, `fund_type`, `industry_group`, `is_40act`, `nav_range`, `revenue_range`, `has_non_accredited`, `n_investors`, security-type flags, commission and fee fields, clarifications.
- `related_person` — `accession`, `seq`, `first`, `middle`, `last`, address fields, `relationships` (JSON array), `clarification`, plus derived `name_norm` and `is_entity`. PK `(accession, seq)`.
- `recipient` — `accession`, `seq`, `name`, `crd`, `bd_name`, `bd_crd`, address fields, `states` (JSON), `foreign_solicitation`.
- `signature` — `accession`, `seq`, `issuer_name`, `signature_name`, `signer`, `title`, `date`, derived `signer_norm`.
- `ingest_log` — `source`, `key` (e.g. `2026-10-28` or `2026q3`), `status`, `n_rows`, `started_at`, `finished_at`, `error`. Makes every ingestion step idempotent and resumable.

### Derived tables (rebuilt by `fundtracker derive`, cheap to recompute)

- `fund` — one row per issuer CIK: `cik` PK, `name`, `fund_type`, `industry_group`, `first_filing_date`, `first_accession`, `latest_filing_date`, `latest_accession`, `n_filings`, `exemptions`, `state`, `country`, `is_hedge_candidate`, `candidate_reason`.
- `manager` — one row per clustered manager: `manager_id` PK, `display_name`, `key_kind` (entity-name / signer / address), `first_seen_date`, `n_funds`.
- `fund_manager` — `cik`, `manager_id`, `evidence` (which related person, signer, or address linked them).
- `launch` — one row per fund family launched together: `launch_id`, `manager_id`, `window_start`, `window_end`, `fund_ciks` (JSON), `newness_score`, `newness_reasons` (JSON). This is the unit the inbox shows.
- `review` — user state: `launch_id` (or `cik`) PK, `status` (new / reviewed / watching / contacted / ignore), `notes`, `updated_at`. Never rebuilt by derive.

### Indexes

`filing(filing_date)`, `filing(cik)`, `offering(fund_type, is_amendment)`, `related_person(name_norm)`, `signature(signer_norm)`, `issuer(cik)`, `issuer(phone)`, `issuer(zip, street1)`.

Expected size after full backfill: roughly 1 million filings, 3 million related persons, a few hundred MB in SQLite. Comfortable.

## 7. Core logic

### 7.1 Hedge-fund candidate rule (`classify.py`)

A filing is a hedge-fund candidate when `industry_group = Pooled Investment Fund` and either:
- `fund_type = Hedge Fund`, or
- `fund_type = Other Investment Fund` and exemptions include `3C.1` or `3C.7` and the name does not match an exclusion list (real estate, SPV, co-invest, feeder of a known PE/VC, credit opportunities, etc.).

Everything is ingested regardless, so the user can widen or narrow the rule in the UI without re-ingesting. Default inbox shows `Hedge Fund` only, with an "include Other Investment Fund" toggle.

### 7.2 Name normalisation (`normalize.py`)

- Join `first middle last`, upper-case, strip punctuation, collapse whitespace.
- Entity detection: any of `LLC L.L.C. LP L.P. LTD LIMITED INC CORP GP PARTNERS CAPITAL MANAGEMENT ADVISORS ADVISERS HOLDINGS` as a token, or `first` in `{"-", "", "N/A"}`.
- Entity key: remove suffix tokens (`LLC LP LTD INC GP THE`) to get a stem, e.g. `AXIARIS CAPITAL MANAGEMENT`.
- Fund-family stem: from the issuer name, strip `FUND, LP, L.P., LTD, LLC, MASTER, FEEDER, OFFSHORE, ONSHORE, CAYMAN, INTERNATIONAL, (I|II|III|IV|V), A, B, SERIES, SPV, CO-INVEST`, so `X Master Fund LP`, `X Offshore Fund Ltd`, `X Fund LP` share stem `X`.
- Address key: `street1 + zip` upper-cased, digits and letters only.
- Amounts: `Indefinite` -> NULL with `is_indefinite = true`; otherwise integer.
- Dates: accept `DD-MON-YYYY` and ISO.

### 7.3 Manager linking (`newness.py`)

For each candidate `D` filing, collect linking keys: entity-type related-person stems, signer name (when the signer is not the fund itself), issuer phone, address key. Look each key up across all prior filings (any industry, any date before this filing date minus a 30-day window). Union the matches into a manager cluster; if none match, create a new manager.

Clustering within the same window: filings within 30 days of each other sharing any key or the same fund-family stem form one `launch`. This folds master/feeder/offshore trios into one inbox row.

### 7.4 Newness score

Score 0-100 with a reasons list, computed per launch:

| Signal | Points | Why |
|---|---|---|
| No linking key seen in any prior filing | 40 | brand-new manager |
| Every fund CIK in the launch has exactly one filing | 15 | genuinely first notice |
| `sale_yet_to_occur` on any fund | 15 | pre-launch |
| `total_amount_sold = 0` and `n_investors = 0` | 10 | nothing raised yet |
| `year_of_inc` within five years and value is this or last calendar year | 10 | newly formed vehicle |
| Exemption `06c` | 5 | can market publicly, often emerging |
| Minimum investment between 100k and 1m | 5 | typical emerging hedge fund ticket |

Penalties: linking key matches a manager with 5+ funds (-30), signer title contains "Chief Compliance Officer" at a firm with prior filings (-10), issuer name matches PE/VC/real-estate patterns (-20).

The weights live in one dict so they can be tuned without touching logic. The UI shows the reasons so the user can calibrate.

### 7.5 Ingestion flows (`ingest.py`)

- `backfill`: scrape the data sets page for zip links, download each missing quarter to `data/datasets/`, load TSVs into raw tables inside one transaction per quarter, mark `ingest_log`. Re-running skips loaded quarters. Then run `derive`.
- `daily [--date]`: fetch `form.YYYYMMDD.idx`, take `D` and `D/A` rows, skip accessions already in `filing`, fetch each `primary_doc.xml` (cache to `data/raw/`), parse, upsert, log the day. Weekends and holidays yield a 404 for the index; log as `no_index` and move on.
- `catchup`: iterate business days from the last successful `daily` to yesterday, calling `daily` for each. The UI and the CLI both call this on start so missed nights heal themselves.
- `derive`: rebuild `fund`, `manager`, `fund_manager`, `launch`. Full rebuild is fine at this size (seconds to low minutes). Keep `review` untouched.
- `reconcile <quarter>`: when a new quarterly zip appears, load it and report any accessions it contains that the daily path missed, then fill them.

Idempotency rule: every writer upserts on the natural key and every step is recorded in `ingest_log`. Running anything twice yields the same database.

## 8. User interface

### CLI (phase 1 deliverable, useful on its own)

```
fundtracker init                      create db, write .env with user agent
fundtracker backfill [--from 2009q1]
fundtracker ingest daily [--date 2026-10-28]
fundtracker ingest catchup
fundtracker derive
fundtracker new [--since 7d] [--min-score 50] [--type hedge|all] [--csv out.csv]
fundtracker fund <cik>                timeline of filings, related persons, EDGAR links
fundtracker manager <id>
fundtracker search "<name>"           local search, with --edgar to hit full-text search
fundtracker serve                     start the web UI on localhost:8000
```

`new` prints a table: filing date, launch name, manager, fund type, exemptions, min investment, offering amount, state, score, top two reasons, EDGAR link. `--csv` writes the same columns plus the full field set.

### Web UI (phase 2)

- **Inbox**: launches since last visit (or a date range), grouped rows, score badge with hover reasons, filters for fund type, exemption (3C.1/3C.7, 506(b)/506(c)), state or country, min investment range, score threshold, review status. Row actions: set status, add note, open EDGAR.
- **Launch / Fund detail**: all filings for the family, side by side; related persons with "also appears in" links; signer; offering terms; raw XML link.
- **Manager detail**: every fund linked to this manager over time, with first-seen date.
- **Search**: local name search across funds, managers, people; optional EDGAR full-text search.
- **Export**: current filtered view to CSV.
- **Status bar**: last successful ingest date, filings loaded, a "refresh now" button that runs `catchup` and, optionally, pulls the Atom feed for today's intraday filings.

Plain server-rendered pages. HTMX for status changes and filters without full reloads. Dark/light via `prefers-color-scheme`. No charts in v1.

## 9. Work packages for implementation sessions

Each package is sized to one focused session. Hand them out in order; each lists what it depends on, what it must deliver, and how we know it is done. The brief at the end of each package can be pasted as the opening instruction to the implementation session, together with "read PLAN.md and docs/edgar-formd-reference.md first".

### WP0: Scaffold and EDGAR client

Depends on: nothing.

Deliver: `pyproject.toml` (metadata, `src` layout, editable install), pinned `requirements.txt` and `requirements-dev.txt`, `src/fundtracker/` skeleton, `config.py` (user agent from `FUNDTRACKER_USER_AGENT` in a `.env` file, data dir, rate limit), `edgar/client.py` with token-bucket limiting at 5 req/s, retry with backoff on 429/403/5xx, gzip, on-disk cache keyed by URL for immutable archive files, and a `tests/` folder with a client test that mocks `requests` and asserts the limiter, the retry, and the cache. `.gitignore` for `data/` and `.env`. `PROGRESS.md` session entry. `git init` with one commit per step.

Done when: `pytest` passes with no network; `python -m fundtracker.edgar.client https://www.sec.gov/Archives/edgar/data/2158817/000215881726000001/index.json` prints the JSON and a second call is served from cache.

Brief: "Set up the FundTracker Python project per PLAN.md section 5 and build the rate-limited, cached EDGAR HTTP client from WP0. Do not build parsers or the database yet."

### WP1: Form D XML parser and database schema

Depends on: WP0.

Deliver: `model.py` dataclasses mirroring section 6 raw tables; `edgar/formd_xml.py` turning `primary_doc.xml` into those dataclasses, tolerant of missing elements and older schema versions; `db/schema.sql` and `db/repo.py` with idempotent upserts; `normalize.py` for amounts, dates, name joining, entity detection. Download 12 real filings into `tests/fixtures/` covering: a Hedge Fund new notice, a D/A amendment, a 3C.7 fund, an Other Investment Fund, a VC fund, a non-fund issuer, a filing with co-issuers, one with sales compensation recipients, one with multiple signatures, one with entity related persons encoded both ways, and two older schema versions.

Done when: golden tests assert parsed values for each fixture; loading a fixture twice leaves row counts unchanged; `Indefinite` and `None` conventions are handled.

Brief: "Implement WP1 from PLAN.md: the Form D XML parser, SQLite schema, repository layer and normalisation helpers, with fixture-based golden tests. Use the element hierarchy in docs/edgar-formd-reference.md section 2."

### WP2: Quarterly data set backfill

Depends on: WP1.

Deliver: `edgar/datasets.py` that scrapes the data sets page for zip links, downloads missing quarters, streams the six TSVs and maps their columns and value conventions (reference section 5) into the same dataclasses as the XML parser; `ingest.backfill` with per-quarter transactions and `ingest_log`; a trimmed fixture zip (a few hundred rows) for tests.

Done when: `fundtracker backfill` loads every available quarter in under 15 minutes on the user's machine; row counts per quarter match the TSV line counts; re-running is a no-op; the 2026 Q3 load reports 15,952 filings and 2,394 Hedge Fund offering rows.

Brief: "Implement WP2 from PLAN.md: discover, download and load the quarterly Form D data sets into the existing schema, idempotently, with a trimmed-zip test fixture."

### WP3: Daily incremental ingest and catch-up

Depends on: WP1. Can run in parallel with WP2.

Deliver: `edgar/index.py` parsing `form.YYYYMMDD.idx` by header column offsets; `ingest.daily` and `ingest.catchup` per section 7.5, including 404 handling for non-business days; CLI commands `ingest daily` and `ingest catchup`; a fixture index file in tests.

Done when: running `ingest daily --date <yesterday>` loads every `D` and `D/A` filing for that day and a second run fetches nothing; `catchup` after deleting three days of `ingest_log` refills exactly those days; the Axiaris example filing (reference section 2) round-trips with the expected values.

Brief: "Implement WP3 from PLAN.md: daily form-index discovery, per-filing XML fetch, catch-up over missed days, all idempotent and logged."

### WP4: Derived layer, scoring and `new` command

Depends on: WP2 and WP3 (needs real history to test against).

Deliver: `classify.py`, `newness.py`, `ingest.derive` building `fund`, `manager`, `fund_manager`, `launch`; CLI `derive`, `new`, `fund`, `manager`, `search` (local); CSV export.

Done when: unit tests cover the candidate rule, the stem and key normalisers, master/feeder clustering, and the score table; `fundtracker new --since 30d` over real data lists launches with Axiaris near the top and a known large multi-fund manager near the bottom; running `derive` twice yields identical derived tables; the user reviews a week of output and confirms the ranking is sensible (this review is the acceptance gate; expect one tuning round).

Brief: "Implement WP4 from PLAN.md: the derived fund/manager/launch tables, hedge-fund classification, newness scoring with the weights in section 7.4, and the `new`, `fund`, `manager`, `search` CLI commands with CSV export."

### WP5: Web UI

Depends on: WP4.

Deliver: Flask app with the pages in section 8, HTMX interactions for status and filters, `review` table persistence, CSV export of the filtered view, status bar with "refresh now" running `catchup`. Templates kept simple and readable.

Done when: `fundtracker serve` opens the inbox; status changes persist across restarts; filters compose; export matches the visible rows; pages render acceptably at phone width.

Brief: "Implement WP5 from PLAN.md: the local web UI (inbox, launch/fund detail, manager detail, search, export, status bar) over the existing database with Flask, Jinja2 and HTMX."

### WP6: Scheduling, digest, documentation

Depends on: WP5.

Deliver: a PowerShell script and instructions to register a Windows Task Scheduler job running `ingest catchup && derive` daily at 06:00; `fundtracker digest` printing a markdown summary of new launches since the last digest (for pasting into notes or email); `README.md` covering install, first backfill, daily use, and how to tune score weights.

Done when: the scheduled task runs successfully overnight once; digest output reviewed by the user.

### WP7 (optional, later): enrichment

Candidates, each its own package when wanted: Form ADV matching for manager AUM and registration status; Atom feed intraday refresh; EDGAR full-text search in the search box; email delivery of the digest; a "similar launches" view based on shared recipients (placement agents).

## 10. Decisions taken and decisions left open

Taken (with reasoning; change them before WP0 if you disagree):

- **Python + requests + SQLite + Flask/HTMX, venv with pinned requirements.** Smallest stack that handles TSV and XML wrangling well and gives a usable UI without a front-end build, and it matches `../edgardly` so the user runs both projects the same way. Changed 2026-10-29 from an initial FastAPI/httpx/uv recommendation after reading edgardly.
- **Ingest everything, classify later.** All Form D filings, all industries, are stored. Hedge-fund filtering is a query. This is what makes manager linking across PE/VC/hedge possible and costs little.
- **Full backfill from the earliest quarter.** Needed for the "never seen before" signal to mean anything. One-time cost of a few MB per quarter.
- **Nightly daily-index ingestion as the source of truth; Atom feed optional.** The index is complete and simple; the feed is noisy and shallow.
- **Launch as the inbox unit, not filing.** Master/feeder/offshore trios would otherwise show as three rows.
- **Rule-based scoring with visible reasons, no ML.** Volumes are tiny and the user needs to trust and tune it.

Open (defaults applied; the user can override at any point):

1. **Backfill depth.** Default: every quarter available. Alternative: last 5 years only, which weakens manager-newness detection for long-tenured managers.
2. **Inbox default scope.** Default: `Hedge Fund` only. Alternative: include `Other Investment Fund` with 3C exemptions by default.
3. **Where review notes live.** Default: in SQLite. Alternative: a CSV the user edits elsewhere.
4. **Ingest schedule.** Default: catch-up on app start, plus an optional Task Scheduler job. Alternative: Task Scheduler only.
5. **User-Agent identity.** Must be a real name and email per SEC policy; the implementer should read it from `.env`, never commit it.

## 11. What edgardly offers and what it does not

`../edgardly` (same user, public on GitHub) is a Flask app that searches EDGAR, downloads filings and extracts XBRL financials for public companies. Reviewed 2026-10-29.

Reuse as reference, not as a dependency:
- `app/edgar_api.py`: the User-Agent header shape, the `_rate_limited_get` idea, EDGAR full-text search and submissions API calls, and a SQLite ticker cache. FundTracker's client is a stricter superset; copy the conventions, not the code.
- `app/tests/`: the pattern of patching the HTTP function and asserting against on-disk JSON fixtures with no network. Follow it.
- Process documents: `docs/V2_PLAN.md`, `PROGRESS.md`, `docs/SESSIONS.md`, `docs/acceptance/`. FundTracker adopts the same three-document structure.

Not reusable: everything XBRL, periods, scaffold and Excel. Form D has no XBRL and no financial statements. FundTracker is a separate repository, not an edgardly feature; the two share no code and the audiences differ (private fund discovery vs public company modelling).

## 12. Risks

- **SEC throttling or format changes.** Mitigated by the 5 req/s ceiling, disk caching, scraping the data sets page for links rather than hard-coding, and tolerant XML parsing. If the daily index path breaks, the Atom feed and submissions API are fallbacks.
- **Self-classification noise.** Some hedge funds file as Other Investment Fund; some PE funds file as Hedge Fund. Handled by the widened candidate rule and the name-pattern penalties, and by keeping everything so the rule can be changed later.
- **Entity-name encoding inconsistency** in related persons. Handled by joining all name parts before normalising, with fixtures covering both encodings.
- **Older schema versions** in the backfill may have fields the X0708 parser does not expect. The data sets already flatten them, so the XML parser only needs to handle schema versions that appear in daily ingestion (recent filings). Still, keep it tolerant.
- **False "new manager" positives** when a manager's prior funds were filed under a different entity name or address. Accept this; the score shows the reasons and the user triages. Form ADV enrichment (WP7) would reduce it.
