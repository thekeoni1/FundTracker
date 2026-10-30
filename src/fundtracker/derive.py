"""The derived layer (PLAN.md section 6): funds, linking keys, managers, launches.

Everything here is rebuilt from the raw tables on every run: each builder
deletes its table's rows and refills them. review is user state and is never
touched. The builders do not commit; derive.run wraps them in one
transaction.
"""

import json
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timezone

from fundtracker import classify, normalize
from fundtracker.db import repo
from fundtracker.normalize import (
    entity_display_name,
    entity_stem,
    family_display_name,
    fund_family_stem,
    is_entity,
    norm_text,
)

log = logging.getLogger(__name__)

# Linking keys that say nothing about who runs a fund. Compared with the
# entity stem or normalised signer name; extend from what the real data shows.
LINK_KEY_STOPLIST = frozenset({
    "GENERAL PARTNER", "MANAGING MEMBER", "MANAGEMENT", "CAPITAL", "PARTNERS",
    "ADVISORS", "N/A", "NA", "NONE", "SAME AS ISSUER", "FUND", "SPV", "SERIES",
})
# An entity stem made only of these tokens is skipped too ("FUND LLC" leaves
# FUND, "SERIES I" and "LIMITED LIABILITY COMPANY" say nothing).
GENERIC_STEM_TOKENS = frozenset(
    "FUND FUNDS SPV SPVS SERIES LLC LLP LIMITED LIABILITY COMPANY CO A B I II III IV V".split())
# Entity stems naming a service provider rather than a manager: Cayman
# corporate, custodial, fiduciary, trustee and director services firms and
# fund administrators sit as related persons on many unrelated managers'
# funds. In the first real run DEUTSCHE INTERNATIONAL CORPORATE SERVICES
# LIMITED alone chained Brevan Howard, Marathon, Marshall Wace and others
# into one manager. Matched with re.search against the stem.
SERVICE_PROVIDER_STEM_PATTERNS = (
    r"\b(CORPORATE|CUSTODIAL|FIDUCIARY|TRUSTEE|DIRECTOR|NOMINEE|SECRETARIAL)S? SERVICES?\b",
    r"\bFUND (SERVICES|ADMINISTRATION|ADMINISTRATORS?)\b",
)
_SERVICE_PROVIDER = re.compile("|".join(SERVICE_PROVIDER_STEM_PATTERNS))
# Keys shorter than this are skipped.
MIN_KEY_LENGTH = 4
# Phones must have at least this many digits.
MIN_PHONE_DIGITS = 7
# Keys held by more distinct CIKs than their cap belong to a platform or
# service provider: SPV platforms (BELLTOWER FUND GROUP, SYDECAR) as entity
# related persons, registered agents, administrators and law firms as phone
# and address. They stay in link_key with over_cap = 1, never link a manager
# and never count as a sighting for newness. Signer keys are not capped.
ENTITY_KEY_CAP = 200
SERVICE_PROVIDER_CAP = 50
KEY_CAPS = {"entity_stem": ENTITY_KEY_CAP, "entity_tokens": ENTITY_KEY_CAP,
            "phone": SERVICE_PROVIDER_CAP, "address": SERVICE_PROVIDER_CAP}
# Only these kinds create edges between CIKs in a manager cluster.
CLUSTER_KINDS = ("entity_stem", "entity_tokens")


