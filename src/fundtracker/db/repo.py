"""SQLite access for the raw tables: connect, upsert one filing, read it back.

Knows the dataclasses in model.py and nothing about XML or TSV, so both
loaders write through the same code. Column lists come from the dataclass
fields, which schema.sql mirrors (a test checks they agree).
"""

import dataclasses
import json
import re
import sqlite3
import types
import typing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fundtracker.model import (
    Filing,
    FormDFiling,
    Issuer,
    Offering,
    Recipient,
    RelatedPerson,
    Signature,
)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Table name, dataclass, and the FormDFiling attribute holding its rows.
CHILD_TABLES = (
    ("issuer", Issuer, "issuers"),
    ("related_person", RelatedPerson, "related_persons"),
    ("recipient", Recipient, "recipients"),
    ("signature", Signature, "signatures"),
)


def connect(db_path):
    """Open db_path (created if missing), apply the schema, enable WAL and foreign keys."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _add_missing_columns(conn)
    return conn


# Columns added to derived tables after databases were created with them.
# CREATE TABLE IF NOT EXISTS leaves an existing table alone, so they are added
# here; derive fills them on its next run.
ADDED_COLUMNS = (
    ("link_key", "display", "TEXT"),
    ("manager", "n_filings", "INTEGER"),
    ("manager", "latest_filing_date", "TEXT"),
    ("manager", "total_sold", "INTEGER"),
    ("manager", "total_investors", "INTEGER"),
    ("manager", "has_amendment", "INTEGER"),
    ("manager", "is_over_cap", "INTEGER"),
    ("manager", "fund_types", "TEXT"),
    ("manager", "city", "TEXT"),
    ("manager", "state", "TEXT"),
    ("manager", "country", "TEXT"),
    ("manager", "people", "TEXT"),
    ("manager", "edgar_cik", "TEXT"),
    ("manager", "prior_firms", "TEXT"),
    ("manager", "adviser_crd", "TEXT"),
    ("manager", "adviser_confidence", "TEXT"),
    ("manager", "adviser_kind", "TEXT"),
    ("manager", "website", "TEXT"),
    ("manager", "employees", "INTEGER"),
    ("manager", "raum", "INTEGER"),
    ("manager", "adv_hedge_funds", "INTEGER"),
    ("manager", "adv_private_assets", "INTEGER"),
    ("manager", "adv_filing_date", "TEXT"),
)


def connect_readonly(db_path):
    """Open an existing database for reading only, as the site does: no schema, no writes.

    Reads go on while derive holds its write transaction, since the database is in WAL mode.
    """
    conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _add_missing_columns(conn):
    for table, column, decl in ADDED_COLUMNS:
        if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def upsert_filing(conn, formd):
    """Write one FormDFiling to all six tables in a single transaction.

    filing and offering are upserted on accession. Child rows for the
    accession are deleted and rewritten, so a re-parse that drops a related
    person leaves no stale row. filing.loaded_at keeps its first value.
    """
    with conn:
        write_filings(conn, [formd])


def write_filings(conn, filings):
    """Write many FormDFilings with executemany batches; return how many.

    Same rules as upsert_filing, but does not commit: the caller owns the
    transaction, so a whole quarter and its ingest_log row commit together
    or not at all.
    """
    filings = list(filings)
    if not filings:
        return 0
    loaded_at = _now()
    _upsert_many(conn, "filing", [f.filing for f in filings], ("accession",),
                 extra={"loaded_at": loaded_at}, keep_on_update=("loaded_at",))
    _upsert_many(conn, "offering", [f.offering for f in filings], ("accession",))
    accessions = [(f.accession,) for f in filings]
    for table, _cls, attr in CHILD_TABLES:
        conn.executemany(f"DELETE FROM {table} WHERE accession = ?", accessions)
        _upsert_many(conn, table, [row for f in filings for row in getattr(f, attr)],
                     ("accession", "seq"))
    return len(filings)


def accessions_present(conn, accessions):
    """The subset of accessions already in the filing table."""
    found = set()
    accessions = list(accessions)
    for i in range(0, len(accessions), 500):
        chunk = accessions[i:i + 500]
        marks = ", ".join("?" * len(chunk))
        found.update(r[0] for r in conn.execute(
            f"SELECT accession FROM filing WHERE accession IN ({marks})", chunk))
    return found


def log_ingest(conn, source, key, status, n_rows=None, started_at=None, finished_at=None,
               error=None):
    """Record the outcome of one ingestion step, replacing any earlier row. Does not commit."""
    conn.execute(
        """
        INSERT INTO ingest_log (source, key, status, n_rows, started_at, finished_at, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source, key) DO UPDATE SET
            status = excluded.status, n_rows = excluded.n_rows, started_at = excluded.started_at,
            finished_at = excluded.finished_at, error = excluded.error
        """,
        (source, key, status, n_rows, started_at, finished_at, error),
    )


def ingest_status(conn, source, key):
    """The ingest_log row for (source, key) as a dict, or None."""
    row = conn.execute("SELECT * FROM ingest_log WHERE source = ? AND key = ?",
                       (source, key)).fetchone()
    return dict(row) if row else None


def replace_advisers(conn, kind, as_of, rows):
    """Replace every adviser of kind with rows (edgar.adv.AdviserRow) as of month as_of;
    return how many were written. Does not commit.

    The two files share no CRD in October 2026; should one appear in both, the
    registered row wins whichever file loads last.
    """
    conn.execute("DELETE FROM adviser WHERE kind = ?", (kind,))
    rows = [dataclasses.asdict(r) for r in rows]
    if not rows:
        return 0
    columns = ["as_of", *rows[0]]
    verb = "INSERT OR REPLACE" if kind == "ria" else "INSERT OR IGNORE"
    conn.executemany(
        f"{verb} INTO adviser ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
        ([as_of, *(_to_sql(v) for v in r.values())] for r in rows))
    return conn.execute("SELECT count(*) FROM adviser WHERE kind = ?", (kind,)).fetchone()[0]


def get_filing(conn, accession):
    """The FormDFiling stored under accession, or None."""
    row = conn.execute("SELECT * FROM filing WHERE accession = ?", (accession,)).fetchone()
    if row is None:
        return None
    offering = conn.execute("SELECT * FROM offering WHERE accession = ?", (accession,)).fetchone()
    children = {}
    for table, cls, attr in CHILD_TABLES:
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE accession = ? ORDER BY seq", (accession,)
        ).fetchall()
        children[attr] = tuple(_from_row(cls, r) for r in rows)
    return FormDFiling(
        filing=_from_row(Filing, row),
        offering=_from_row(Offering, offering) if offering else Offering(accession),
        **children,
    )


def filings_for_cik(conn, cik):
    """Every filing naming cik as primary issuer or co-issuer, oldest first.

    Ordered by filing date, then accession; filings with no date sort first.
    """
    rows = conn.execute(
        """
        SELECT f.accession FROM filing f
        WHERE f.cik = :cik
           OR EXISTS (SELECT 1 FROM issuer i WHERE i.accession = f.accession AND i.cik = :cik)
        ORDER BY f.filing_date, f.accession
        """,
        {"cik": cik},
    ).fetchall()
    return [get_filing(conn, r["accession"]) for r in rows]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _upsert_many(conn, table, objs, key, extra=None, keep_on_update=()):
    if not objs:
        return
    names = [f.name for f in dataclasses.fields(objs[0])]
    columns = names + list(extra or {})
    updates = [c for c in columns if c not in key and c not in keep_on_update]
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' * len(columns))}) "
        f"ON CONFLICT ({', '.join(key)}) DO "
        + (f"UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in updates)}" if updates else "NOTHING")
    )
    tail = tuple((extra or {}).values())
    conn.executemany(sql, (tuple(_to_sql(getattr(o, n)) for n in names) + tail for o in objs))


def _to_sql(value):
    if isinstance(value, tuple):
        return json.dumps(list(value))
    if isinstance(value, bool):
        return int(value)
    return value


def _kind(annotation):
    """'tuple', 'bool' or None for a dataclass field annotation."""
    args = typing.get_args(annotation) if isinstance(annotation, types.UnionType) else (annotation,)
    for arg in args:
        if typing.get_origin(arg) is tuple:
            return "tuple"
        if arg is bool:
            return "bool"
    return None


def _from_row(cls, row):
    kwargs = {}
    for f in dataclasses.fields(cls):
        value = row[f.name]
        kind = _kind(f.type)
        if kind == "tuple":
            value = tuple(json.loads(value)) if value else ()
        elif kind == "bool" and value is not None:
            value = bool(value)
        kwargs[f.name] = value
    return cls(**kwargs)


# Readers over the derived tables (derive.py), for the CLI and later the UI.


def launches_since(conn, since, min_score=0, fund_types=None):
    """Launches with window_start on or after since, best score first, then newest.

    fund_types limits the launch's fund_type (None for every type). Each row
    is a dict of the launch columns plus manager_name and the anchor fund's
    fund columns prefixed with fund_.
    """
    return find_launches(conn, since, min_score=min_score, fund_types=fund_types)


# Bands the site filters on, as (lowest included, highest excluded); None is open.
MIN_INVESTMENT_BANDS = {"<100k": (None, 100_000), "100k-1m": (100_000, 1_000_000),
                        ">1m": (1_000_000, None)}
RAISED_BANDS = {"0": (None, 1), ">0": (1, None), ">1m": (1_000_000, None),
                ">10m": (10_000_000, None)}
# Form ADV item 5A total employees; only registered advisers report it.
EMPLOYEE_BANDS = {"1-5": (1, 6), "6-20": (6, 21), "21+": (21, None)}
# The adviser filter: a high or medium link (manager.adviser_crd is set only for those).
ADVISER_FILTERS = {"linked": "m.adviser_crd IS NOT NULL", "ria": "m.adviser_kind = 'ria'",
                   "era": "m.adviser_kind = 'era'", "none": "m.adviser_crd IS NULL"}
LAUNCH_SORTS = {
    "score": "l.newness_score DESC, l.window_start DESC, l.launch_id",
    "date": "l.window_start DESC, l.newness_score DESC, l.launch_id",
    "raised": "coalesce(f.total_amount_sold_num, -1) DESC, l.newness_score DESC, l.launch_id",
}


def _band(column, band, bands, where, params):
    """Add column's band to where. Nothing raised counts a null amount as 0."""
    if band not in bands:
        return
    low, high = bands[band]
    if low is not None:
        where.append(f"{column} >= ?")
        params.append(low)
    if high is not None:
        where.append(f"coalesce({column}, 0) < ?")
        params.append(high)


