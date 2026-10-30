"""The SEC's quarterly Form D data sets: discover, download, read.

Reference: docs/edgar-formd-reference.md section 5. The data sets page links
one zip per quarter. Links use more than one URL prefix and, for 2008 Q2 to
2013 Q4, a "_d_0.zip" file name instead of "_d.zip", so quarters are found
by pattern rather than by building URLs.
"""

import csv
import io
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin

from fundtracker import normalize as n
from fundtracker.model import (
    Filing,
    FormDFiling,
    Issuer,
    Offering,
    Recipient,
    RelatedPerson,
    Signature,
)

DATASETS_PAGE = "https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets"

_ZIP_LINK = re.compile(
    r"""href=["']([^"']*form-d-data-sets/(\d{4})q([1-4])_d(?:_(\d+))?\.zip)["']""", re.I
)


@dataclass(frozen=True, order=True)
class Quarter:
    year: int
    q: int
    url: str = ""

    @property
    def key(self):
        """The ingest_log key and file stem, e.g. 2026q3."""
        return f"{self.year}q{self.q}"

    @classmethod
    def parse(cls, text):
        """Quarter from "2026q3" (case-insensitive); url is left empty."""
        m = re.fullmatch(r"(\d{4})[qQ]([1-4])", text.strip())
        if not m:
            raise ValueError(f"not a quarter: {text!r} (expected e.g. 2026q3)")
        return cls(int(m.group(1)), int(m.group(2)))


def parse_quarter_links(html, base_url=DATASETS_PAGE):
    """Quarters linked from the data sets page, ascending, one per quarter.

    When a quarter is linked more than once, the plain "_d.zip" name wins
    over a numbered "_d_N.zip" one.
    """
    best = {}
    for m in _ZIP_LINK.finditer(html):
        href, year, q, suffix = m.groups()
        rank = -1 if suffix is None else int(suffix)
        key = (int(year), int(q))
        if key not in best or rank < best[key][0]:
            best[key] = (rank, urljoin(base_url, href))
    return [Quarter(y, q, url) for (y, q), (_rank, url) in sorted(best.items())]


def list_quarters(client):
    """Fetch the data sets page and return every quarter it links, ascending."""
    response = client.get(DATASETS_PAGE)
    response.raise_for_status()
    return parse_quarter_links(response.text)


def zip_path(quarter, dest_dir):
    return Path(dest_dir) / f"{quarter.key}_d.zip"


def download_quarter(client, quarter, dest_dir):
    """Stream the quarter's zip to dest_dir/{yyyy}q{n}_d.zip and return the path.

    A file already there with non-zero size is kept and not fetched again;
    the folder is the cache. Writes to a .part file first so an interrupted
    download never looks complete.
    """
    target = zip_path(quarter, dest_dir)
    if target.is_file() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    response = client.get(quarter.url, stream=True)
    try:
        response.raise_for_status()
        with open(part, "wb") as out:
            for chunk in response.iter_content(chunk_size=1 << 20):
                out.write(chunk)
    finally:
        response.close()
    part.replace(target)
    return target


# ---------------------------------------------------------------------------
# Reading a quarter zip

TSV_FILES = ("FORMDSUBMISSION", "OFFERING", "ISSUERS", "RELATEDPERSONS", "RECIPIENTS", "SIGNATURES")