def build_funds(conn):
    """One fund row per filer CIK; return the number written.

    Name, classification and exemptions come from the latest filing (by
    filing date, then accession); offering terms and year of incorporation
    from the first, which is the notice that launched the fund.
    """
    conn.execute("DELETE FROM fund")
    rows = conn.execute(
        """
        WITH ends AS MATERIALIZED (
            -- filing_date is ISO and accession fixed width, so the joined text sorts by both
            SELECT cik, count(*) AS n,
                   substr(min(filing_date || ' ' || accession), 12) AS first_accession,
                   substr(max(filing_date || ' ' || accession), 12) AS latest_accession
            FROM filing GROUP BY cik
        )
        SELECT e.cik, e.n, e.first_accession, f.filing_date AS first_filing_date,
               e.latest_accession, l.filing_date AS latest_filing_date,
               i.entity_name, i.state_or_country, i.state_or_country_desc,
               o.industry_group, o.fund_type, o.exemptions,
               fo.sale_yet_to_occur, fo.total_amount_sold_num, fo.n_investors,
               fo.min_investment_num, fo.total_offering_amount_num,
               fo.total_offering_amount_is_indefinite,
               fi.year_of_inc_choice, fi.year_of_inc_value
        FROM ends e
        JOIN filing f ON f.accession = e.first_accession
        JOIN filing l ON l.accession = e.latest_accession
        LEFT JOIN issuer i ON i.accession = e.latest_accession AND i.is_primary = 1
        LEFT JOIN offering o ON o.accession = e.latest_accession
        LEFT JOIN offering fo ON fo.accession = e.first_accession
        LEFT JOIN issuer fi ON fi.accession = e.first_accession AND fi.is_primary = 1
        """)
    out = []
    for r in rows:
        exemptions = classify.normalize_exemptions(json.loads(r["exemptions"] or "[]"))
        candidate, reason = classify.is_hedge_candidate(
            r["industry_group"], r["fund_type"], exemptions, r["entity_name"])
        out.append((
            r["cik"], r["entity_name"], fund_family_stem(r["entity_name"]), r["fund_type"],
            r["industry_group"], r["first_filing_date"], r["first_accession"],
            r["latest_filing_date"], r["latest_accession"], r["n"], json.dumps(exemptions),
            r["state_or_country"], r["state_or_country_desc"], int(candidate), reason,
            r["sale_yet_to_occur"], r["total_amount_sold_num"], r["n_investors"],
            r["min_investment_num"], r["total_offering_amount_num"],
            r["total_offering_amount_is_indefinite"], r["year_of_inc_choice"],
            r["year_of_inc_value"],
        ))
    conn.executemany(f"INSERT INTO fund VALUES ({', '.join('?' * 23)})", out)
    return len(out)


def _usable(key):
    return bool(key) and len(key) >= MIN_KEY_LENGTH and key not in LINK_KEY_STOPLIST


def _usable_stem(stem):
    return (_usable(stem) and not set(stem.split()) <= GENERIC_STEM_TOKENS
            and not _SERVICE_PROVIDER.search(stem))


def entity_tokens(stem):
    """The stem as a sorted token set, so "MANAGEMENT AQR CAPITAL" matches "AQR CAPITAL MANAGEMENT"."""
    return " ".join(sorted(set(stem.split())))


def build_link_keys(conn):
    """Fill link_key with every usable key per CIK and its earliest filing date.

    Keys held by more CIKs than their KEY_CAPS entry are kept with over_cap
    set. Entity and signer rows also carry a display name: the written form
    (normalize.entity_display_name, or the signer as filed) that the CIK's
    filings use most often for that key. Returns a dict with the rows
    written and, per capped kind, how many distinct keys are over the cap.
    """
    conn.execute("DELETE FROM link_key")
    first = {}  # (kind, key, cik) -> earliest filing date
    displays = defaultdict(lambda: defaultdict(int))  # (kind, key, cik) -> {display: rows}

    def add(kind, key, cik, day, display=None, n=0):
        k = (kind, key, cik)
        if k not in first or (day and (first[k] is None or day < first[k])):
            first[k] = day
        if display:
            displays[k][display] += n

    for r in conn.execute(
            """
            SELECT f.cik, rp.name_norm, rp.first, rp.middle, rp.last,
                   min(f.filing_date) AS day, count(*) AS n
            FROM related_person rp JOIN filing f USING (accession)
            WHERE rp.is_entity = 1
            GROUP BY f.cik, rp.name_norm, rp.first, rp.middle, rp.last
            """):
        stem = entity_stem(r["name_norm"])
        if _usable_stem(stem):
            display = entity_display_name(r["first"], r["middle"], r["last"])
            add("entity_stem", stem, r["cik"], r["day"], display, r["n"])
            add("entity_tokens", entity_tokens(stem), r["cik"], r["day"], display, r["n"])

    # Every name each CIK has filed under, to skip a fund signing as itself.
    own_names = defaultdict(set)
    for r in conn.execute("SELECT DISTINCT cik, entity_name FROM issuer WHERE is_primary = 1"):
        own_names[r["cik"]].add(norm_text(r["entity_name"]))
    for r in conn.execute(
            """
            SELECT f.cik, s.signer_norm, s.signer, min(f.filing_date) AS day, count(*) AS n
            FROM signature s JOIN filing f USING (accession)
            GROUP BY f.cik, s.signer_norm, s.signer
            """):
        signer = r["signer_norm"]
        if (not _usable(signer) or signer in own_names[r["cik"]]
                or is_entity(signer, None, signer)):
            continue
        add("signer", signer, r["cik"], r["day"], " ".join((r["signer"] or "").split()), r["n"])

    for r in conn.execute(
            """
            SELECT f.cik, i.phone, i.street1, i.zip, min(f.filing_date) AS day
            FROM issuer i JOIN filing f USING (accession)
            WHERE i.is_primary = 1
            GROUP BY f.cik, i.phone, i.street1, i.zip
            """):
        phone = re.sub(r"\D", "", r["phone"] or "")
        if len(phone) >= MIN_PHONE_DIGITS and _usable(phone):
            add("phone", phone, r["cik"], r["day"])
        address = address_key(r["street1"], r["zip"])
        if _usable(address):
            add("address", address, r["cik"], r["day"])

    ciks_per_key = defaultdict(int)
    for kind, key, _cik in first:
        if kind in KEY_CAPS:
            ciks_per_key[(kind, key)] += 1
    over = {k for k, n in ciks_per_key.items() if n > KEY_CAPS[k[0]]}
    over_cap = {kind: sum(1 for k in over if k[0] == kind) for kind in KEY_CAPS}
    for kind, n in over_cap.items():
        log.info("%d %s keys shared by more than %d CIKs flagged over_cap", n, kind, KEY_CAPS[kind])

    rows = sorted((kind, key, cik, day, int((kind, key) in over),
                   most_frequent(displays.get((kind, key, cik))))
                  for (kind, key, cik), day in first.items())
    conn.executemany("INSERT INTO link_key (kind, key, cik, first_date, over_cap, display) "
                     "VALUES (?, ?, ?, ?, ?, ?)", rows)
    return {"rows": len(rows), "over_cap": over_cap}


