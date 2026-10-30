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

## Open questions

Numbered, dated, closed in place with the answer and the date.

1. (2026-10-29) Which schema versions other than X0708 appear in Form D XML filed since 2009, and which elements differ? Only matters for the XML path, which handles recent filings; the data sets flatten older ones. Resolve in WP1 by sampling.
2. (2026-10-29) Earliest quarter on the Form D data sets page. Resolve in WP2 by scraping the page.
3. (2026-10-29) Maximum depth of the Latest Filings Atom feed (`start` parameter). Only matters if WP7 intraday refresh is built.
