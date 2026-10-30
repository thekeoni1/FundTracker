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
    set. Entity and signer rows also carry a display name: of the written
    forms (normalize.entity_display_name of one first, middle and last, or
    the signer as filed) the CIK's filings give that key, the one filed most
    often, ties to the longest. Only that one form is kept per row while
    scanning: a count table per row held every form of 1.7 million keys and
    ran the real run out of memory (Session 5). Returns a dict with the rows
    written and, per capped kind, how many distinct keys are over the cap.
    """
    conn.execute("DELETE FROM link_key")
    first = {}  # (kind, key, cik) -> earliest filing date
    best = {}   # (kind, key, cik) -> display rank of its best written form, see _display_rank

    def add(kind, key, cik, day, display=None, n=0):
        k = (kind, key, cik)
        if k not in first or (day and (first[k] is None or day < first[k])):
            first[k] = day
        if display:
            rank = _display_rank(display, n)
            if k not in best or rank < best[k]:
                best[k] = rank

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
                   best[(kind, key, cik)][2] if (kind, key, cik) in best else None)
                  for (kind, key, cik), day in first.items())
    conn.executemany("INSERT INTO link_key (kind, key, cik, first_date, over_cap, display) "
                     "VALUES (?, ?, ?, ?, ?, ?)", rows)
    return {"rows": len(rows), "over_cap": over_cap}


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

    SQLite counts the CIKs per manager and name, so Python only keeps the best
    name per manager while reading the counts.
    """
    best = {}  # manager_id -> (source rank, display rank)
    for r in conn.execute(
            """
            SELECT fm.manager_id,
                   CASE WHEN lk.kind = 'signer' THEN 2 WHEN lk.over_cap THEN 1 ELSE 0 END AS source,
                   lk.display, count(DISTINCT lk.cik) AS n
            FROM link_key lk JOIN fund_manager fm USING (cik)
            WHERE lk.kind IN ('entity_stem', 'signer') AND lk.display IS NOT NULL
            GROUP BY fm.manager_id, source, lk.display
            """):
        rank = (r["source"], _display_rank(r["display"], r["n"]))
        if r["manager_id"] not in best or rank < best[r["manager_id"]]:
            best[r["manager_id"]] = rank
    conn.executemany("UPDATE manager SET display_name = ? WHERE manager_id = ?",
                     sorted((rank[1][2], manager_id) for manager_id, rank in best.items()))


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
    places = {}
    for r in conn.execute(
            """
            SELECT m.manager_id, i.city, i.state_or_country, i.state_or_country_desc
            FROM manager m JOIN fund f ON f.cik = substr(m.manager_id, 2)
            LEFT JOIN issuer i ON i.accession = f.latest_accession AND i.is_primary = 1
            """):
        places[r[0]] = _place(r[1], r[2], r[3])
    rows = []
    for r in conn.execute(
            """
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
            """):
        manager_id = r["manager_id"]
        types = sorted(set((r["types"] or "").split(",")) - {""})
        rows.append((r["n_filings"], r["latest"], r["sold"], r["investors"], int(bool(r["amended"])),
                     int(manager_id in over_cap), json.dumps(types),
                     *places.get(manager_id, (None, None, None)), manager_id[1:], manager_id))
    conn.executemany(
        """
        UPDATE manager SET n_filings = ?, latest_filing_date = ?, total_sold = ?,
               total_investors = ?, has_amendment = ?, is_over_cap = ?, fund_types = ?,
               city = ?, state = ?, country = ?, edgar_cik = ?
        WHERE manager_id = ?
        """, rows)
    conn.executemany("UPDATE manager SET people = ? WHERE manager_id = ?", _people(conn))


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
        if not name or not r["name_norm"]:
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


# Launches: pooled funds first filed within LAUNCH_WINDOW_DAYS of each other
# that share a manager, a signer, an over-cap entity or a fund family stem.

LAUNCH_WINDOW_DAYS = 30


def _days(start, end):
    if not start or not end:
        return 0 if start == end else LAUNCH_WINDOW_DAYS + 1
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _fold_keys(conn):
    """cik -> set of fold keys from link_key: ("signer", key) for every signer, and
    ("entity", key) for every over-cap entity key, which names a large manager or
    platform (AQR's series funds share no other entity)."""
    keys = defaultdict(set)
    for r in conn.execute(
            """
            SELECT cik, kind, key FROM link_key
            WHERE (kind = 'signer' AND over_cap = 0)
               OR (kind IN ('entity_stem', 'entity_tokens') AND over_cap = 1)
            """):
        keys[r["cik"]].add(("signer" if r["kind"] == "signer" else "entity", r["key"]))
    return keys


def build_launches(conn, score=None):
    """Fill launch from fund, fund_manager and link_key; return the number of launches.

    Every fund whose latest industry group is Pooled Investment Fund belongs
    to exactly one launch. Funds are walked in (first filing date, first
    accession) order. A fund joins the earliest open launch that started at
    most LAUNCH_WINDOW_DAYS before it and holds a fund sharing one of its
    fold keys: the same manager (when the manager has more than one fund),
    a signer, an over-cap entity key, or the same fund family stem.
    Otherwise it starts a new launch. The fixed walk order keeps launch_id
    stable. score(launch, funds) returns (score, reasons) and is applied when
    given.
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
        ORDER BY f.first_filing_date, f.first_accession
        """)]
    shared = _fold_keys(conn)

    windows = []  # each a list of funds, in the order they joined
    latest = {}   # fold key -> index of the latest window holding a fund with it
    for fund in funds:
        keys = set(shared.get(fund["cik"], ()))
        if fund["n_funds"] > 1:
            keys.add(("manager", fund["manager_id"]))
        if _usable(fund["family_stem"]):
            keys.add(("family", fund["family_stem"]))
        # Windows are opened in date order, so a key's latest window is its
        # only one that can still be within LAUNCH_WINDOW_DAYS.
        open_ = [latest[k] for k in keys if k in latest
                 and _days(windows[latest[k]][0]["first_filing_date"],
                           fund["first_filing_date"]) <= LAUNCH_WINDOW_DAYS]
        if open_:
            index = min(open_)
            windows[index].append(fund)
        else:
            index = len(windows)
            windows.append([fund])
        for k in keys:
            latest[k] = max(index, latest.get(k, -1))

    rows = []
    for window in windows:
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
            "window_end": max(f["first_filing_date"] for f in window),
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
    keys under the cap was seen on any filing; its over-cap entity keys, as
    display name and fund count; the signer titles on its first filing; and
    each manager's fund first-filing dates, to count funds filed before a
    launch. Over-cap phone and address keys belong to registered agents and
    administrators and say nothing about the manager, so they are not read.
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
    platforms = _over_cap_entities(conn, pooled)
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
        build_manager_stats(conn)
        counts["fund_manager"] = counts["fund"]
        counts["launch"] = build_launches(conn, score=launch_scorer(conn))
        repo.log_ingest(conn, DERIVE_SOURCE, (today or date.today()).isoformat(), "ok",
                        n_rows=counts["launch"], started_at=started, finished_at=_now())
    return counts


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