def _in(column, values, where, params):
    if values:
        where.append(f"{column} IN ({', '.join('?' * len(values))})")
        params.extend(values)


def find_launches(conn, since, until=None, min_score=0, fund_types=None, rule=None, states=(),
                  min_inv=None, raised=None, hide_platforms=False, sort="score"):
    """Launches with window_start from since to until (both included), filtered and sorted.

    rule is "b" or "c" for an anchor fund claiming Rule 506(b) or 506(c);
    states are the anchor fund's state codes (a two-letter state, or EDGAR's
    country code); min_inv and raised are keys of MIN_INVESTMENT_BANDS and
    RAISED_BANDS on the anchor fund's terms; hide_platforms leaves out
    launches whose reasons name an over-cap organisation; sort is a key of
    LAUNCH_SORTS. Rows are as launches_since returns them.
    """
    where = ["l.window_start >= ?", "coalesce(l.newness_score, 0) >= ?"]
    params = [since, min_score]
    if until:
        where.append("l.window_start <= ?")
        params.append(until)
    _in("l.fund_type", fund_types, where, params)
    if rule in ("b", "c"):
        where.append("EXISTS (SELECT 1 FROM json_each(f.exemptions) WHERE value = ?)")
        params.append(f"06{rule}")
    _in("f.state", list(states), where, params)
    _band("f.min_investment_num", min_inv, MIN_INVESTMENT_BANDS, where, params)
    _band("f.total_amount_sold_num", raised, RAISED_BANDS, where, params)
    if hide_platforms:
        where.append("NOT EXISTS (SELECT 1 FROM json_each(l.newness_reasons) "
                     "WHERE json_extract(value, '$.code') = 'platform')")
    fund_columns = [r["name"] for r in conn.execute("PRAGMA table_info(fund)")]
    rows = conn.execute(
        f"""
        SELECT l.*, m.display_name AS manager_name,
               {', '.join(f'f.{c} AS fund_{c}' for c in fund_columns)}
        FROM launch l
        LEFT JOIN manager m ON m.manager_id = l.manager_id
        LEFT JOIN fund f ON f.cik = l.anchor_cik
        WHERE {' AND '.join(where)}
        ORDER BY {LAUNCH_SORTS.get(sort, LAUNCH_SORTS["score"])}
        """, params).fetchall()
    return [dict(r) for r in rows]


