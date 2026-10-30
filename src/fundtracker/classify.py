"""Hedge-fund candidate rule (PLAN.md 7.1). Pure functions, no database.

Every Form D is ingested; this decides which pooled funds the inbox treats as
hedge funds. The rule and the name patterns are module constants so they can
be tuned without touching the logic.
"""

import re

from fundtracker.normalize import norm_text

POOLED_FUND = "Pooled Investment Fund"
HEDGE_FUND = "Hedge Fund"
OTHER_FUND = "Other Investment Fund"

# An Other Investment Fund is a candidate only with one of these exemptions.
COMPANY_ACT_EXEMPTIONS = frozenset({"3C.1", "3C.7"})

# Before the 2013 rule change, Rule 506 filings carry 06; later ones say 06b or 06c.
EXEMPTION_ALIASES = {"06": "06b"}

# Names that mark an Other Investment Fund as not a hedge fund. Matched with
# re.search against normalize.norm_text(name): upper case, punctuation turned
# into spaces, so "Co-Invest" arrives as "CO INVEST".
EXCLUSION_PATTERNS = {
    "real_estate": r"\bREAL ESTATE\b|\bREALTY\b|\bPROPERT(Y|IES)\b",
    "reit": r"\bREITS?\b",
    "spv": r"\bSPVS?\b|\bSPECIAL PURPOSE\b",
    "co_invest": r"\bCO ?INVEST(MENT|MENTS|OR|ORS)?\b",
    "credit_opportunities": r"\bCREDIT OPPORTUNIT(Y|IES)\b",
    "private_equity": r"\bPRIVATE EQUITY\b",
    "venture": r"\bVENTURES?\b",
    "buyout": r"\bBUY ?OUTS?\b",
    "growth_equity": r"\bGROWTH EQUITY\b",
    "infrastructure": r"\bINFRASTRUCTURE\b",
    "secondaries": r"\bSECONDAR(Y|IES)\b",
    "bdc": r"\bBDC\b|\bBUSINESS DEVELOPMENT\b",
    "clo": r"\bCLOS?\b|\bCOLLATERALI[SZ]ED LOAN\b",
    "pe_vc_feeder": r"\b(PE|VC)\b",  # "XYZ PE Feeder", "ABC VC Access Fund"
}

# The subset behind the newness penalty for PE, VC and real estate names.
PE_VC_RE_PATTERNS = ("real_estate", "reit", "private_equity", "venture", "buyout",
                     "growth_equity", "pe_vc_feeder")

_COMPILED = {key: re.compile(pattern) for key, pattern in EXCLUSION_PATTERNS.items()}


def normalize_exemptions(exemptions):
    """Exemption codes with 06 read as 06b, in filed order, without repeats."""
    out = []
    for code in exemptions or ():
        code = EXEMPTION_ALIASES.get(code, code)
        if code not in out:
            out.append(code)
    return out


RULE_506 = {"06b": "506(b)", "06c": "506(c)"}


def rule_506(exemptions):
    """The Rule 506 variants among normalised exemption codes: "506(b)", "506(c)", both, or ""."""
    return ", ".join(RULE_506[c] for c in sorted(RULE_506) if c in (exemptions or ()))


def exclusion_matches(name):
    """The keys of EXCLUSION_PATTERNS that match name, in table order."""
    text = norm_text(name)
    return [key for key, pattern in _COMPILED.items() if pattern.search(text)]


def matches_pe_vc_re(name):
    """True if name looks like a private equity, venture or real estate vehicle."""
    text = norm_text(name)
    return any(_COMPILED[key].search(text) for key in PE_VC_RE_PATTERNS)


def is_hedge_candidate(industry_group, fund_type, exemptions, issuer_name):
    """(is candidate, reason code): hedge_fund, other_fund_3c, or "" when not a candidate."""
    if industry_group != POOLED_FUND:
        return False, ""
    if fund_type == HEDGE_FUND:
        return True, "hedge_fund"
    if fund_type == OTHER_FUND:
        codes = set(normalize_exemptions(exemptions))
        if codes & COMPANY_ACT_EXEMPTIONS and not exclusion_matches(issuer_name):
            return True, "other_fund_3c"
    return False, ""