def most_frequent(counts):
    """The key of {text: count} with the highest count, ties to the longest, then the first
    in sort order; None for no counts."""
    if not counts:
        return None
    return min(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))[0]


def address_key(street1, zip_code):
    """normalize.address_key, but only when both parts are present.

    A zip alone or a street alone would link every filer in a zip code or on
    "123 Main Street" anywhere.
    """
    if not (street1 and street1.strip() and zip_code and zip_code.strip()):
        return None
    return normalize.address_key(street1, zip_code)


# Managers: connected components of CIKs that share a link key.

# Order of kinds when two edges fall on the same date.
KIND_ORDER = ("entity_stem", "entity_tokens", "signer", "phone", "address")


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        parent = self.parent
        root = x
        while parent.setdefault(root, root) != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _key_groups(conn):
    """Yield (kind, key, [(first_date, cik), ...] in date order) for each clustering key
    held by two or more CIKs: CLUSTER_KINDS only, over-cap keys left out."""
    current, members = None, []
    marks = ", ".join("?" * len(CLUSTER_KINDS))
    for r in conn.execute(f"SELECT kind, key, cik, first_date FROM link_key "
                          f"WHERE kind IN ({marks}) AND over_cap = 0 "
                          f"ORDER BY kind, key, first_date, cik", CLUSTER_KINDS):
        group = (r["kind"], r["key"])
        if group != current:
            if len(members) > 1:
                yield current[0], current[1], members
            current, members = group, []
        members.append((r["first_date"] or "", r["cik"]))
    if len(members) > 1:
        yield current[0], current[1], members


def build_managers(conn):
    """Fill manager and fund_manager from link_key; return the number of managers.

    Managers are the connected components of CIKs sharing an entity_stem or
    entity_tokens key under the cap; signer, phone and address keys never
    link (one SPV platform otherwise chains most of EDGAR into one manager,
    see the decisions log). The CIKs sharing a key are linked in date order: the CIK that held the
    key first is joined to each later one on the date that one first filed
    with it. A component's key_kind is the kind of its earliest edge, and
    each CIK's evidence is its own earliest edge as kind:key.
    """
    conn.execute("DELETE FROM fund_manager")
    conn.execute("DELETE FROM manager")
    funds = {r["cik"]: (r["first_filing_date"] or "", r["name"]) for r in conn.execute(
        "SELECT cik, first_filing_date, name FROM fund")}
    uf = UnionFind()
    evidence = {}  # cik -> (date, kind rank, "kind:key") of its earliest edge
    for kind, key, members in _key_groups(conn):
        rank = KIND_ORDER.index(kind)
        anchor = members[0][1]
        for day, cik in members[1:]:
            if anchor not in funds or cik not in funds:
                continue
            uf.union(anchor, cik)
            edge = (day, rank, f"{kind}:{key}")
            for c in (anchor, cik):
                if c not in evidence or edge < evidence[c]:
                    evidence[c] = edge

    members_of = defaultdict(list)
    for cik in funds:
        members_of[uf.find(cik)].append(cik)
    names = _name_counts(conn, uf)
    managers, links = [], []
    for root, ciks in members_of.items():
        ciks.sort(key=lambda c: (funds[c][0], c))
        earliest = ciks[0]
        manager_id = f"m{earliest}"
        # The component's first edge is the earliest evidence of any member.
        first_edge = min((evidence[c] for c in ciks if c in evidence), default=None)
        key_kind = first_edge[2].split(":", 1)[0] if first_edge else None
        managers.append((manager_id, _display_name(names.get(root), funds[earliest][1]),
                         key_kind, funds[earliest][0] or None, len(ciks)))
        links.extend((c, manager_id, evidence[c][2] if c in evidence else None) for c in ciks)
    conn.executemany("INSERT INTO manager VALUES (?, ?, ?, ?, ?)", sorted(managers))
    conn.executemany("INSERT INTO fund_manager VALUES (?, ?, ?)", sorted(links))
    return len(managers)