def funds_for_launches(conn, launches):
    """Every fund of the given launch rows as a dict of fund columns plus launch_id and
    launch_score, in launch order and then the launch's own fund order."""
    out = []
    for launch in launches:
        ciks = json.loads(launch["fund_ciks"])
        rows = {r["cik"]: dict(r) for r in conn.execute(
            f"SELECT * FROM fund WHERE cik IN ({', '.join('?' * len(ciks))})", ciks)}
        out.extend(dict(rows[c], launch_id=launch["launch_id"], launch_score=launch["newness_score"])
                   for c in ciks if c in rows)
    return out


def last_ingest_key(conn, source, statuses=("ok", "no_index")):
    """The latest ingest_log key for source with one of statuses, or None."""
    marks = ", ".join("?" * len(statuses))
    return conn.execute(f"SELECT max(key) FROM ingest_log WHERE source = ? AND status IN ({marks})",
                        (source, *statuses)).fetchone()[0]


def site_status(conn):
    """What the site's status bar shows: last daily day loaded, filings, last derive."""
    derive = conn.execute(
        "SELECT key, finished_at FROM ingest_log WHERE source = 'derive' AND status = 'ok' "
        "ORDER BY finished_at DESC LIMIT 1").fetchone()
    return {"last_daily": last_ingest_key(conn, "daily"),
            "filings": conn.execute("SELECT count(*) FROM filing").fetchone()[0],
            "last_derive": derive["finished_at"] if derive else None}


