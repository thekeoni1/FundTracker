# FundTracker session prompts

One prompt per work session, each a record of what was asked for as of its date. What actually happened is in PROGRESS.md. Each prompt is self-contained: paste it into a fresh Claude Code session in the FundTracker folder and it loads its own context before doing anything.

Rules of use:

- Do not start session N+1 until session N's exit criteria are met and recorded in PROGRESS.md.
- Every session begins by reading PLAN.md, PROGRESS.md and docs/edgar-formd-reference.md, and ends by updating PROGRESS.md.
- Tests never touch the network. Real filings go into tests/fixtures.
- One commit per numbered step, in order. Plain prose, no em dashes, in every doc and user-visible string.

## Session 0. Scaffold and EDGAR client (WP0)

Read PLAN.md (sections 5, 9 WP0, 10 and 11), PROGRESS.md, and docs/edgar-formd-reference.md section 1 first. This session builds the project skeleton and the HTTP client only. No parser, no database, no UI.

1. Create the project: `pyproject.toml` with `src` layout and package `fundtracker`, pinned `requirements.txt` (requests) and `requirements-dev.txt` (`-r requirements.txt`, pytest, pytest-timeout), `.gitignore` covering `data/`, `.env`, `venv/`, `__pycache__/`, `.pytest_cache/`. Create a venv, install editable, confirm `python -c "import fundtracker"` works. `git init` and commit.
2. `src/fundtracker/config.py`: load `FUNDTRACKER_USER_AGENT` from a `.env` file in the project root (and the environment), fail with a clear message if it is missing or still a placeholder, expose `DATA_DIR` (default `./data`), `RAW_DIR` (`data/raw`), `DB_PATH` (`data/fundtracker.db`), `RATE_LIMIT_PER_SEC` (default 5). Write `.env.example`. Commit.
3. `src/fundtracker/edgar/client.py`: a class wrapping `requests.Session` with the SEC headers (User-Agent, Accept-Encoding gzip deflate), a token-bucket limiter at the configured rate, retry with exponential backoff on 429, 403 and 5xx (max 5 tries, then raise), a 30 second timeout, and `get_cached(url)` that stores the body under `RAW_DIR` keyed by a hash of the URL plus a readable suffix and serves it on later calls; `get(url)` bypasses the cache. Add a `__main__` that fetches one URL and prints the body, reporting whether it came from cache. Commit.
4. `tests/test_client.py`: patch `requests.Session.request` (or the session's `get`) and assert: headers are set; N calls take at least (N-1)/rate seconds with a fake clock; 429 then 200 yields one retry; 5 consecutive 500s raise; a cached URL is requested once across two calls. No network. Commit.
5. Append the session entry to PROGRESS.md (shipped, test count, open questions). Commit.

Constraints: match the stack in PLAN.md section 5 (requests, not httpx; venv, not uv). Keep dependencies to requests plus the test tools. Do not fetch anything from EDGAR inside tests. The live smoke check in the exit criteria is run by hand, once.

Exit criteria: `pytest` green with no network; `python -m fundtracker.edgar.client https://www.sec.gov/Archives/edgar/data/2158817/000215881726000001/index.json` prints the folder JSON and a second run says it was served from cache; five commits in the order above; PROGRESS.md updated.