# Where a manager's display name comes from, in order of preference: entity
# related persons under the cap, then over the cap (an AQR series fund whose
# only entity is AQR itself), then signers.
NAME_SOURCES = ("entity", "entity_over_cap", "signer")


def _name_counts(conn, uf):
    """root -> {source: {display name: number of member CIKs}} from entity_stem and signer keys."""
    ciks = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    for r in conn.execute("SELECT kind, cik, over_cap, display FROM link_key "
                          "WHERE kind IN ('entity_stem', 'signer') AND display IS NOT NULL"):
        source = ("signer" if r["kind"] == "signer"
                  else "entity_over_cap" if r["over_cap"] else "entity")
        ciks[uf.find(r["cik"])][source][r["display"]].add(r["cik"])
    return {root: {source: {name: len(c) for name, c in names.items()}
                   for source, names in sources.items()}
            for root, sources in ciks.items()}


def _display_name(counts, earliest_fund_name):
    """The most frequent entity name, else the most frequent signer, else the earliest
    fund's name without its family tokens."""
    for source in NAME_SOURCES:
        names = (counts or {}).get(source)
        if names:
            return most_frequent(names)
    return family_display_name(earliest_fund_name)


# Launches: a manager's pooled funds first filed within LAUNCH_WINDOW_DAYS.

LAUNCH_WINDOW_DAYS = 30


def _windows(funds):
    """Split funds, sorted by first filing date, into launch windows."""
    out = []
    for fund in funds:
        if out and _days(out[-1][0]["first_filing_date"], fund["first_filing_date"]) <= LAUNCH_WINDOW_DAYS:
            out[-1].append(fund)
        else:
            out.append([fund])
    return out


def _days(start, end):
    if not start or not end:
        return 0 if start == end else LAUNCH_WINDOW_DAYS + 1
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def build_launches(conn, score=None):
    """Fill launch from fund and fund_manager; return the number of launches.

    Every fund whose latest industry group is Pooled Investment Fund belongs
    to exactly one launch. A manager's funds are walked in first-filing
    order, and a fund more than LAUNCH_WINDOW_DAYS after the current
    window's start opens a new launch. Funds of single-fund managers are
    grouped the same way by fund family stem, which folds master, feeder and
    offshore vehicles that name no common related person. score(launch,
    funds) returns (score, reasons) and is applied when given.
    """
    conn.execute("DELETE FROM launch")
    first_d = {}  # cik -> (filing date, accession) of its earliest D filing
    for r in conn.execute("SELECT cik, min(filing_date || ' ' || accession) FROM filing "
                          "WHERE submission_type = 'D' GROUP BY cik"):
        first_d[r[0]] = (r[1][:10], r[1][11:])
    funds = [dict(r) for r in conn.execute(
        """
        SELECT f.*, fm.manager_id, m.n_funds
        FROM fund f JOIN fund_manager fm USING (cik) JOIN manager m USING (manager_id)
        WHERE f.industry_group = 'Pooled Investment Fund'
        ORDER BY f.first_filing_date, f.cik
        """)]
    groups = defaultdict(list)
    for fund in funds:
        if fund["n_funds"] == 1:
            groups[("family", fund["family_stem"] or fund["cik"])].append(fund)
        else:
            groups[("manager", fund["manager_id"])].append(fund)

    rows = []
    for _group, members in sorted(groups.items()):
        for window in _windows(members):
            # launch_id is the earliest D filing of any fund in the window; a
            # fund with no D in the database falls back to its first filing.
            anchors = [(first_d.get(f["cik"]) or (f["first_filing_date"] or "", f["first_accession"]), f)
                       for f in window]
            (_day, launch_id), anchor = min(anchors, key=lambda a: a[0])
            types = {f["fund_type"] for f in window}
            launch = {
                "launch_id": launch_id,
                "manager_id": anchor["manager_id"],
                "display_name": anchor["name"],
                "anchor_cik": anchor["cik"],
                "fund_type": "Hedge Fund" if "Hedge Fund" in types else anchor["fund_type"],
                "window_start": window[0]["first_filing_date"],
                "window_end": window[-1]["first_filing_date"],
                "fund_ciks": json.dumps([f["cik"] for f in window]),
                "newness_score": None,
                "newness_reasons": "[]",
            }
            if score is not None:
                points, reasons = score(launch, window)
                launch["newness_score"], launch["newness_reasons"] = points, json.dumps(reasons)
            rows.append(launch)
    if rows:
        columns = list(rows[0])
        conn.executemany(
            f"INSERT INTO launch ({', '.join(columns)}) "
            f"VALUES ({', '.join(':' + c for c in columns)})", rows)
    return len(rows)