def age_cutoff(today, age):
    """The date age before today: age is a number of months, or a timedelta."""
    from fundtracker.traction import months_before

    return today - age if isinstance(age, timedelta) else months_before(today, age)


def emerging_managers(conn, today, min_age_months, max_age_months, fund_types=None,
                      exclude_over_cap=True, max_funds=20):
    """Managers first seen between max_age and min_age before today, oldest bound included.

    Ages are months, or a timedelta for a span in days. The rest is as for
    managers_first_seen.
    """
    return managers_first_seen(conn, age_cutoff(today, max_age_months), age_cutoff(today, min_age_months),
                               fund_types, exclude_over_cap, max_funds)


def managers_first_seen(conn, oldest, newest, fund_types=None, exclude_over_cap=True, max_funds=20,
                        raised=None, states=(), prior_firm="any", adviser="any", employees=None):
    """Managers first seen from oldest to newest (dates, both included).

    Only managers with a Pooled Investment Fund are listed; fund_types (for
    example ["Hedge Fund"]) keeps those with a fund whose latest fund_type is
    one of them. The 3C candidate rule that new uses is not applied: it let
    institutions' credit and private equity series in (Session 7). Over-cap
    organisations and managers with max_funds or more funds are left out when
    asked. raised is a key of RAISED_BANDS on the manager's total sold;
    states are the state codes of its earliest fund (edgar_cik); prior_firm
    is "with", "without" or "any"; adviser is a key of ADVISER_FILTERS or "any";
    employees a key of EMPLOYEE_BANDS. Each row is a dict of manager columns with
    "funds": its fund rows, oldest first, each with latest_total_amount_sold
    and latest_n_investors from its latest filing.
    """
    where = ["m.first_seen_date >= ?", "m.first_seen_date <= ?"]
    params = [oldest.isoformat(), newest.isoformat()]
    if exclude_over_cap:
        where.append("coalesce(m.is_over_cap, 0) = 0")
    if max_funds is not None:
        where.append("m.n_funds < ?")
        params.append(max_funds)
    _band("m.total_sold", raised, RAISED_BANDS, where, params)
    if states:
        where.append(f"(SELECT state FROM fund WHERE cik = m.edgar_cik) IN ({', '.join('?' * len(states))})")
        params.extend(states)
    if prior_firm in ("with", "without"):
        where.append(f"coalesce(m.prior_firms, '[]') {'<>' if prior_firm == 'with' else '='} '[]'")
    if adviser in ADVISER_FILTERS:
        where.append(ADVISER_FILTERS[adviser])
    if employees in EMPLOYEE_BANDS:
        where.append("m.employees IS NOT NULL")
        _band("m.employees", employees, EMPLOYEE_BANDS, where, params)
    pooled = "f.industry_group = 'Pooled Investment Fund'"
    if fund_types:
        pooled += f" AND f.fund_type IN ({', '.join('?' * len(fund_types))})"
        params.extend(fund_types)
    where.append(f"EXISTS (SELECT 1 FROM fund_manager fm JOIN fund f USING (cik) "
                 f"WHERE fm.manager_id = m.manager_id AND {pooled})")
    managers = [dict(r, funds=[]) for r in conn.execute(
        f"SELECT m.* FROM manager m WHERE {' AND '.join(where)} ORDER BY m.manager_id", params)]
    by_id = {m["manager_id"]: m for m in managers}
    ids = list(by_id)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for r in conn.execute(
                f"""
                SELECT f.*, fm.manager_id, lo.total_amount_sold_num AS latest_total_amount_sold,
                       lo.n_investors AS latest_n_investors
                FROM fund_manager fm JOIN fund f USING (cik)
                LEFT JOIN offering lo ON lo.accession = f.latest_accession
                WHERE fm.manager_id IN ({', '.join('?' * len(chunk))})
                ORDER BY f.first_filing_date, f.cik
                """, chunk):
            by_id[r["manager_id"]]["funds"].append(dict(r))
    return managers


