"""SQLite access for the raw tables: connect, upsert one filing, read it back.

Knows the dataclasses in model.py and nothing about XML or TSV, so both
loaders write through the same code. Column lists come from the dataclass
fields, which schema.sql mirrors (a test checks they agree).
"""

import dataclasses
import json
import sqlite3
import types
import typing
from datetime import datetime, timezone
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
    return conn


def upsert_filing(conn, formd):
    """Write one FormDFiling to all six tables in a single transaction.

    filing and offering are upserted on accession. Child rows for the
    accession are deleted and rewritten, so a re-parse that drops a related
    person leaves no stale row. filing.loaded_at keeps its first value.
    """
    accession = formd.accession
    with conn:
        _upsert(conn, "filing", formd.filing, ("accession",),
                extra={"loaded_at": _now()}, keep_on_update=("loaded_at",))
        _upsert(conn, "offering", formd.offering, ("accession",))
        for table, _cls, attr in CHILD_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE accession = ?", (accession,))
            for row in getattr(formd, attr):
                _upsert(conn, table, row, ("accession", "seq"))


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


def _upsert(conn, table, obj, key, extra=None, keep_on_update=()):
    values = {f.name: _to_sql(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    values.update(extra or {})
    columns = list(values)
    updates = [c for c in columns if c not in key and c not in keep_on_update]
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + c for c in columns)}) "
        f"ON CONFLICT ({', '.join(key)}) DO "
        + (f"UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in updates)}" if updates else "NOTHING")
    )
    conn.execute(sql, values)


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