def launch_scorer(conn):
    """A score(launch, funds) for build_launches, with the facts newness needs.

    Reads, once per run: for each pooled fund, the earliest date any of its
    keys under the cap was seen on any filing; its over-cap keys; the
    signer titles on its first filing; and each manager's fund first-filing
    dates, to count funds filed before a launch.
    """
    from bisect import bisect_left

    from fundtracker import newness

    pooled = "SELECT cik FROM fund WHERE industry_group = 'Pooled Investment Fund'"
    key_first_seen = dict(conn.execute(
        f"""
        WITH seen AS (
            SELECT kind, key, min(first_date) AS first_seen FROM link_key
            WHERE over_cap = 0 GROUP BY kind, key
        )
        SELECT lk.cik, min(seen.first_seen) FROM link_key lk JOIN seen USING (kind, key)
        WHERE lk.over_cap = 0 AND lk.cik IN ({pooled}) GROUP BY lk.cik
        """).fetchall())
    platforms = defaultdict(set)
    for r in conn.execute(f"SELECT cik, kind, key FROM link_key WHERE over_cap = 1 "
                          f"AND kind != 'entity_tokens' AND cik IN ({pooled})"):
        platforms[r["cik"]].add(r["key"] if r["kind"] == "entity_stem" else f"{r['kind']} {r['key']}")
    titles = defaultdict(list)
    for r in conn.execute(f"SELECT f.cik, s.title FROM fund f JOIN signature s "
                          f"ON s.accession = f.first_accession WHERE f.cik IN ({pooled})"):
        titles[r["cik"]].append(r["title"])
    manager_dates = defaultdict(list)
    for r in conn.execute("SELECT fm.manager_id, f.first_filing_date FROM fund_manager fm "
                          "JOIN fund f USING (cik) ORDER BY f.first_filing_date"):
        manager_dates[r[0]].append(r[1] or "")

    def score(launch, funds):
        start = launch["window_start"]
        seen = [key_first_seen[f["cik"]] for f in funds if key_first_seen.get(f["cik"])]
        facts = {
            "window_start": start,
            "key_seen_before": bool(seen) and min(seen) < start,
            "prior_funds": bisect_left(manager_dates[launch["manager_id"]], start),
            "signer_titles": [t for f in funds for t in titles.get(f["cik"], ())],
            "platform_keys": sorted({k for f in funds for k in platforms.get(f["cik"], ())}),
            "funds": [dict(f, exemptions=json.loads(f["exemptions"])) for f in funds],
        }
        return newness.score_launch(facts)

    return score


DERIVE_SOURCE = "derive"


def run(conn, today=None):
    """Rebuild every derived table in one transaction and log it; return the counts.

    The ingest_log row (source derive, key the run date) is replaced on every
    run, since derive always rebuilds from scratch. review is not touched.
    """
    started = _now()
    conn.execute("PRAGMA mmap_size = 4294967296")  # the joins read the raw tables at random
    conn.execute("PRAGMA cache_size = -131072")
    counts = {}
    with conn:
        counts["fund"] = build_funds(conn)
        keys = build_link_keys(conn)
        counts["link_key"], counts["over_cap"] = keys["rows"], keys["over_cap"]
        counts["manager"] = build_managers(conn)
        counts["fund_manager"] = counts["fund"]
        counts["launch"] = build_launches(conn, score=launch_scorer(conn))
        repo.log_ingest(conn, DERIVE_SOURCE, (today or date.today()).isoformat(), "ok",
                        n_rows=counts["launch"], started_at=started, finished_at=_now())
    return counts


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