def get_fund(conn, cik):
    """The fund row for cik as a dict, with manager_id, evidence and manager_name, or None.
    Accepts an unpadded CIK."""
    row = conn.execute(
        """
        SELECT f.*, fm.manager_id, fm.evidence, m.display_name AS manager_name FROM fund f
        LEFT JOIN fund_manager fm USING (cik) LEFT JOIN manager m USING (manager_id)
        WHERE f.cik = ?
        """, (pad_cik(cik),)).fetchone()
    return dict(row) if row else None


def get_manager(conn, manager_id):
    """The manager row as a dict with its funds (oldest first) under "funds", or None."""
    row = conn.execute("SELECT * FROM manager WHERE manager_id = ?", (manager_id,)).fetchone()
    if row is None:
        return None
    manager = dict(row)
    manager["funds"] = [dict(r) for r in conn.execute(
        """
        SELECT f.*, fm.evidence FROM fund_manager fm JOIN fund f USING (cik)
        WHERE fm.manager_id = ? ORDER BY f.first_filing_date, f.cik
        """, (manager_id,))]
    manager["adviser"], manager["possible_advisers"] = manager_adviser(conn, manager_id)
    return manager


def manager_adviser(conn, manager_id):
    """(adviser, possible advisers) for a manager. adviser is the linked adviser row as a dict
    with confidence and match_kinds, or None; possible advisers are the candidate adviser
    rows when there is no high or medium link, by CRD."""
    if not _has_table(conn, "manager_adviser"):
        return None, []
    link = conn.execute("SELECT * FROM manager_adviser WHERE manager_id = ?", (manager_id,)).fetchone()
    if link is None:
        return None, []
    if link["crd"] is not None:
        row = conn.execute("SELECT * FROM adviser WHERE crd = ?", (link["crd"],)).fetchone()
        if row is not None:
            return dict(row, confidence=link["confidence"],
                        match_kinds=json.loads(link["match_kinds"])), []
    crds = json.loads(link["evidence"]).get("candidates", [])
    rows = {r["crd"]: dict(r) for r in conn.execute(
        f"SELECT * FROM adviser WHERE crd IN ({', '.join('?' * len(crds))})", crds)} if crds else {}
    return None, [rows[c] for c in crds if c in rows]


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (name,)).fetchone() is not None


def get_launch(conn, launch_id):
    """The launch row as a dict with manager_name, or None. Under "funds", its funds in the
    launch's order, each with sale_date and the primary issuer's address and phone from the
    fund's first filing; under "people" and "signers", those of the funds' first filings."""
    row = conn.execute(
        "SELECT l.*, m.display_name AS manager_name FROM launch l "
        "LEFT JOIN manager m USING (manager_id) WHERE l.launch_id = ?", (launch_id,)).fetchone()
    if row is None:
        return None
    launch = dict(row)
    ciks = json.loads(launch["fund_ciks"] or "[]")
    funds = {r["cik"]: dict(r) for r in conn.execute(
        f"""
        SELECT f.*, o.sale_date, i.street1, i.street2, i.city, i.state_or_country,
               i.state_or_country_desc, i.zip, i.phone
        FROM fund f
        LEFT JOIN offering o ON o.accession = f.first_accession
        LEFT JOIN issuer i ON i.accession = f.first_accession AND i.is_primary = 1
        WHERE f.cik IN ({', '.join('?' * len(ciks))})
        """, ciks)}
    launch["funds"] = [funds[c] for c in ciks if c in funds]
    accessions = [f["first_accession"] for f in launch["funds"]]
    launch["people"] = people_on(conn, accessions)
    launch["signers"] = signers_on(conn, accessions)
    return launch


