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

from fundtracker import classify, normalize
from fundtracker.normalize import entity_stem, fund_family_stem, is_entity, norm_text

log = logging.getLogger(__name__)

# Linking keys that say nothing about who runs a fund. Compared with the
# entity stem or normalised signer name; extend from what the real data shows.
LINK_KEY_STOPLIST = frozenset({
    "GENERAL PARTNER", "MANAGING MEMBER", "MANAGEMENT", "CAPITAL", "PARTNERS",
    "ADVISORS", "N/A", "NA", "NONE", "SAME AS ISSUER",
})
# Keys shorter than this are skipped.
MIN_KEY_LENGTH = 4
# Phones must have at least this many digits.
MIN_PHONE_DIGITS = 7
# A phone or address shared by more CIKs than this belongs to a service
# provider (registered agent, administrator, law firm) and is dropped.
# Entity and signer keys are never capped.
SERVICE_PROVIDER_CAP = 50
CAPPED_KINDS = ("phone", "address")


def build_funds(conn):
    """One fund row per filer CIK; return the number written.

    Name, classification and exemptions come from the latest filing (by
    filing date, then accession); offering terms and year of incorporation
    from the first, which is the notice that launched the fund.
    """
    conn.execute("DELETE FROM fund")
    rows = conn.execute(
        """
        WITH ranked AS MATERIALIZED (
            SELECT accession, cik, filing_date,
                   row_number() OVER (PARTITION BY cik ORDER BY filing_date, accession) AS rn_first,
                   row_number() OVER (PARTITION BY cik ORDER BY filing_date DESC, accession DESC) AS rn_last,
                   count(*) OVER (PARTITION BY cik) AS n
            FROM filing
        )
        SELECT l.cik, l.n, f.accession AS first_accession, f.filing_date AS first_filing_date,
               l.accession AS latest_accession, l.filing_date AS latest_filing_date,
               i.entity_name, i.state_or_country, i.state_or_country_desc,
               o.industry_group, o.fund_type, o.exemptions,
               fo.sale_yet_to_occur, fo.total_amount_sold_num, fo.n_investors,
               fo.min_investment_num, fo.total_offering_amount_num,
               fo.total_offering_amount_is_indefinite,
               fi.year_of_inc_choice, fi.year_of_inc_value
        FROM ranked l
        JOIN ranked f ON f.cik = l.cik AND f.rn_first = 1
        LEFT JOIN issuer i ON i.accession = l.accession AND i.is_primary = 1
        LEFT JOIN offering o ON o.accession = l.accession
        LEFT JOIN offering fo ON fo.accession = f.accession
        LEFT JOIN issuer fi ON fi.accession = f.accession AND fi.is_primary = 1
        WHERE l.rn_last = 1
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


def entity_tokens(stem):
    """The stem as a sorted token set, so "MANAGEMENT AQR CAPITAL" matches "AQR CAPITAL MANAGEMENT"."""
    return " ".join(sorted(set(stem.split())))


def build_link_keys(conn):
    """Fill link_key with every usable key per CIK and its earliest filing date.

    Returns a dict with the rows written and, per capped kind, how many
    service-provider keys were dropped.
    """
    conn.execute("DELETE FROM link_key")
    first = {}  # (kind, key, cik) -> earliest filing date

    def add(kind, key, cik, day):
        k = (kind, key, cik)
        if k not in first or (day and (first[k] is None or day < first[k])):
            first[k] = day

    for r in conn.execute(
            """
            SELECT f.cik, rp.name_norm, min(f.filing_date) AS day
            FROM related_person rp JOIN filing f USING (accession)
            WHERE rp.is_entity = 1
            GROUP BY f.cik, rp.name_norm
            """):
        stem = entity_stem(r["name_norm"])
        if _usable(stem):
            add("entity_stem", stem, r["cik"], r["day"])
            add("entity_tokens", entity_tokens(stem), r["cik"], r["day"])

    for r in conn.execute(
            """
            SELECT f.cik, s.signer_norm, i.entity_name, min(f.filing_date) AS day
            FROM signature s JOIN filing f USING (accession)
            LEFT JOIN issuer i ON i.accession = s.accession AND i.is_primary = 1
            GROUP BY f.cik, s.signer_norm, i.entity_name
            """):
        signer = r["signer_norm"]
        if (not _usable(signer) or signer == norm_text(r["entity_name"])
                or is_entity(signer, None, signer)):
            continue
        add("signer", signer, r["cik"], r["day"])

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
        if kind in CAPPED_KINDS:
            ciks_per_key[(kind, key)] += 1
    dropped_keys = {k for k, n in ciks_per_key.items() if n > SERVICE_PROVIDER_CAP}
    dropped = {kind: sum(1 for k in dropped_keys if k[0] == kind) for kind in CAPPED_KINDS}
    for kind, n in dropped.items():
        log.info("dropped %d %s keys shared by more than %d CIKs", n, kind, SERVICE_PROVIDER_CAP)

    rows = [(kind, key, cik, day) for (kind, key, cik), day in first.items()
            if (kind, key) not in dropped_keys]
    rows.sort()
    conn.executemany("INSERT INTO link_key (kind, key, cik, first_date) VALUES (?, ?, ?, ?)", rows)
    return {"rows": len(rows), "dropped": dropped}


def address_key(street1, zip_code):
    """normalize.address_key, but only when both parts are present.

    A zip alone or a street alone would link every filer in a zip code or on
    "123 Main Street" anywhere.
    """
    if not (street1 and street1.strip() and zip_code and zip_code.strip()):
        return None
    return normalize.address_key(street1, zip_code)
