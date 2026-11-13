# FundTracker

FundTracker finds newly launched hedge funds and young hedge fund managers in the SEC's Form D filings. It loads every Form D since 2008 into a local SQLite database, links funds to the managers behind them, and scores each launch and each manager. A local site shows the two rankings, with filters, Excel export and links out to EDGAR, LinkedIn and the SEC adviser search.

## Setup

You need Python 3.12 or later and about 3 GB of disk. On Windows, from a terminal:

```
git clone https://github.com/thekeoni1/FundTracker.git
cd FundTracker
py -m venv venv
venv\Scripts\pip install -r requirements.txt -e .
copy .env.example .env
venv\Scripts\python -m fundtracker backfill
venv\Scripts\python -m fundtracker serve
```

Before `backfill`, edit `.env` and set `FUNDTRACKER_USER_AGENT` to your name and email. The SEC requires it on every request and blocks requests without it. `backfill` downloads the quarterly Form D data sets and loads about 780,000 filings in roughly 15 minutes. `serve` then sees that no daily filings are loaded and starts a refresh, which fetches each filing since the last quarterly data set, one request at a time under the SEC's rate limit. Late in a quarter that is several thousand filings and up to half an hour; the rankings fill in when it ends. Open http://127.0.0.1:8000.

On macOS or Linux, use `python3 -m venv venv`, `venv/bin/pip`, `venv/bin/python` and `cp`.

Run `backfill`, `refresh` and `derive` from your own terminal. A process started by another tool, such as an AI coding assistant, can be stopped partway when the machine runs low on memory. derive then rolls back and nothing is lost, but the run is wasted.

## Daily use

Start the site with `venv\Scripts\python -m fundtracker serve`. If the last daily index loaded is older than the previous weekday, it refreshes in the background first and the pages keep working while it runs. `--no-refresh` skips this. The Refresh button on the site does the same job on demand.

A refresh is `ingest catchup` (every weekday's filings since the last one loaded) followed by `derive` (rebuild funds, managers, launches, scores and the search index). `venv\Scripts\python -m fundtracker refresh` runs it from the terminal.

To refresh without opening the site, `fundtracker schedule install` adds a Windows Task Scheduler task that runs `refresh` at 06:30 on weekdays, as you, while you are logged on. Its output goes to `refresh.log` in the data folder. A missed run (the laptop was asleep) is caught up the next time anything refreshes. `schedule show` and `schedule remove` manage the task. On other systems the command prints a cron line to add instead.

`fundtracker digest` writes `data/exports/digest-YYYYMMDD.md` with the week's best launches and the emerging managers that passed 5,000,000 raised or added a vehicle since the previous digest. The first digest only records a baseline.

`venv\Scripts\python -m fundtracker --help` lists the other commands (`new`, `emerging`, `fund`, `manager`, `search`, `stats`).

## The two rankings

Launches lists new funds by filing date with a newness score from 0 to 100. Most of the score comes from a manager with no earlier fund on EDGAR, a single filing, and nothing sold yet; a recent incorporation, Rule 506(c) and a minimum investment between 100,000 and 1,000,000 add a little, and an established manager or a private equity style name takes points off. Master, feeder and offshore vehicles filed together count as one launch. Each row shows the reasons behind its score.

Emerging lists managers whose first Form D is 6 to 18 months old, ranked by traction: money raised, the number of investors, amendments, a second vehicle and recent filings. Managers whose people filed for a large firm before are labelled with it. Platforms and firms with 20 or more funds are left out. A manager's offering is open while one of its funds has filed within 13 calendar months and still reports an indefinite amount or money remaining; otherwise it is lapsed, and the Offering filter splits on that.

The links above each page's filters are presets, saved combinations of the filters below them.

## Data and settings

The database, the cached EDGAR XML and the data set zips live in `data/`, or wherever `FUNDTRACKER_DATA_DIR` in `.env` points. Keep them out of OneDrive or any other synced folder: the database is about 2 GB and a sync client re-uploads it after every refresh. Exports and digests go to `data/exports/` unless `FUNDTRACKER_EXPORT_DIR` says otherwise. Nothing under `data/` is committed.

The scoring rules are constants at the top of their modules:

- `src/fundtracker/newness.py`: launch score weights and the minimum investment band.
- `src/fundtracker/traction.py`: emerging score weights, raised bands, the 20 fund limit.
- `src/fundtracker/classify.py`: which filings count as hedge fund candidates.
- `src/fundtracker/derive.py`: how funds are linked into managers, and the 30 day launch window.

Run `derive` after changing any of them.

## Tests

`venv\Scripts\pip install -r requirements-dev.txt`, then `venv\Scripts\python -m pytest`. The tests never touch the network; they run on real filings saved under `tests/fixtures`.

## Documents

- `PLAN.md`: the design, data model and work packages.
- `PROGRESS.md`: decisions, a log of each work session, and open questions.
- `docs/edgar-formd-reference.md`: verified facts about the EDGAR sources.
- `docs/SESSIONS.md`: the prompt for each work session.