def filings_timeline(conn, manager_id):
    """One dict per filing of the manager's funds, oldest first: filing_date, submission_type,
    accession, cik, fund name, and the offering's amounts, investors and minimum."""
    rows = conn.execute(
        """
        SELECT f.filing_date, f.submission_type, f.accession, f.cik, fu.name,
               o.total_amount_sold_num, o.n_investors, o.min_investment_num,
               o.total_offering_amount_num, o.total_offering_amount_is_indefinite
        FROM fund_manager fm JOIN fund fu USING (cik)
        JOIN filing f ON f.cik = fm.cik
        LEFT JOIN offering o ON o.accession = f.accession
        WHERE fm.manager_id = ?
        ORDER BY f.filing_date, f.accession
        """, (manager_id,)).fetchall()
    return [dict(r) for r in rows]


def people_on(conn, accessions):
    """The related persons on the given filings, one dict per name_norm in filing order:
    name_norm, name (as displayed), is_entity, roles (merged) and n_filings."""
    from fundtracker.normalize import entity_display_name, person_display_name

    people = {}
    for chunk in _chunks(accessions):
        for r in conn.execute(
                f"SELECT * FROM related_person WHERE accession IN ({', '.join('?' * len(chunk))}) "
                "ORDER BY accession, seq", chunk):
            if not r["name_norm"]:
                continue
            p = people.get(r["name_norm"])
            if p is None:
                display = entity_display_name if r["is_entity"] else person_display_name
                p = people[r["name_norm"]] = {
                    "name_norm": r["name_norm"], "is_entity": bool(r["is_entity"]),
                    "name": display(r["first"], r["middle"], r["last"]) or r["name_norm"],
                    "roles": [], "accessions": set()}
            p["roles"].extend(x for x in json.loads(r["relationships"] or "[]") if x not in p["roles"])
            p["accessions"].add(r["accession"])
    for p in people.values():
        p["n_filings"] = len(p.pop("accessions"))
    return list(people.values())


def signers_on(conn, accessions):
    """Distinct signers with their titles on the given filings, as dicts, by signer."""
    seen = {}
    for chunk in _chunks(accessions):
        for r in conn.execute(
                f"SELECT signer, signer_norm, title FROM signature "
                f"WHERE accession IN ({', '.join('?' * len(chunk))})", chunk):
            seen.setdefault((r["signer_norm"] or "", r["title"] or ""), " ".join((r["signer"] or "").split()))
    return [{"signer": signer, "signer_norm": norm, "title": title}
            for (norm, title), signer in sorted(seen.items())]


def manager_phone(conn, manager):
    """The phone as filed on the primary issuer of the manager's earliest fund's latest filing."""
    row = conn.execute(
        "SELECT i.phone FROM fund f JOIN issuer i ON i.accession = f.latest_accession AND i.is_primary = 1 "
        "WHERE f.cik = ?", (manager["edgar_cik"],)).fetchone()
    return row["phone"] if row else None


def appearances(conn, name_norm):
    """Every fund name_norm appears on, as a related person or a signer, with its manager.

    Returns one dict per fund, in order of first appearance: cik, name (the
    fund's), fund_type, manager_id, manager_name, roles (merged; a signature
    adds "Signer: title"), first_date, last_date, first_accession (this
    name's first filing on the fund) and n_filings.
    """
    rows = conn.execute(
        """
        SELECT f.filing_date, f.accession, f.cik, rp.relationships AS roles, NULL AS title, 0 AS signed
        FROM related_person rp JOIN filing f USING (accession) WHERE rp.name_norm = :n
        UNION ALL
        SELECT f.filing_date, f.accession, f.cik, '[]', s.title, 1
        FROM signature s JOIN filing f USING (accession) WHERE s.signer_norm = :n
        ORDER BY 1, 2
        """, {"n": name_norm}).fetchall()
    by_cik = {}
    for r in rows:
        a = by_cik.setdefault(r["cik"], {
            "cik": r["cik"], "name": None, "fund_type": None, "manager_id": None, "manager_name": None,
            "roles": [], "first_date": r["filing_date"], "first_accession": r["accession"],
            "accessions": set()})
        roles = json.loads(r["roles"] or "[]")
        if r["signed"]:
            roles = [f"Signer: {r['title']}" if r["title"] else "Signer"]
        a["roles"].extend(x for x in roles if x not in a["roles"])
        a["last_date"] = r["filing_date"]
        a["accessions"].add(r["accession"])
    for chunk in _chunks(by_cik):
        for r in conn.execute(
                f"""
                SELECT f.cik, f.name, f.fund_type, fm.manager_id, m.display_name AS manager_name
                FROM fund f LEFT JOIN fund_manager fm USING (cik) LEFT JOIN manager m USING (manager_id)
                WHERE f.cik IN ({', '.join('?' * len(chunk))})
                """, chunk):
            by_cik[r["cik"]].update(name=r["name"], fund_type=r["fund_type"], manager_id=r["manager_id"],
                                    manager_name=r["manager_name"])
    for a in by_cik.values():
        a["n_filings"] = len(a.pop("accessions"))
    return list(by_cik.values())


