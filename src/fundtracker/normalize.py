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
# Name parts left out of a display name, compared after norm_text, besides the
# placeholders above and any single letter: Trian files middle name "P.",
# Vauban first name "NA". Display only: name_norm keeps them, so stored and
# newly loaded rows still give the same linking keys.
DISPLAY_FRAGMENTS = frozenset({"P", "L", "NA", "N A", "NONE", "NMN"})
# Name parts that stand for "no name" in an individual's name, compared after
# norm_text: (none), NONE, NMN and N.M.N. (no middle name), NMI, N/A. Single
# letters are kept: in the data they are initials ("J. Derek Ramsey") or
# abbreviated surnames ("Ben S."), not placeholders.
PERSON_PLACEHOLDERS = frozenset({"NONE", "NMN", "NMI", "NA", "N A"})
# A related person filed as an individual whose name holds one of these is an
# entity ("Carne Global Fund Managers (Luxembourg) S.A."), not a person to write to.
PERSON_ENTITY_TOKENS = frozenset("LLC LLP LP LTD LIMITED INC CORP CORPORATION GP SA SARL AG PLC".split())
# A first-name part ending in one of these before a last-name part that does
# not is the back half of a reversed entity name (AQR files "Management, LLC"
# and "AQR Capital", Belltower "Ltd." and "Belltower Fund Group,").
DISPLAY_SUFFIX_TOKENS = frozenset("LLC LP LTD INC CORP".split())

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
    """None, "N/A" in any case, or text with no letters or digits ("-", "--")."""
    return part is None or part.strip().upper() in ENTITY_FIRST_NAMES or not norm_text(part)


def street2(street1, text):
    """street2, or None when it is empty or only repeats street1.

    The data sets often copy street1 into an empty street2 (5 of 6 cases
    checked against the XML in 2026 Q3); the sixth filer typed the line
    twice. Either way the repeat carries nothing, and dropping it makes the
    XML and the data set agree.
    """
    text = none_to_null(text)
    if text is not None and text == none_to_null(street1):
        return None
    return text


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


def _is_fragment(part):
    if _is_placeholder(part):
        return True
    normalised = norm_text(part)
    return normalised in DISPLAY_FRAGMENTS or len(normalised) == 1


def _ends_in_suffix(part):
    tokens = norm_text(part).split()
    return bool(tokens) and tokens[-1] in DISPLAY_SUFFIX_TOKENS


def entity_display_name(first, middle, last):
    """A related person's name as the filer wrote it, for display, or None.

    Placeholder and fragment parts are dropped, and reversed halves swapped
    back: ("Management, LLC", None, "AQR Capital") gives "AQR Capital
    Management, LLC", ("-", "P.", "Trian Fund Management, L.P.") gives
    "Trian Fund Management, L.P.". Linking still uses entity_stem.
    """
    first, middle, last = (None if _is_fragment(p) else " ".join(p.split())
                           for p in (first, middle, last))
    if first and last and _ends_in_suffix(first) and not _ends_in_suffix(last):
        first, last = last, first
    return " ".join(p for p in (first, middle, last) if p) or None


def person_display_name(first, middle, last):
    """An individual's name as filed, placeholders dropped, initials kept: "Jane Q. Doe".

    ("Stephan", "NMN", "Klee") gives "Stephan Klee", ("(none)", None, "Smith") "Smith".
    """
    parts = [" ".join(p.split()) for p in (first, middle, last)
             if not _is_placeholder(p) and norm_text(p) not in PERSON_PLACEHOLDERS]
    return " ".join(parts) or None


def looks_like_entity(name):
    """True when a name filed as an individual holds an entity suffix (PERSON_ENTITY_TOKENS)."""
    return any(t in PERSON_ENTITY_TOKENS for t in norm_text(name).split())


def family_display_name(issuer_name):
    """fund_family_stem in the filer's own capitalisation: "Zephyr Master Fund, LP" -> "Zephyr"."""
    if not issuer_name:
        return None
    name = re.sub(r"(?i)\bco-?invest(ment)?\b", "COINVEST", issuer_name)
    words = [w for w in name.split()
             if norm_text(w) and not set(norm_text(w).split()) <= FUND_FAMILY_TOKENS]
    return " ".join(words).strip(" ,") or " ".join(issuer_name.split())


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
    """ISO date string from ISO (2026-05-01, optionally with a time), DD-MON-YYYY (30-SEP-2026)
    or YYYYMMDD; else None. A time, as in the older data sets' FILING_DATE, is dropped."""
    text = none_to_null(text)
    if text is None:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?", text):
            return date.fromisoformat(text[:10]).isoformat()
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
