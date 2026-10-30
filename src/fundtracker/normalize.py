"""Pure helpers that turn Form D text into comparable values (PLAN.md 7.2).

No XML and no SQLite here: the XML parser and the WP2 data set loader both
call these, so the two sources normalise identically.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# Tokens that mark a related person as an entity rather than an individual,
# compared after norm_text, so L.L.C. and L.P. arrive as LLC and LP.
ENTITY_TOKENS = frozenset(
    "LLC LP LTD LIMITED INC CORP GP PARTNERS CAPITAL MANAGEMENT ADVISORS ADVISERS HOLDINGS".split()
)
# Placeholders filers put in firstName when the related person is an entity,
# compared upper-cased (GEF files "n/a").
ENTITY_FIRST_NAMES = frozenset({"", "-", "N/A"})
# Removed anywhere in an entity name to get its stem.
ENTITY_SUFFIX_TOKENS = frozenset("LLC LP LTD INC GP THE".split())
# Removed anywhere in an issuer name to get its fund-family stem.
FUND_FAMILY_TOKENS = frozenset(
    "FUND LP LTD LLC MASTER FEEDER OFFSHORE ONSHORE CAYMAN INTERNATIONAL "
    "I II III IV V A B SERIES SPV COINVEST".split()
)

_MONTHS = {m: i for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), start=1)}


def none_to_null(text):
    """Strip text; return None for missing, empty, or the literal "None" in any case.

    EDGAR writes "None" for absent CRD numbers and previous names, and some
    filers type "none" as a recipient name.
    """
    if text is None:
        return None
    text = text.strip()
    return None if text == "" or text.lower() == "none" else text


def _is_placeholder(part):
    return part is None or part.strip().upper() in ENTITY_FIRST_NAMES


def norm_text(text):
    """Upper case, drop dots and apostrophes, other punctuation to spaces, collapse.

    Dots are dropped rather than spaced so L.L.C. becomes LLC and L.P. becomes LP.
    """
    if text is None:
        return ""
    text = re.sub(r"[.'’]", "", text.upper())
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return text.strip()


def join_name(first, middle, last):
    """Join the three name parts into one normalised name, or None if all are empty.

    Placeholder parts such as "-" and "N/A" are skipped, so both entity
    encodings (firstName="-" lastName="X GP LLC" and firstName="X GP"
    lastName="LLC") give "X GP LLC".
    """
    parts = [p for p in (first, middle, last) if not _is_placeholder(p)]
    joined = norm_text(" ".join(parts))
    return joined or None


def is_entity(first, last, joined=None):
    """True when the related person is an entity, judged on the joined name."""
    if _is_placeholder(first):
        return True
    tokens = norm_text(joined if joined is not None else join_name(first, None, last)).split()
    return any(t in ENTITY_TOKENS for t in tokens)


def entity_stem(name):
    """Name without LLC, LP, LTD, INC, GP and THE: "Axiaris Capital Management LLC" -> "AXIARIS CAPITAL MANAGEMENT"."""
    tokens = [t for t in norm_text(name).split() if t not in ENTITY_SUFFIX_TOKENS]
    return " ".join(tokens) or None


def fund_family_stem(issuer_name):
    """The part of a fund name shared by its master, feeder and offshore vehicles.

    "X Master Fund LP", "X Offshore Fund Ltd" and "X Fund LP" all give "X".
    Falls back to the whole normalised name if every token is a family token.
    """
    if not issuer_name:
        return None
    name = re.sub(r"(?i)\bco-?invest(ment)?\b", "COINVEST", issuer_name)
    normalised = norm_text(name)
    tokens = [t for t in normalised.split() if t not in FUND_FAMILY_TOKENS]
    return " ".join(tokens) or normalised or None


def address_key(street1, zip_code):
    """street1 plus zip, upper case, letters and digits only; None if both are empty."""
    key = re.sub(r"[^A-Z0-9]", "", f"{street1 or ''}{zip_code or ''}".upper())
    return key or None


def parse_amount(text):
    """Return (num, is_indefinite) for an amount as Form D files it.

    "25000000" -> (25000000, False); "Indefinite" -> (None, True);
    missing or unparseable -> (None, None). Commas, $ and a zero fraction are tolerated.
    """
    text = none_to_null(text)
    if text is None:
        return None, None
    if text.lower() == "indefinite":
        return None, True
    try:
        value = Decimal(text.replace(",", "").replace("$", ""))
    except InvalidOperation:
        return None, None
    if value != value.to_integral_value():
        return None, None
    return int(value), False


def parse_int(text):
    """An integer count, or None."""
    num, _ = parse_amount(text)
    return num


def parse_date(text):
    """ISO date string from ISO (2026-05-01), DD-MON-YYYY (30-SEP-2026) or YYYYMMDD; else None."""
    text = none_to_null(text)
    if text is None:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text).isoformat()
        if re.fullmatch(r"\d{8}", text):
            return datetime.strptime(text, "%Y%m%d").date().isoformat()
        m = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", text)
        if m and m.group(2).upper() in _MONTHS:
            return date(int(m.group(3)), _MONTHS[m.group(2).upper()], int(m.group(1))).isoformat()
    except ValueError:
        return None
    return None


def parse_bool(text):
    """True for "true" or "YES", False for "false" or "NO", None for empty, missing or anything else."""
    text = none_to_null(text)
    if text is None:
        return None
    return {"true": True, "yes": True, "false": False, "no": False}.get(text.lower())