def person_name(conn, name_norm):
    """name_norm as a related person's name is displayed, or as a signer wrote it, or None."""
    from fundtracker.normalize import entity_display_name, person_display_name

    r = conn.execute("SELECT first, middle, last, is_entity FROM related_person WHERE name_norm = ? LIMIT 1",
                     (name_norm,)).fetchone()
    if r:
        display = entity_display_name if r["is_entity"] else person_display_name
        return display(r["first"], r["middle"], r["last"]) or name_norm
    r = conn.execute("SELECT signer FROM signature WHERE signer_norm = ? LIMIT 1", (name_norm,)).fetchone()
    return " ".join(r["signer"].split()) if r and r["signer"] else None


# search_index: FTS5 over fund names, manager display names and related person
# names, rebuilt by derive. It is created here rather than in schema.sql so a
# Python build without FTS5 still opens the database; search then falls back
# to LIKE, as it does on a database derive has not yet indexed. Rows go in
# in the order search shows them (funds newest first, managers newest first,
# people most filings first), so a query ordered by rowid stops at its limit
# instead of sorting every match; sort keeps the value that order came from.
SEARCH_INDEX_COLUMNS = ("kind, id UNINDEXED, name, sort UNINDEXED, "
                        "tokenize = 'unicode61 remove_diacritics 2'")


def has_fts5(conn):
    return "ENABLE_FTS5" in {r[0] for r in conn.execute("PRAGMA compile_options")}


def rebuild_search_index(conn, schema="main"):
    """Drop and refill search_index from fund, manager and related_person; return its rows,
    or None without FTS5. Does not commit."""
    if not has_fts5(conn):
        return None
    table = f"{schema}.search_index"
    conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(f"CREATE VIRTUAL TABLE {table} USING fts5({SEARCH_INDEX_COLUMNS})")
    for select in (
            "SELECT 'fund', cik, name, first_filing_date FROM fund WHERE name <> '' "
            "ORDER BY first_filing_date DESC, cik",
            "SELECT 'manager', manager_id, display_name, first_seen_date FROM manager "
            "WHERE display_name <> '' ORDER BY first_seen_date DESC, manager_id",
            "SELECT 'person', name_norm, name_norm, count(*) AS n FROM related_person "
            "WHERE name_norm <> '' GROUP BY name_norm ORDER BY n DESC, name_norm"):
        conn.execute(f"INSERT INTO {table} (kind, id, name, sort) {select}")
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _fts_query(text):
    """Every word of text as a prefix of a word in name, all required:
    'howe cap' -> 'name : "howe"* AND name : "cap"*'. None when text has no letters or digits."""
    words = re.findall(r"[^\W_]+", text or "")
    return " AND ".join(f'name : "{w}"*' for w in words) or None


def _index_rows(conn, match, kind, order, limit):
    return conn.execute(
        f"SELECT id, name, sort FROM search_index WHERE search_index MATCH ? "
        f"ORDER BY {order} LIMIT ?", (f"kind : {kind} AND {match}", limit)).fetchall()


def _search_indexed(conn, text, fn):
    """fn(match) on the search index, or None when there is no usable index."""
    match = _fts_query(text)
    if match is None:
        return None
    try:
        return fn(match)
    except sqlite3.OperationalError:  # no search_index yet, or no FTS5 in this build
        return None


def search(conn, text, limit=50):
    """Funds, managers and people whose names match text, up to limit of each.

    Returns {"funds": [...], "managers": [...], "people": [...]}: fund rows
    with manager_id, newest first; manager rows, newest first; and people as
    name_norm with n_filings, most filings first. Every word of text matches
    the start of a word in the name, through search_index; without the index,
    text matches anywhere in the name through LIKE.
    """
    def indexed(match):
        fund_ids = [r["id"] for r in _index_rows(conn, match, "fund", "rowid", limit)]
        manager_ids = [r["id"] for r in _index_rows(conn, match, "manager", "rowid", limit)]
        people = _index_rows(conn, match, "person", "rowid", limit)
        return {"funds": _by_ids(conn, FUND_SEARCH_SQL, "f.cik", fund_ids, "f.first_filing_date DESC, f.cik"),
                "managers": _by_ids(conn, MANAGER_SEARCH_SQL, "manager_id", manager_ids,
                                    "first_seen_date DESC, manager_id"),
                "people": [{"name_norm": r["id"], "n_filings": r["sort"]} for r in people]}

    return _search_indexed(conn, text, indexed) or _search_like(conn, text, limit)


