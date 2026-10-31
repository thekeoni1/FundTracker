"""Named filter presets: a row of links above each page's filters.

Each preset is (label, query dict) over the page's existing filters. Its link
carries only those keys, so every other filter stays at its default. A preset
is active when each of its keys, parsed as the page parses it, equals the
current filter. Nothing is stored.
"""

from urllib.parse import urlencode

from werkzeug.datastructures import MultiDict

LAUNCH_PRESETS = (
    ("Hedge launches this week", {"type": "hedge", "since": "7d"}),
    ("Raised already", {"raised": ">0"}),
    ("Small minimums", {"min_inv": "<100k"}),
)
EMERGING_PRESETS = (
    ("Small shops with a website", {"adviser": "linked", "employees": "1-5"}),
    ("Open and not contacted", {"offering": "open", "outreach": "untouched"}),
    ("Spin-outs", {"prior_firm": "with"}),
)


def marked(presets, current, parse, today):
    """[{"label", "query", "active"}] for presets, given the current filters dict and
    parse(args, today), the page's filter function."""
    out = []
    for label, query in presets:
        wanted = parse(MultiDict(query), today)
        out.append({"label": label, "query": urlencode(query),
                    "active": all(wanted[key] == current[key] for key in query)})
    return out
