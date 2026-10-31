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
from datetime import date, datetime, timedelta, timezone

from fundtracker import classify, normalize
from fundtracker.db import repo
from fundtracker.normalize import (
    entity_display_name,
    entity_stem,
    family_display_name,
    fund_family_stem,
    is_entity,
    looks_like_entity,
    norm_text,
    person_display_name,
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

    def fund_rows():
        for r in rows:
            exemptions = classify.normalize_exemptions(json.loads(r["exemptions"] or "[]"))
            candidate, reason = classify.is_hedge_candidate(
                r["industry_group"], r["fund_type"], exemptions, r["entity_name"])
            yield (
                r["cik"], r["entity_name"], fund_family_stem(r["entity_name"]), r["fund_type"],
                r["industry_group"], r["first_filing_date"], r["first_accession"],
                r["latest_filing_date"], r["latest_accession"], r["n"], json.dumps(exemptions),
                r["state_or_country"], r["state_or_country_desc"], int(candidate), reason,
                r["sale_yet_to_occur"], r["total_amount_sold_num"], r["n_investors"],
                r["min_investment_num"], r["total_offering_amount_num"],
                r["total_offering_amount_is_indefinite"], r["year_of_inc_choice"],
                r["year_of_inc_value"],
            )

    # Streamed: a list of every fund row held about 300 MB on the real data.
    return conn.executemany(f"INSERT INTO fund VALUES ({', '.join('?' * 23)})", fund_rows()).rowcount


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
    set. Entity and signer rows also carry a display name: of the written
    forms (normalize.entity_display_name of one first, middle and last, or
    the signer as filed) the CIK's filings give that key, the one filed most
    often, ties to the longest. Only that one form is kept per row while
    scanning: a count table per row held every form of 1.7 million keys and
    ran the real run out of memory (Session 5). Returns a dict with the rows
    written and, per capped kind, how many distinct keys are over the cap.
    """
    conn.execute("DELETE FROM link_key")
    conn.execute("DROP TABLE IF EXISTS temp.key_rows")
    conn.execute("CREATE TEMP TABLE key_rows (kind TEXT, key TEXT, cik TEXT, day TEXT, "
                 "display TEXT, n INTEGER)")
    insert = "INSERT INTO temp.key_rows VALUES (?, ?, ?, ?, ?, ?)"
    conn.executemany(insert, _entity_key_rows(conn))
    conn.executemany(insert, _signer_key_rows(conn))
    conn.executemany(insert, _issuer_key_rows(conn))

    # One row per (kind, key, cik): its earliest day, and its best written form
    # (most rows filed, ties to the longest, then the first in sort order).
    caps = " ".join(f"WHEN '{kind}' THEN n_ciks > {cap}" for kind, cap in KEY_CAPS.items())
    conn.execute(
        f"""
        INSERT INTO link_key (kind, key, cik, first_date, over_cap, display)
        WITH ranked AS (
            SELECT kind, key, cik, display,
                   min(day) OVER cik_key AS first_date,
                   row_number() OVER (PARTITION BY kind, key, cik
                                      ORDER BY display IS NULL, n DESC, length(display) DESC,
                                               display) AS rank
            FROM key_rows WINDOW cik_key AS (PARTITION BY kind, key, cik)
        ), one AS (
            SELECT kind, key, cik, first_date, display,
                   count(*) OVER (PARTITION BY kind, key) AS n_ciks
            FROM ranked WHERE rank = 1
        )
        SELECT kind, key, cik, first_date, coalesce(CASE kind {caps} END, 0), display
        FROM one ORDER BY kind, key, cik
        """)
    conn.execute("DROP TABLE temp.key_rows")
    over_cap = dict.fromkeys(KEY_CAPS, 0)
    for r in conn.execute("SELECT kind, count(DISTINCT key) FROM link_key WHERE over_cap = 1 "
                          "GROUP BY kind"):
        over_cap[r[0]] = r[1]
    for kind, n in over_cap.items():
        log.info("%d %s keys shared by more than %d CIKs flagged over_cap", n, kind, KEY_CAPS[kind])
    return {"rows": conn.execute("SELECT count(*) FROM link_key").fetchone()[0], "over_cap": over_cap}


def _entity_key_rows(conn):
    """(kind, key, cik, day, display, rows) for entity_stem and entity_tokens keys."""
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
            yield "entity_stem", stem, r["cik"], r["day"], display, r["n"]
            yield "entity_tokens", entity_tokens(stem), r["cik"], r["day"], display, r["n"]


def _signer_key_rows(conn):
    """Signer keys, skipping a fund signing as itself. Signers and the CIK's own names are
    both read in CIK order and merged, so no name table is held."""
    names = conn.execute("SELECT DISTINCT cik, entity_name FROM issuer WHERE is_primary = 1 "
                         "ORDER BY cik")
    pending = names.fetchone()
    own_cik, own = None, set()
    for r in conn.execute(
            """
            SELECT f.cik, s.signer_norm, s.signer, min(f.filing_date) AS day, count(*) AS n
            FROM signature s JOIN filing f USING (accession)
            GROUP BY f.cik, s.signer_norm, s.signer ORDER BY f.cik
            """):
        if r["cik"] != own_cik:
            own_cik, own = r["cik"], set()
            while pending is not None and pending[0] < own_cik:
                pending = names.fetchone()
            while pending is not None and pending[0] == own_cik:
                own.add(norm_text(pending[1]))
                pending = names.fetchone()
        signer = r["signer_norm"]
        if not _usable(signer) or signer in own or is_entity(signer, None, signer):
            continue
        yield "signer", signer, r["cik"], r["day"], " ".join((r["signer"] or "").split()), r["n"]


def _issuer_key_rows(conn):
    """Phone and address keys from each CIK's primary issuer rows."""
    for r in conn.execute(
            """
            SELECT f.cik, i.phone, i.street1, i.zip, min(f.filing_date) AS day
            FROM issuer i JOIN filing f USING (accession)
            WHERE i.is_primary = 1
            GROUP BY f.cik, i.phone, i.street1, i.zip
            """):
        phone = re.sub(r"\D", "", r["phone"] or "")
        if len(phone) >= MIN_PHONE_DIGITS and _usable(phone):
            yield "phone", phone, r["cik"], r["day"], None, 0
        address = address_key(r["street1"], r["zip"])
        if _usable(address):
            yield "address", address, r["cik"], r["day"], None, 0


def _display_rank(display, count):
    """Sort key that puts the most frequent name first, ties to the longest, then the first
    in sort order. The name is the last element."""
    return (-count, -len(display), display)


def most_frequent(counts):
    """The key of {text: count} with the highest count, ties to the longest, then the first
    in sort order; None for no counts."""
    if not counts:
        return None
    return min(_display_rank(text, n) for text, n in counts.items())[2]


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
    managers, links = [], []
    for root, ciks in members_of.items():
        ciks.sort(key=lambda c: (funds[c][0], c))
        earliest = ciks[0]
        manager_id = f"m{earliest}"
        # The component's first edge is the earliest evidence of any member.
        first_edge = min((evidence[c] for c in ciks if c in evidence), default=None)
        key_kind = first_edge[2].split(":", 1)[0] if first_edge else None
        managers.append((manager_id, family_display_name(funds[earliest][1]),
                         key_kind, funds[earliest][0] or None, len(ciks)))
        links.extend((c, manager_id, evidence[c][2] if c in evidence else None) for c in ciks)
    conn.executemany("INSERT INTO manager (manager_id, display_name, key_kind, first_seen_date, n_funds) "
                     "VALUES (?, ?, ?, ?, ?)", sorted(managers))
    conn.executemany("INSERT INTO fund_manager VALUES (?, ?, ?)", sorted(links))
    _set_display_names(conn)
    return len(managers)


# Where a manager's display name comes from, in order of preference: entity
# related persons under the cap, then over the cap (an AQR series fund whose
# only entity is AQR itself), then signers.
NAME_SOURCES = ("entity", "entity_over_cap", "signer")


def _set_display_names(conn):
    """Set each manager's display_name from link_key: the display held by the most member
    CIKs from the first source in NAME_SOURCES that has one, ties to the longest. Managers
    with none keep the earliest fund's family display name build_managers wrote.

    SQLite counts the CIKs per manager and name and ranks them; Python only passes
    the winners to the update.
    """
    names = conn.execute(
        """
        WITH counted AS (
            SELECT fm.manager_id,
                   CASE WHEN lk.kind = 'signer' THEN 2 WHEN lk.over_cap THEN 1 ELSE 0 END AS source,
                   lk.display, count(DISTINCT lk.cik) AS n
            FROM link_key lk JOIN fund_manager fm USING (cik)
            WHERE lk.kind IN ('entity_stem', 'signer') AND lk.display IS NOT NULL
            GROUP BY fm.manager_id, source, lk.display
        ), ranked AS (
            SELECT manager_id, display,
                   row_number() OVER (PARTITION BY manager_id
                                      ORDER BY source, n DESC, length(display) DESC, display) AS rank
            FROM counted
        )
        SELECT display, manager_id FROM ranked WHERE rank = 1
        """).fetchall()
    conn.executemany("UPDATE manager SET display_name = ? WHERE manager_id = ?", names)


# Manager traction (PLAN.md 7.6): what the member funds' latest filings say.

# The most individual related persons kept per manager.
MAX_PEOPLE = 8
# EDGAR state_or_country codes that are United States states and territories.
US_STATE_CODES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR VI GU AS MP".split())


def build_manager_stats(conn):
    """Fill the traction columns of manager from its funds' latest filings.

    Run after build_managers and build_link_keys. SQLite does the sums; Python
    only splits fund types, names the country and walks the related persons
    one manager at a time, so memory stays flat.
    """
    over_cap = {r[0] for r in conn.execute(
        """
        SELECT DISTINCT fm.manager_id FROM link_key lk JOIN fund_manager fm USING (cik)
        WHERE lk.over_cap = 1 AND lk.kind IN ('entity_stem', 'entity_tokens')
        """)}

    def rows():
        for r in conn.execute(
                """
                WITH stats AS (
                    SELECT fm.manager_id, sum(f.n_filings) AS n_filings,
                           max(f.latest_filing_date) AS latest,
                           sum(coalesce(lo.total_amount_sold_num, 0)) AS sold,
                           sum(coalesce(lo.n_investors, 0)) AS investors,
                           max(f.n_filings > 1 AND lf.submission_type = 'D/A') AS amended,
                           group_concat(DISTINCT f.fund_type) AS types
                    FROM fund_manager fm JOIN fund f USING (cik)
                    LEFT JOIN offering lo ON lo.accession = f.latest_accession
                    LEFT JOIN filing lf ON lf.accession = f.latest_accession
                    GROUP BY fm.manager_id
                )
                SELECT s.*, i.city, i.state_or_country, i.state_or_country_desc
                FROM stats s JOIN fund e ON e.cik = substr(s.manager_id, 2)
                LEFT JOIN issuer i ON i.accession = e.latest_accession AND i.is_primary = 1
                """):
            manager_id = r["manager_id"]
            types = sorted(set((r["types"] or "").split(",")) - {""})
            yield (r["n_filings"], r["latest"], r["sold"], r["investors"], int(bool(r["amended"])),
                   int(manager_id in over_cap), json.dumps(types),
                   *_place(r["city"], r["state_or_country"], r["state_or_country_desc"]),
                   manager_id[1:], manager_id)

    conn.executemany(
        """
        UPDATE manager SET n_filings = ?, latest_filing_date = ?, total_sold = ?,
               total_investors = ?, has_amendment = ?, is_over_cap = ?, fund_types = ?,
               city = ?, state = ?, country = ?, edgar_cik = ?
        WHERE manager_id = ?
        """, rows())
    conn.executemany("UPDATE manager SET people = ? WHERE manager_id = ?", _people(conn))
    conn.executemany("UPDATE manager SET prior_firms = ? WHERE manager_id = ?", _prior_firms(conn))


# A manager with this many funds is a large firm for the prior_firm label.
PRIOR_FIRM_FUNDS = 10
# The most prior firms named per manager, largest first.
MAX_PRIOR_FIRMS = 3


def _prior_firms(conn):
    """Yield (prior firms JSON, manager_id) for managers whose signer, or an individual on a
    member fund's latest filing, appears on an earlier filing of a different manager with
    PRIOR_FIRM_FUNDS or more funds: up to MAX_PRIOR_FIRMS display names, largest first.

    Names are compared as name_norm and signer_norm. SQLite matches them through two
    temporary tables, so Python only holds the result.
    """
    conn.execute("DROP TABLE IF EXISTS temp.big_names")
    conn.execute("DROP TABLE IF EXISTS temp.manager_names")
    conn.execute(
        """
        CREATE TEMP TABLE big_names AS
        SELECT name, manager_id, min(day) AS day FROM (
            SELECT lk.key AS name, fm.manager_id, lk.first_date AS day
            FROM link_key lk JOIN fund_manager fm USING (cik) JOIN manager m USING (manager_id)
            WHERE lk.kind = 'signer' AND m.n_funds >= :big
            UNION ALL
            SELECT rp.name_norm, fm.manager_id, f.filing_date
            FROM manager m JOIN fund_manager fm USING (manager_id)
            JOIN filing f ON f.cik = fm.cik JOIN related_person rp USING (accession)
            WHERE m.n_funds >= :big AND rp.is_entity = 0 AND rp.name_norm IS NOT NULL
        ) GROUP BY name, manager_id
        """, {"big": PRIOR_FIRM_FUNDS})
    conn.execute("CREATE INDEX temp.big_names_name ON big_names(name)")
    conn.execute(
        """
        CREATE TEMP TABLE manager_names AS
        SELECT DISTINCT fm.manager_id, lk.key AS name
        FROM link_key lk JOIN fund_manager fm USING (cik) WHERE lk.kind = 'signer'
        UNION
        SELECT fm.manager_id, rp.name_norm
        FROM fund_manager fm JOIN fund f USING (cik)
        JOIN related_person rp ON rp.accession = f.latest_accession
        WHERE rp.is_entity = 0 AND rp.name_norm IS NOT NULL
        """)
    current, firms = None, []
    for r in conn.execute(
            """
            SELECT n.manager_id, b.manager_id AS firm, big.display_name, big.n_funds
            FROM manager_names n JOIN manager m ON m.manager_id = n.manager_id
            JOIN big_names b ON b.name = n.name
            JOIN manager big ON big.manager_id = b.manager_id
            WHERE b.manager_id != n.manager_id AND b.day < m.first_seen_date
            GROUP BY n.manager_id, b.manager_id
            ORDER BY n.manager_id, big.n_funds DESC, big.display_name
            """):
        if r["manager_id"] != current:
            if firms:
                yield json.dumps(firms), current
            current, firms = r["manager_id"], []
        if len(firms) < MAX_PRIOR_FIRMS:
            firms.append(r["display_name"])
    if firms:
        yield json.dumps(firms), current
    conn.execute("DROP TABLE temp.big_names")
    conn.execute("DROP TABLE temp.manager_names")


def _place(city, code, desc):
    """(city, state, country): state is the code for US issuers, country a readable name.
    A city filed all in capitals is title-cased; any other is kept as filed."""
    if city and city.isupper():
        city = city.title()
    if code in US_STATE_CODES:
        return city, code, "United States"
    country = desc or code
    return city, None, country.title() if country else None


def _people(conn):
    """Yield (people JSON, manager_id) for every manager with an individual related person
    on a member fund's latest filing: up to MAX_PEOPLE, deduped by name_norm with roles
    merged, funds in first-filing order and persons in filing order."""
    current, people = None, {}
    for r in conn.execute(
            """
            SELECT fm.manager_id, f.cik, f.latest_accession, f.latest_filing_date,
                   rp.first, rp.middle, rp.last, rp.name_norm, rp.relationships
            FROM fund_manager fm JOIN fund f USING (cik)
            JOIN related_person rp ON rp.accession = f.latest_accession
            WHERE rp.is_entity = 0
            ORDER BY fm.manager_id, f.first_filing_date, f.cik, rp.seq
            """):
        if r["manager_id"] != current:
            if people:
                yield json.dumps(list(people.values())), current
            current, people = r["manager_id"], {}
        name = person_display_name(r["first"], r["middle"], r["last"])
        if not name or not r["name_norm"] or looks_like_entity(name):
            continue
        roles = json.loads(r["relationships"] or "[]")
        if r["name_norm"] in people:
            merged = people[r["name_norm"]]["roles"]
            merged.extend(role for role in roles if role not in merged)
        elif len(people) < MAX_PEOPLE:
            people[r["name_norm"]] = {"name": name, "roles": roles, "cik": r["cik"],
                                      "accession": r["latest_accession"],
                                      "filing_date": r["latest_filing_date"]}
    if people:
        yield json.dumps(list(people.values())), current


# Form ADV advisers (PLAN.md WP7): which registered or exempt adviser runs a manager.

# Phone and address keys held by more advisers than this are shared office
# buildings and service providers, and are ignored.
ADVISER_KEY_CAP = 20
# Removed from adviser and manager names before comparing, on top of
# normalize.ENTITY_SUFFIX_TOKENS: Form ADV names carry these more often.
ADV_SUFFIX_TOKENS = normalize.ENTITY_SUFFIX_TOKENS | frozenset(
    "LLP PLLC LC CO CORP CORPORATION COMPANY LIMITED".split())
# Phones compare on their last this many digits, so +1 and 001 prefixes do not matter.
ADV_PHONE_DIGITS = 10
# The hedge-fund tie-break: phone and address together point to at least
# TIEBREAK_MIN and at most TIEBREAK_MAX advisers, exactly one of which has hedge funds.
TIEBREAK_MIN, TIEBREAK_MAX = 2, 3
HIGH, MEDIUM, NONE = "high", "medium", "none"
LINKED = (HIGH, MEDIUM)


def adviser_stem(name):
    """A firm name for adviser matching: normalised, ADV_SUFFIX_TOKENS removed, None when
    the rest is too short or generic to identify anyone (derive._usable_stem)."""
    stem = " ".join(t for t in norm_text(name).split() if t not in ADV_SUFFIX_TOKENS)
    return stem if _usable_stem(stem) else None


def adviser_tokens(name):
    """The words of adviser_stem(name) that can show a manager and an adviser are the same
    firm: not in ADV_SUFFIX_TOKENS or GENERIC_STEM_TOKENS, at least MIN_KEY_LENGTH long."""
    stem = adviser_stem(name) or ""
    return frozenset(t for t in stem.split() if len(t) >= MIN_KEY_LENGTH
                     and t not in ADV_SUFFIX_TOKENS and t not in GENERIC_STEM_TOKENS)


def _adviser_keys(conn):
    """(kind, key, crd) for every adviser: name stems of both names, the phone's last
    ADV_PHONE_DIGITS digits, and the address as address_key gives it, also with a
    ZIP+4 cut to five digits. Phone and address keys over ADVISER_KEY_CAP are left out."""
    keys = set()
    for r in conn.execute("SELECT crd, name, legal_name, phone, street1, postal_code FROM adviser"):
        for name in (r["name"], r["legal_name"]):
            stem = adviser_stem(name)
            if stem:
                keys.add(("name", stem, r["crd"]))
        phone = re.sub(r"\D", "", r["phone"] or "")
        if len(phone) >= MIN_PHONE_DIGITS:
            keys.add(("phone", phone[-ADV_PHONE_DIGITS:], r["crd"]))
        postal = (r["postal_code"] or "").strip()
        for code in {postal, postal[:5] if re.fullmatch(r"\d{5}-\d{4}", postal) else postal}:
            address = address_key(r["street1"], code)
            if address:
                keys.add(("address", address, r["crd"]))
    holders = defaultdict(int)
    for kind, key, _crd in keys:
        holders[kind, key] += 1
    return sorted(k for k in keys if k[0] == "name" or holders[k[0], k[1]] <= ADVISER_KEY_CAP)


def build_manager_advisers(conn):
    """Fill manager_adviser: each manager's adviser and how sure the link is.

    Run after build_manager_stats. A manager's keys are its display name and
    the entity stems of its funds' related persons (compared through
    adviser_stem), and its funds' phone and address keys under the Form D
    cap (phones on their last ADV_PHONE_DIGITS digits). Against the adviser
    keys from _adviser_keys, the tiers are:

      high    one adviser by name (several narrowed to one by phone or
              address), or else one adviser by phone
      medium  one adviser by address, or else two or three by phone and
              address of which exactly one has hedge funds
      none    anything else; the candidates are kept in evidence

    A link by address alone, or by phone alone to an adviser with no hedge
    funds, stands only when the manager and the adviser share a name token
    (adviser_tokens); otherwise it is none.

    Managers with no candidate get no row. Returns {tier: managers}.
    """
    conn.execute("DELETE FROM manager_adviser")
    tiers = dict.fromkeys((HIGH, MEDIUM, NONE), 0)

    def rows():
        for row in adviser_links(conn):
            tiers[row[2]] += 1
            yield row

    conn.executemany("INSERT INTO manager_adviser VALUES (?, ?, ?, ?, ?)", rows())
    conn.execute(
        f"""
        UPDATE manager SET (adviser_crd, adviser_confidence, adviser_kind, website, employees, raum,
                            adv_hedge_funds, adv_private_assets, adv_filing_date) = (
            SELECT a.crd, ma.confidence, a.kind, a.website, a.employees, a.raum, a.n_hedge_funds,
                   a.private_fund_assets, a.latest_filing_date
            FROM manager_adviser ma JOIN adviser a USING (crd)
            WHERE ma.manager_id = manager.manager_id)
        WHERE manager_id IN (SELECT manager_id FROM manager_adviser
                             WHERE confidence IN ({', '.join(f"'{t}'" for t in LINKED)}))
        """)
    log.info("manager advisers: %(high)d high, %(medium)d medium, %(none)d candidates only", tiers)
    return tiers


def adviser_links(conn):
    """Yield the manager_adviser rows (manager_id, crd, confidence, match_kinds, evidence)
    in manager order, writing only temporary tables, so a read-only connection can run it."""
    for table in ("adv_key", "manager_key", "adv_hit"):
        conn.execute(f"DROP TABLE IF EXISTS temp.{table}")
    conn.execute("CREATE TEMP TABLE adv_key (kind TEXT, key TEXT, crd TEXT)")
    conn.executemany("INSERT INTO temp.adv_key VALUES (?, ?, ?)", _adviser_keys(conn))
    conn.execute("CREATE INDEX temp.adv_key_kind_key ON adv_key(kind, key)")
    adviser_names = {r[0] for r in conn.execute("SELECT key FROM temp.adv_key WHERE kind = 'name'")}
    # Each manager's keys, built first so the join below probes the adviser keys by
    # index: comparing through a function inside the join scanned every pair.
    conn.execute("CREATE TEMP TABLE manager_key (manager_id TEXT, kind TEXT, key TEXT)")
    names = conn.execute(
        """
        SELECT manager_id, display_name FROM manager
        UNION
        SELECT fm.manager_id, lk.key FROM link_key lk JOIN fund_manager fm USING (cik)
        WHERE lk.kind = 'entity_stem' AND lk.over_cap = 0
        """)
    conn.executemany("INSERT INTO temp.manager_key VALUES (?, 'name', ?)",
                     ((m, stem) for m, name in names if (stem := adviser_stem(name)) in adviser_names))
    conn.execute(
        f"""
        INSERT INTO temp.manager_key
        SELECT fm.manager_id, lk.kind,
               CASE lk.kind WHEN 'phone' THEN substr(lk.key, -{ADV_PHONE_DIGITS}) ELSE lk.key END
        FROM link_key lk JOIN fund_manager fm USING (cik)
        WHERE lk.kind IN ('phone', 'address') AND lk.over_cap = 0
        """)
    hedge = {r[0] for r in conn.execute("SELECT crd FROM adviser WHERE any_hedge_funds = 1")}
    conn.execute(
        """
        CREATE TEMP TABLE adv_hit AS
        SELECT DISTINCT mk.manager_id, mk.kind, mk.key, ak.crd
        FROM temp.manager_key mk CROSS JOIN temp.adv_key ak ON ak.kind = mk.kind AND ak.key = mk.key
        """)
    adv_tokens = _adviser_token_map(conn)
    manager_tokens = _manager_token_map(conn)
    hits = conn.execute("SELECT * FROM temp.adv_hit ORDER BY manager_id, kind, key, crd")
    for manager_id, group in _groups(hits):
        yield _adviser_link(manager_id, [(r["kind"], r["key"], r["crd"]) for r in group], hedge,
                            manager_tokens.get(manager_id, frozenset()), adv_tokens)
    for table in ("adv_key", "manager_key", "adv_hit"):
        conn.execute(f"DROP TABLE temp.{table}")


def _adviser_token_map(conn):
    """{crd: adviser_tokens of the adviser's name and legal name}."""
    return {r["crd"]: adviser_tokens(r["name"]) | adviser_tokens(r["legal_name"])
            for r in conn.execute("SELECT crd, name, legal_name FROM adviser")}


def _manager_token_map(conn):
    """{manager_id: adviser_tokens of its display name and its funds' entity stems under
    the Form D cap}, for the managers in temp.adv_hit."""
    tokens = defaultdict(frozenset)
    for r in conn.execute(
            """
            SELECT manager_id, display_name FROM manager
            WHERE manager_id IN (SELECT manager_id FROM temp.adv_hit)
            UNION
            SELECT fm.manager_id, lk.key FROM link_key lk JOIN fund_manager fm USING (cik)
            WHERE lk.kind = 'entity_stem' AND lk.over_cap = 0
              AND fm.manager_id IN (SELECT manager_id FROM temp.adv_hit)
            """):
        tokens[r[0]] |= adviser_tokens(r[1])
    return tokens


def _adviser_link(manager_id, found, hedge, tokens, adv_tokens):
    """The manager_adviser row for one manager's (kind, key, crd) hits, given the
    manager's name tokens and {crd: name tokens} for the advisers."""
    by_kind = defaultdict(set)
    for kind, _key, crd in found:
        by_kind[kind].add(crd)
    names, phones, addresses = by_kind["name"], by_kind["phone"], by_kind["address"]
    crd, confidence, extra = None, NONE, []
    narrowed = names & (phones | addresses)
    shared = phones | addresses
    hedgers = sorted(shared & hedge)
    if len(names) == 1:
        crd, confidence = next(iter(names)), HIGH
    elif len(names) > 1 and len(narrowed) == 1:
        crd, confidence = next(iter(narrowed)), HIGH
    elif len(phones) == 1:
        crd, confidence = next(iter(phones)), HIGH
        if crd not in hedge:
            crd, confidence, extra = _name_token_check(crd, confidence, tokens, adv_tokens)
    elif len(addresses) == 1:
        crd, confidence, extra = _name_token_check(next(iter(addresses)), MEDIUM, tokens, adv_tokens)
    elif TIEBREAK_MIN <= len(shared) <= TIEBREAK_MAX and len(hedgers) == 1:
        crd, confidence, extra = hedgers[0], MEDIUM, ["hedge_tiebreak"]
    kinds = [k for k in ("name", "phone", "address") if crd in by_kind[k]] + extra
    evidence = {"keys": sorted({f"{kind}:{key}" for kind, key, c in found if crd is None or c == crd}),
                "candidates": sorted({c for _k, _key, c in found}, key=lambda c: (len(c), c))}
    return manager_id, crd, confidence, json.dumps(kinds), json.dumps(evidence)


def _name_token_check(crd, confidence, tokens, adv_tokens):
    """(crd, confidence, extra kinds) when the manager and adviser share a name token,
    else no link."""
    if tokens & adv_tokens.get(crd, frozenset()):
        return crd, confidence, ["name_token"]
    return None, NONE, []


# Launches: pooled funds first filed within LAUNCH_WINDOW_DAYS of each other
# that share a manager, a signer, an over-cap entity or a fund family stem.

LAUNCH_WINDOW_DAYS = 30


def _days(start, end):
    if not start or not end:
        return 0 if start == end else LAUNCH_WINDOW_DAYS + 1
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


# Fold keys travel with each fund row as kind US key, joined by RS.
_KEY_SEP, _KEYS_SEP = "\x1f", "\x1e"
POOLED = "SELECT cik FROM fund WHERE industry_group = 'Pooled Investment Fund'"


def build_launches(conn, score=None):
    """Fill launch from fund, fund_manager and link_key; return the number of launches.

    Every fund whose latest industry group is Pooled Investment Fund belongs
    to exactly one launch. Funds are walked in (first filing date, first
    accession) order. A fund joins the earliest open launch that started at
    most LAUNCH_WINDOW_DAYS before it and holds a fund sharing one of its
    fold keys: the same manager (when the manager has more than one fund),
    a signer, an over-cap entity key (a large manager or platform: AQR's
    series funds share no other entity), or the same fund family stem.
    Otherwise it starts a new launch. The fixed walk order keeps launch_id
    stable. score(launch, funds) returns (score, reasons) and is applied when
    given.

    The walk reads light rows with each fund's fold keys joined in SQL and
    forgets keys whose launch can no longer be joined. The launches are then
    written to a temporary table and read back with their funds' columns one
    launch at a time, so no fund table is held in Python.
    """
    conn.execute("DELETE FROM launch")
    for table in ("launch_member", "first_d", "key_seen"):
        conn.execute(f"DROP TABLE IF EXISTS temp.{table}")
    walk = conn.execute(
        f"""
        WITH fold AS (
            SELECT cik, group_concat(CASE kind WHEN 'signer' THEN 'signer' ELSE 'entity' END
                                     || char(31) || key, char(30)) AS keys
            FROM link_key
            WHERE (kind = 'signer' AND over_cap = 0)
               OR (kind IN ('entity_stem', 'entity_tokens') AND over_cap = 1)
            GROUP BY cik
        )
        SELECT f.cik, f.first_filing_date, f.family_stem, fm.manager_id, m.n_funds, fold.keys
        FROM fund f JOIN fund_manager fm USING (cik) JOIN manager m USING (manager_id)
        LEFT JOIN fold USING (cik)
        WHERE f.industry_group = 'Pooled Investment Fund'
        ORDER BY f.first_filing_date, f.first_accession
        """)
    windows = []   # [start date, [ciks in the order they joined]]
    latest = {}    # fold key -> index of the latest window holding a fund with it
    prune_after = None
    for cik, day, family, manager_id, n_funds, joined in walk:
        if prune_after is not None and day > prune_after:
            # Windows that started more than LAUNCH_WINDOW_DAYS ago take no more funds.
            latest = {k: i for k, i in latest.items()
                      if _days(windows[i][0], day) <= LAUNCH_WINDOW_DAYS}
            prune_after = None
        if prune_after is None and day:
            prune_after = (date.fromisoformat(day) + timedelta(days=LAUNCH_WINDOW_DAYS)).isoformat()
        keys = {tuple(k.split(_KEY_SEP, 1)) for k in joined.split(_KEYS_SEP)} if joined else set()
        if n_funds > 1:
            keys.add(("manager", manager_id))
        if _usable(family):
            keys.add(("family", family))
        # Windows are opened in date order, so a key's latest window is its
        # only one that can still be within LAUNCH_WINDOW_DAYS.
        open_ = [latest[k] for k in keys if k in latest
                 and _days(windows[latest[k]][0], day) <= LAUNCH_WINDOW_DAYS]
        if open_:
            index = min(open_)
            windows[index][1].append(cik)
        else:
            index = len(windows)
            windows.append([day, [cik]])
        for k in keys:
            latest[k] = max(index, latest.get(k, -1))
    del latest

    conn.execute("CREATE TEMP TABLE launch_member (idx INTEGER, pos INTEGER, cik TEXT, "
                 "PRIMARY KEY (idx, pos))")
    conn.executemany("INSERT INTO temp.launch_member VALUES (?, ?, ?)",
                     ((i, pos, cik) for i, (_day, ciks) in enumerate(windows)
                      for pos, cik in enumerate(ciks)))
    del windows
    conn.execute("CREATE TEMP TABLE first_d AS SELECT cik, min(filing_date || ' ' || accession) AS d "
                 "FROM filing WHERE submission_type = 'D' GROUP BY cik")
    conn.execute("CREATE INDEX temp.first_d_cik ON first_d(cik)")
    # The earliest date any of a pooled fund's keys under the cap was seen on any filing.
    conn.execute(
        f"""
        CREATE TEMP TABLE key_seen AS
        WITH seen AS (
            SELECT kind, key, min(first_date) AS first_seen FROM link_key
            WHERE over_cap = 0 GROUP BY kind, key
        )
        SELECT lk.cik, min(seen.first_seen) AS first_seen
        FROM link_key lk JOIN seen USING (kind, key)
        WHERE lk.over_cap = 0 AND lk.cik IN ({POOLED}) GROUP BY lk.cik
        """)
    conn.execute("CREATE INDEX temp.key_seen_cik ON key_seen(cik)")
    members = conn.execute(
        """
        SELECT lm.idx, f.*, fm.manager_id, fd.d AS first_d, ks.first_seen AS key_first_seen,
               (SELECT group_concat(coalesce(s.title, ''), char(30)) FROM signature s
                WHERE s.accession = f.first_accession) AS first_titles
        FROM launch_member lm JOIN fund f USING (cik) JOIN fund_manager fm USING (cik)
        LEFT JOIN first_d fd USING (cik) LEFT JOIN key_seen ks USING (cik)
        ORDER BY lm.idx, lm.pos
        """)

    def launch_rows():
        for _idx, window in _groups(members):
            yield _launch_row(window, score)

    columns = ("launch_id", "manager_id", "display_name", "anchor_cik", "fund_type", "window_start",
               "window_end", "fund_ciks", "newness_score", "newness_reasons")
    n = conn.executemany(f"INSERT INTO launch ({', '.join(columns)}) "
                         f"VALUES ({', '.join('?' * len(columns))})", launch_rows()).rowcount
    for table in ("launch_member", "first_d", "key_seen"):
        conn.execute(f"DROP TABLE temp.{table}")
    return max(n, 0)


def _groups(rows):
    """(idx, [row dicts]) for consecutive rows sharing their first column."""
    current, group = None, []
    for r in rows:
        if r[0] != current and group:
            yield current, group
            group = []
        current = r[0]
        group.append(dict(r))
    if group:
        yield current, group


def _launch_row(window, score):
    """The launch row for one window of fund dicts, in joining order."""
    # launch_id is the earliest D filing of any fund in the window; a
    # fund with no D in the database falls back to its first filing.
    def anchor_key(f):
        return (f["first_d"][:10], f["first_d"][11:]) if f["first_d"] else (
            f["first_filing_date"] or "", f["first_accession"])

    anchor = min(window, key=anchor_key)
    launch_id = anchor_key(anchor)[1]
    types = {f["fund_type"] for f in window}
    launch = {
        "launch_id": launch_id,
        "manager_id": anchor["manager_id"],
        "window_start": window[0]["first_filing_date"],
    }
    points, reasons = score(launch, window) if score is not None else (None, [])
    return (launch_id, anchor["manager_id"], anchor["name"], anchor["cik"],
            "Hedge Fund" if "Hedge Fund" in types else anchor["fund_type"],
            window[0]["first_filing_date"], max(f["first_filing_date"] for f in window),
            json.dumps([f["cik"] for f in window]), points, json.dumps(reasons))


def launch_scorer(conn):
    """A score(launch, funds) for build_launches, with the facts newness needs.

    Each fund row from build_launches carries key_first_seen (the earliest date
    any of its keys under the cap was seen on any filing) and first_titles (the
    signer titles on its first filing). This reads, once per run, the over-cap
    entity keys of pooled funds, as display name and fund count, and each
    manager's fund first-filing dates, to count funds filed before a launch.
    Over-cap phone and address keys belong to registered agents and
    administrators and say nothing about the manager, so they are not read.
    """
    from bisect import bisect_left

    from fundtracker import newness

    platforms = _over_cap_entities(conn, POOLED)
    manager_dates = defaultdict(list)
    for r in conn.execute("SELECT fm.manager_id, f.first_filing_date FROM fund_manager fm "
                          "JOIN fund f USING (cik) ORDER BY f.first_filing_date"):
        manager_dates[r[0]].append(r[1] or "")

    def score(launch, funds):
        start = launch["window_start"]
        seen = [f["key_first_seen"] for f in funds if f.get("key_first_seen")]
        facts = {
            "window_start": start,
            "key_seen_before": bool(seen) and min(seen) < start,
            "prior_funds": bisect_left(manager_dates[launch["manager_id"]], start),
            "signer_titles": [t for f in funds for t in (f.get("first_titles") or "").split(_KEYS_SEP)
                              if f.get("first_titles") is not None],
            "platforms": _merge_platforms(p for f in funds for p in platforms.get(f["cik"], ())),
            "funds": [dict(f, exemptions=json.loads(f["exemptions"])) for f in funds],
        }
        return newness.score_launch(facts)

    return score


def _over_cap_entities(conn, ciks_sql):
    """cik -> {(display name, number of CIKs on the key)} for the over-cap entity keys of
    the CIKs ciks_sql selects. A key's display name is the one most of its CIKs use."""
    names, sizes = {}, {}
    displays = defaultdict(lambda: defaultdict(int))
    for r in conn.execute("SELECT kind, key, display, count(*) AS n FROM link_key "
                          "WHERE over_cap = 1 AND kind IN ('entity_stem', 'entity_tokens') "
                          "GROUP BY kind, key, display"):
        k = (r["kind"], r["key"])
        sizes[k] = sizes.get(k, 0) + r["n"]
        displays[k][r["display"] or r["key"]] += r["n"]
    for k, counts in displays.items():
        names[k] = most_frequent(counts)
    out = defaultdict(set)
    for r in conn.execute(f"SELECT cik, kind, key FROM link_key WHERE over_cap = 1 "
                          f"AND kind IN ('entity_stem', 'entity_tokens') AND cik IN ({ciks_sql})"):
        k = (r["kind"], r["key"])
        out[r["cik"]].add((names[k], sizes[k]))
    return out


def _merge_platforms(pairs):
    """Sorted [(name, n), ...] with one entry per name, at its largest count. A stem key
    and its sorted-token key usually name the same organisation with different counts."""
    best = {}
    for name, n in pairs:
        best[name] = max(n, best.get(name, 0))
    return sorted(best.items())


DERIVE_SOURCE = "derive"
# SQLite memory for a derive run. The map lets the joins read the raw tables
# without a copy per page, but mapped pages count toward the process's working
# set, and the harness stops a run when the machine runs short. 4 GiB put the
# peak at 2.65 GiB (Session 7); see the decisions log for the budget.
DERIVE_MMAP_BYTES = 512 * 1024 * 1024
DERIVE_CACHE_KIB = 131072


def run(conn, today=None):
    """Rebuild every derived table in one transaction and log it; return the counts.

    The ingest_log row (source derive, key the run date) is replaced on every
    run, since derive always rebuilds from scratch. review is not touched.
    """
    started = _now()
    conn.execute(f"PRAGMA mmap_size = {DERIVE_MMAP_BYTES}")
    conn.execute(f"PRAGMA cache_size = -{DERIVE_CACHE_KIB}")
    counts = {}
    with conn:
        counts["fund"] = build_funds(conn)
        keys = build_link_keys(conn)
        counts["link_key"], counts["over_cap"] = keys["rows"], keys["over_cap"]
        counts["manager"] = build_managers(conn)
        build_manager_stats(conn)
        counts["manager_adviser"] = build_manager_advisers(conn)
        counts["fund_manager"] = counts["fund"]
        counts["launch"] = build_launches(conn, score=launch_scorer(conn))
        counts["search_index"] = repo.rebuild_search_index(conn)
        repo.log_ingest(conn, DERIVE_SOURCE, (today or date.today()).isoformat(), "ok",
                        n_rows=counts["launch"], started_at=started, finished_at=_now())
    return counts


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