FUND_SEARCH_SQL = """
    SELECT f.cik, f.name, f.fund_type, f.first_filing_date, f.state, fm.manager_id
    FROM fund f LEFT JOIN fund_manager fm USING (cik)"""
MANAGER_SEARCH_SQL = """
    SELECT manager_id, display_name, first_seen_date, n_funds, city, state, country FROM manager"""


def _by_ids(conn, sql, column, ids, order):
    marks = ", ".join("?" * len(ids))
    return [dict(r) for r in conn.execute(f"{sql} WHERE {column} IN ({marks}) ORDER BY {order}", ids)]


def _search_like(conn, text, limit):
    p = _like_pattern(text)
    funds = conn.execute(
        f"""{FUND_SEARCH_SQL}
        WHERE f.name LIKE ? ESCAPE '!' ORDER BY f.first_filing_date DESC, f.cik LIMIT ?
        """, (p, limit)).fetchall()
    managers = conn.execute(
        f"""{MANAGER_SEARCH_SQL}
        WHERE display_name LIKE ? ESCAPE '!' ORDER BY first_seen_date DESC, manager_id LIMIT ?
        """, (p, limit)).fetchall()
    # The people query reads only the name_norm index, about a third of a second on three million rows.
    people = conn.execute(
        """
        SELECT name_norm, count(*) AS n_filings FROM related_person
        WHERE name_norm LIKE ? ESCAPE '!' GROUP BY name_norm ORDER BY n_filings DESC, name_norm LIMIT ?
        """, (p, limit)).fetchall()
    return {"funds": [dict(r) for r in funds], "managers": [dict(r) for r in managers],
            "people": [dict(r) for r in people]}


def _chunks(values, size=500):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def search_names(conn, text, limit=50):
    """Case-insensitive local search over fund names, manager names and related persons.

    Returns up to limit dicts with kind (fund, manager or person), id (a CIK
    or manager_id), name and, for persons, the CIK of a fund they appear on.
    Managers come first, then funds, then persons, each by name. Matching is
    as for search.
    """
    def indexed(match):
        rows = []
        for kind in ("manager", "fund", "person"):
            for r in _index_rows(conn, match, kind, "name", limit - len(rows)):
                cik = conn.execute(
                    "SELECT min(f.cik) FROM related_person rp JOIN filing f USING (accession) "
                    "WHERE rp.name_norm = ?", (r["id"],)).fetchone()[0] if kind == "person" else None
                rows.append({"kind": kind, "id": r["id"], "name": r["name"], "cik": cik})
            if len(rows) >= limit:
                break
        return rows

    rows = _search_indexed(conn, text, indexed)
    return rows if rows is not None else _search_names_like(conn, text, limit)


def _search_names_like(conn, text, limit):
    pattern = _like_pattern(text)
    rows = conn.execute(
        """
        SELECT * FROM (
            SELECT 'fund' AS kind, cik AS id, name, NULL AS cik, 1 AS rank FROM fund
            WHERE name LIKE :p ESCAPE '!'
            UNION ALL
            SELECT 'manager', manager_id, display_name, NULL, 0 FROM manager
            WHERE display_name LIKE :p ESCAPE '!'
            UNION ALL
            SELECT 'person', rp.name_norm, rp.name_norm, min(f.cik), 2
            FROM related_person rp JOIN filing f USING (accession)
            WHERE rp.name_norm LIKE :p ESCAPE '!' GROUP BY rp.name_norm
        ) ORDER BY rank, name LIMIT :limit
        """, {"p": pattern, "limit": limit}).fetchall()
    return [{k: r[k] for k in ("kind", "id", "name", "cik")} for r in rows]


def _like_pattern(text):
    """%text% for LIKE ... ESCAPE '!', with the wildcards in text escaped.

    SQLite's LIKE ignores case for ASCII letters, so no upper() is needed.
    """
    text = (text or "").strip()
    for ch in ("!", "%", "_"):
        text = text.replace(ch, "!" + ch)
    return f"%{text}%"


def pad_cik(cik):
    """A CIK as stored: ten digits with leading zeros."""
    cik = str(cik).strip()
    return cik.zfill(10) if cik.isdigit() else cik