# Column -> (kind, model field). Kinds are converted by _convert below:
#   text      none_to_null
#   date      parse_date (the files use ISO, ISO with a time, and DD-MON-YYYY)
#   bool      parse_bool (true/false, YES/NO, empty is None)
#   int       parse_int
#   amount    <field>_text, <field>_num, <field>_is_indefinite
#   money     <field>_text, <field>_num
#   csv       comma-separated list -> tuple, in filed order
#   states    comma-separated list -> sorted tuple without duplicates
#   part      one numbered column of a list (RELATIONSHIP_1..3) -> tuple
#   seq       SEQ_KEY, used for ordering and then renumbered from 1
#   key       ACCESSIONNUMBER
# Every header column must appear either here or in IGNORED_COLUMNS; a test
# checks this so a new SEC column fails loudly.
SUBMISSION_COLUMNS = {
    "ACCESSIONNUMBER": ("key", "accession"),
    "FILE_NUM": ("text", "file_num"),
    "FILING_DATE": ("date", "filing_date"),
    "SCHEMAVERSION": ("text", "schema_version"),
    "SUBMISSIONTYPE": ("text", "submission_type"),
    "TESTORLIVE": ("text", "test_or_live"),
}
OFFERING_COLUMNS = {
    "ACCESSIONNUMBER": ("key", "accession"),
    "INDUSTRYGROUPTYPE": ("text", "industry_group"),
    "INVESTMENTFUNDTYPE": ("text", "fund_type"),
    "IS40ACT": ("bool", "is_40act"),
    "REVENUERANGE": ("text", "revenue_range"),
    "AGGREGATENETASSETVALUERANGE": ("text", "nav_range"),
    "FEDERALEXEMPTIONS_ITEMS_LIST": ("csv", "exemptions"),
    "ISAMENDMENT": ("bool", "is_amendment"),
    "PREVIOUSACCESSIONNUMBER": ("text", "previous_accession"),  # moved to Filing
    "SALE_DATE": ("date", "sale_date"),
    "YETTOOCCUR": ("bool", "sale_yet_to_occur"),
    "MORETHANONEYEAR": ("bool", "more_than_one_year"),
    "ISEQUITYTYPE": ("bool", "is_equity"),
    "ISDEBTTYPE": ("bool", "is_debt"),
    "ISOPTIONTOACQUIRETYPE": ("bool", "is_option_to_acquire"),
    "ISSECURITYTOBEACQUIREDTYPE": ("bool", "is_security_to_be_acquired"),
    "ISPOOLEDINVESTMENTFUNDTYPE": ("bool", "is_pooled_fund"),
    "ISTENANTINCOMMONTYPE": ("bool", "is_tenant_in_common"),
    "ISMINERALPROPERTYTYPE": ("bool", "is_mineral_property"),
    "ISOTHERTYPE": ("bool", "is_other_type"),
    "DESCRIPTIONOFOTHERTYPE": ("text", "other_type_desc"),
    "ISBUSINESSCOMBINATIONTRANS": ("bool", "is_business_combination"),
    "BUSCOMBCLARIFICATIONOFRESP": ("text", "business_combination_clarification"),
    "MINIMUMINVESTMENTACCEPTED": ("money", "min_investment"),
    "TOTALOFFERINGAMOUNT": ("amount", "total_offering_amount"),
    "TOTALAMOUNTSOLD": ("amount", "total_amount_sold"),
    "TOTALREMAINING": ("amount", "total_remaining"),
    "SALESAMTCLARIFICATIONOFRESP": ("text", "sales_amount_clarification"),
    "HASNONACCREDITEDINVESTORS": ("bool", "has_non_accredited"),
    "NUMBERNONACCREDITEDINVESTORS": ("int", "n_non_accredited"),
    "TOTALNUMBERALREADYINVESTED": ("int", "n_investors"),
    "SALESCOMM_DOLLARAMOUNT": ("money", "sales_commissions"),
    "SALESCOMM_ISESTIMATE": ("bool", "sales_commissions_is_estimate"),
    "FINDERSFEE_DOLLARAMOUNT": ("money", "finders_fees"),
    "FINDERSFEE_ISESTIMATE": ("bool", "finders_fees_is_estimate"),
    "FINDERFEECLARIFICATIONOFRESP": ("text", "fees_clarification"),
    "GROSSPROCEEDSUSED_DOLLARAMOUNT": ("money", "gross_proceeds_used"),
    "GROSSPROCEEDSUSED_ISESTIMATE": ("bool", "gross_proceeds_used_is_estimate"),
    "GROSSPROCEEDSUSED_CLAROFRESP": ("text", "gross_proceeds_clarification"),
    "AUTHORIZEDREPRESENTATIVE": ("bool", "authorized_representative"),
}
_ADDRESS_COLUMNS = {
    "STREET1": ("text", "street1"),
    "STREET2": ("text", "street2"),
    "CITY": ("text", "city"),
    "STATEORCOUNTRY": ("text", "state_or_country"),
    "STATEORCOUNTRYDESCRIPTION": ("text", "state_or_country_desc"),
    "ZIPCODE": ("text", "zip"),
}
ISSUER_COLUMNS = {
    "ACCESSIONNUMBER": ("key", "accession"),
    "IS_PRIMARYISSUER_FLAG": ("bool", "is_primary"),
    "ISSUER_SEQ_KEY": ("seq", "seq"),
    "CIK": ("text", "cik"),
    "ENTITYNAME": ("text", "entity_name"),
    **_ADDRESS_COLUMNS,
    "ISSUERPHONENUMBER": ("text", "phone"),
    "JURISDICTIONOFINC": ("text", "jurisdiction"),
    "ISSUER_PREVIOUSNAME_1": ("part", "previous_names"),
    "ISSUER_PREVIOUSNAME_2": ("part", "previous_names"),
    "ISSUER_PREVIOUSNAME_3": ("part", "previous_names"),
    "EDGAR_PREVIOUSNAME_1": ("part", "edgar_previous_names"),
    "EDGAR_PREVIOUSNAME_2": ("part", "edgar_previous_names"),
    "EDGAR_PREVIOUSNAME_3": ("part", "edgar_previous_names"),
    "ENTITYTYPE": ("text", "entity_type"),
    "ENTITYTYPEOTHERDESC": ("text", "entity_type_other"),
    "YEAROFINC_TIMESPAN_CHOICE": ("text", "year_of_inc_choice"),
    "YEAROFINC_VALUE_ENTERED": ("int", "year_of_inc_value"),
}
RELATED_PERSON_COLUMNS = {
    "ACCESSIONNUMBER": ("key", "accession"),
    "RELATEDPERSON_SEQ_KEY": ("seq", "seq"),
    "FIRSTNAME": ("text", "first"),
    "MIDDLENAME": ("text", "middle"),
    "LASTNAME": ("text", "last"),
    **_ADDRESS_COLUMNS,
    "RELATIONSHIP_1": ("part", "relationships"),
    "RELATIONSHIP_2": ("part", "relationships"),
    "RELATIONSHIP_3": ("part", "relationships"),
    "RELATIONSHIPCLARIFICATION": ("text", "clarification"),
}
RECIPIENT_COLUMNS = {
    "ACCESSIONNUMBER": ("key", "accession"),
    "RECIPIENT_SEQ_KEY": ("seq", "seq"),
    "RECIPIENTNAME": ("text", "name"),
    "RECIPIENTCRDNUMBER": ("text", "crd"),
    "ASSOCIATEDBDNAME": ("text", "bd_name"),
    "ASSOCIATEDBDCRDNUMBER": ("text", "bd_crd"),
    **_ADDRESS_COLUMNS,
    "STATES_OR_VALUE_LIST": ("states", "states"),
    "FOREIGNSOLICITATION": ("bool", "foreign_solicitation"),
}
SIGNATURE_COLUMNS = {
    "ACCESSIONNUMBER": ("key", "accession"),
    "SIGNATURE_SEQ_KEY": ("seq", "seq"),
    "ISSUERNAME": ("text", "issuer_name"),
    "SIGNATURENAME": ("text", "signature_name"),
    "NAMEOFSIGNER": ("text", "signer"),
    "SIGNATURETITLE": ("text", "title"),
    "SIGNATUREDATE": ("date", "date"),
}
COLUMNS = {
    "FORMDSUBMISSION": SUBMISSION_COLUMNS,
    "OFFERING": OFFERING_COLUMNS,
    "ISSUERS": ISSUER_COLUMNS,
    "RELATEDPERSONS": RELATED_PERSON_COLUMNS,
    "RECIPIENTS": RECIPIENT_COLUMNS,
    "SIGNATURES": SIGNATURE_COLUMNS,
}
# Columns read on purpose and dropped, with the reason.
IGNORED_COLUMNS = {
    "FORMDSUBMISSION": {
        "SIC_CODE": "not in the XML and not used",
        "OVER100PERSONSFLAG": "data set flag for filings listing over 100 related persons; not in the model",
        "OVER100ISSUERFLAG": "data set flag for filings listing over 100 issuers; not in the model",
    },
    "OFFERING": {
        "OVER100RECIPIENTFLAG": "data set flag for filings listing over 100 recipients; not in the model",
    },
    "ISSUERS": {},
    "RELATEDPERSONS": {},
    "RECIPIENTS": {"DESCRIPTIONS_LIST": "state names for STATES_OR_VALUE_LIST; codes are kept"},
    "SIGNATURES": {},
}


