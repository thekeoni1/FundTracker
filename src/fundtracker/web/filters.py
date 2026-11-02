"""The query-string filters of the Launches and Emerging pages.

Each page's filters parse from request.args into a plain dict, with a
default for anything missing or not understood, so every URL renders. The
option tables give each choice the label the form and the summary line show.
"""

import re
from datetime import date, timedelta

from fundtracker.traction import months_before

TYPES = {"hedge": "Hedge Fund only", "all": "every pooled fund type"}
RULES = {"any": "any exemption", "b": "Rule 506(b)", "c": "Rule 506(c)"}
MIN_INVESTMENTS = {"any": "any minimum", "<100k": "minimum under 100,000",
                   "100k-1m": "minimum 100,000 to under 1,000,000", ">1m": "minimum 1,000,000 or more"}
RAISED = {"any": "any amount raised", "0": "nothing raised", ">0": "something raised",
          ">1m": "raised 1,000,000 or more", ">10m": "raised 10,000,000 or more"}
PLATFORMS = {"hide": "organisations on many funds hidden", "show": "organisations on many funds shown"}
PRIOR_FIRMS = {"any": "with or without a prior firm", "with": "with a prior firm",
               "without": "without a prior firm"}
ADVISERS = {"any": "with or without a Form ADV adviser", "linked": "with a Form ADV adviser",
            "ria": "registered adviser (RIA)", "era": "exempt reporting adviser (ERA)",
            "none": "no Form ADV adviser"}
OFFERINGS = {"any": "open or lapsed offering", "open": "offering open", "lapsed": "offering lapsed"}
EMPLOYEES = {"any": "any headcount", "1-5": "1 to 5 employees", "6-20": "6 to 20 employees",
             "21+": "21 or more employees"}
LAUNCH_SORTS = {"score": "best score first", "date": "newest first", "raised": "most raised first"}
EMERGING_SORTS = {"score": "best score first", "raised": "most raised first",
                  "first_seen": "newest first"}

LAUNCH_DAYS = 7
EMERGING_FROM_MONTHS = 18
EMERGING_TO_MONTHS = 6


def launch_filters(args, today):
    """The Launches filters: a filing date range, default the last 7 days, and the rest."""
    return {
        "since": _date(args.get("since"), today, today - timedelta(days=LAUNCH_DAYS)),
        "until": _date(args.get("until"), today, today),
        "min_score": _int(args.get("min_score")),
        "type": _choice(args.get("type"), TYPES),
        "rule": _choice(args.get("rule"), RULES),
        "state": _states(args.getlist("state")),
        "min_inv": _choice(args.get("min_inv"), MIN_INVESTMENTS),
        "raised": _choice(args.get("raised"), RAISED),
        "platforms": _choice(args.get("platforms"), PLATFORMS),
        "sort": _choice(args.get("sort"), LAUNCH_SORTS),
    }


def emerging_filters(args, today):
    """The Emerging filters: a first-seen date range, default 18 to 6 months ago, and the rest."""
    return {
        "first_seen_from": _date(args.get("first_seen_from"), today,
                                 months_before(today, EMERGING_FROM_MONTHS)),
        "first_seen_to": _date(args.get("first_seen_to"), today, months_before(today, EMERGING_TO_MONTHS)),
        "min_score": _int(args.get("min_score")),
        "type": _choice(args.get("type"), TYPES),
        "raised": _choice(args.get("raised"), RAISED),
        "state": _states(args.getlist("state")),
        "platforms": _choice(args.get("platforms"), PLATFORMS),
        "prior_firm": _choice(args.get("prior_firm"), PRIOR_FIRMS),
        "adviser": _choice(args.get("adviser"), ADVISERS),
        "employees": _choice(args.get("employees"), EMPLOYEES),
        "offering": _choice(args.get("offering"), OFFERINGS),
        "sort": _choice(args.get("sort"), EMERGING_SORTS),
    }


def describe_launches(f):
    """The filters as short phrases for the line above the table."""
    return _describe(f, [f"filed {f['since']} to {f['until']}"], LAUNCH_SORTS, (
        ("type", TYPES), ("rule", RULES), ("min_inv", MIN_INVESTMENTS), ("raised", RAISED),
        ("platforms", PLATFORMS)))


def describe_emerging(f):
    return _describe(f, [f"first seen {f['first_seen_from']} to {f['first_seen_to']}"], EMERGING_SORTS, (
        ("type", TYPES), ("raised", RAISED), ("platforms", PLATFORMS), ("prior_firm", PRIOR_FIRMS),
        ("adviser", ADVISERS), ("employees", EMPLOYEES), ("offering", OFFERINGS)))


def _describe(f, phrases, sorts, choices):
    if f["min_score"]:
        phrases.append(f"score {f['min_score']} or more")
    for key, options in choices:
        if key == "type" or f[key] != next(iter(options)):
            phrases.append(options[f[key]])
    if f["state"]:
        phrases.append("state " + ", ".join(f["state"]))
    phrases.append(sorts[f["sort"]])
    return phrases


def _date(text, today, default):
    """An ISO date, or 7d for seven days before today; default for anything else."""
    text = (text or "").strip()
    m = re.fullmatch(r"(\d+)d", text)
    if m:
        return today - timedelta(days=int(m.group(1)))
    try:
        return date.fromisoformat(text)
    except ValueError:
        return default


def _int(text, default=0):
    try:
        return max(0, int((text or "").strip()))
    except ValueError:
        return default


def _choice(text, options):
    """text when it is one of options' keys, else the first key (the default)."""
    return text if text in options else next(iter(options))


def _states(values):
    """Two-letter codes from repeated state parameters, each of which may hold several,
    separated by commas or spaces. Upper case, de-duplicated, in order."""
    codes = []
    for value in values:
        for code in re.split(r"[\s,]+", value.strip().upper()):
            if re.fullmatch(r"[A-Z0-9]{2}", code) and code not in codes:
                codes.append(code)
    return codes