def read_quarter(zip_path):
    """Yield one FormDFiling per row of FORMDSUBMISSION in a quarter zip.

    Child rows are sorted by SEQ_KEY and renumbered from 1, with the primary
    issuer first, so a filing reads the same as from its XML. One quarter is
    held in memory while it is read (tens of MB); callers go a quarter at a time.
    """
    with zipfile.ZipFile(zip_path) as zf:
        tables = {name: _read_tsv(zf, name) for name in TSV_FILES}

    offerings = {row["accession"]: row for row in tables["OFFERING"]}
    children = {}
    for name in ("ISSUERS", "RELATEDPERSONS", "RECIPIENTS", "SIGNATURES"):
        grouped = defaultdict(list)
        for row in tables[name]:
            grouped[row["accession"]].append(row)
        children[name] = grouped

    for sub in tables["FORMDSUBMISSION"]:
        yield _filing(sub, offerings.get(sub["accession"]), children)


def tsv_headers(zip_path):
    """{table: [header columns]} for the six TSVs in a quarter zip."""
    result = {}
    with zipfile.ZipFile(zip_path) as zf:
        for name in TSV_FILES:
            with _open_member(zf, name) as fh:
                result[name] = next(csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE))
    return result


def _open_member(zf, name):
    """The one <name>.tsv in the zip, whatever quarter folder it sits in."""
    wanted = f"{name}.tsv".upper()
    matches = [m for m in zf.namelist() if PurePosixPath(m).name.upper() == wanted]
    if len(matches) != 1:
        raise ValueError(f"{zf.filename}: expected one {name}.tsv, found {matches}")
    return io.TextIOWrapper(zf.open(matches[0]), encoding="utf-8", errors="replace", newline="")


def _read_tsv(zf, name):
    """Rows of one TSV, converted to model field names and values."""
    csv.field_size_limit(1 << 30)
    columns = COLUMNS[name]
    with _open_member(zf, name) as fh:
        reader = csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        return [_convert(raw, columns) for raw in reader]


def _split(text):
    text = n.none_to_null(text)
    if text is None:
        return ()
    return tuple(p for p in (n.none_to_null(x) for x in text.split(",")) if p is not None)


def _convert(raw, columns):
    out = {}
    for column, (kind, field) in columns.items():
        value = raw.get(column)
        if kind == "key":
            out[field] = (value or "").strip()
        elif kind == "text":
            out[field] = n.none_to_null(value)
        elif kind == "date":
            out[field] = n.parse_date(value)
        elif kind == "bool":
            out[field] = n.parse_bool(value)
        elif kind in ("int", "seq"):
            out[field] = n.parse_int(value)
        elif kind == "amount":
            text = n.none_to_null(value)
            num, indefinite = n.parse_amount(text)
            out[f"{field}_text"] = text
            out[f"{field}_num"] = num
            out[f"{field}_is_indefinite"] = indefinite
        elif kind == "money":
            text = n.none_to_null(value)
            out[f"{field}_text"] = text
            out[f"{field}_num"] = n.parse_int(text)
        elif kind == "csv":
            out[field] = _split(value)
        elif kind == "states":
            out[field] = tuple(sorted(set(_split(value))))
        elif kind == "part":
            part = n.none_to_null(value)
            out[field] = out.get(field, ()) + ((part,) if part is not None else ())
        else:
            raise ValueError(f"unknown column kind {kind!r} for {column}")
    return out


def _seq_key(row):
    return (row["seq"] is None, row["seq"] or 0)


def _renumber(rows):
    return [dict(r, seq=i) for i, r in enumerate(rows, start=1)]


def _filing(sub, offering_row, children):
    accession = sub["accession"]
    offering_row = dict(offering_row or {"accession": accession})
    previous_accession = offering_row.pop("previous_accession", None)

    issuer_rows = sorted(children["ISSUERS"].get(accession, []),
                         key=lambda r: (not r["is_primary"], _seq_key(r)))
    issuers = tuple(
        Issuer(**dict(r, is_primary=bool(r["is_primary"]), cik=_cik(r["cik"])))
        for r in _renumber(issuer_rows)
    )
    people = []
    for r in _renumber(sorted(children["RELATEDPERSONS"].get(accession, []), key=_seq_key)):
        joined = n.join_name(r["first"], r["middle"], r["last"])
        people.append(RelatedPerson(**r, name_norm=joined,
                                    is_entity=n.is_entity(r["first"], r["last"], joined)))
    recipients = tuple(
        Recipient(**r)
        for r in _renumber(sorted(children["RECIPIENTS"].get(accession, []), key=_seq_key))
    )
    signatures = tuple(
        Signature(**r, signer_norm=n.norm_text(r["signer"]) or None)
        for r in _renumber(sorted(children["SIGNATURES"].get(accession, []), key=_seq_key))
    )

    filing = Filing(
        accession=accession,
        cik=issuers[0].cik if issuers else None,
        filing_date=sub["filing_date"],
        submission_type=sub["submission_type"],
        previous_accession=previous_accession,
        file_num=sub["file_num"],
        schema_version=sub["schema_version"],
        test_or_live=sub["test_or_live"],
        source="dataset",
    )
    return FormDFiling(
        filing=filing,
        offering=Offering(**offering_row),
        issuers=issuers,
        related_persons=tuple(people),
        recipients=recipients,
        signatures=signatures,
    )


def _cik(text):
    return text.zfill(10) if text and text.isdigit() else text
