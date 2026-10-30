"""Data set reader tests on a small hand-built zip that uses the real headers."""

import zipfile

import pytest

from fundtracker.edgar import datasets

# Header rows as in 2026q3_d.zip (identical in 2010q2_d.zip).
HEADERS = {
    "FORMDSUBMISSION": ["ACCESSIONNUMBER", "FILE_NUM", "FILING_DATE", "SIC_CODE", "SCHEMAVERSION",
                        "SUBMISSIONTYPE", "TESTORLIVE", "OVER100PERSONSFLAG", "OVER100ISSUERFLAG"],
    "ISSUERS": ["ACCESSIONNUMBER", "IS_PRIMARYISSUER_FLAG", "ISSUER_SEQ_KEY", "CIK", "ENTITYNAME",
                "STREET1", "STREET2", "CITY", "STATEORCOUNTRY", "STATEORCOUNTRYDESCRIPTION", "ZIPCODE",
                "ISSUERPHONENUMBER", "JURISDICTIONOFINC", "ISSUER_PREVIOUSNAME_1",
                "ISSUER_PREVIOUSNAME_2", "ISSUER_PREVIOUSNAME_3", "EDGAR_PREVIOUSNAME_1",
                "EDGAR_PREVIOUSNAME_2", "EDGAR_PREVIOUSNAME_3", "ENTITYTYPE", "ENTITYTYPEOTHERDESC",
                "YEAROFINC_TIMESPAN_CHOICE", "YEAROFINC_VALUE_ENTERED"],
    "OFFERING": ["ACCESSIONNUMBER", "INDUSTRYGROUPTYPE", "INVESTMENTFUNDTYPE", "IS40ACT",
                 "REVENUERANGE", "AGGREGATENETASSETVALUERANGE", "FEDERALEXEMPTIONS_ITEMS_LIST",
                 "ISAMENDMENT", "PREVIOUSACCESSIONNUMBER", "SALE_DATE", "YETTOOCCUR",
                 "MORETHANONEYEAR", "ISEQUITYTYPE", "ISDEBTTYPE", "ISOPTIONTOACQUIRETYPE",
                 "ISSECURITYTOBEACQUIREDTYPE", "ISPOOLEDINVESTMENTFUNDTYPE", "ISTENANTINCOMMONTYPE",
                 "ISMINERALPROPERTYTYPE", "ISOTHERTYPE", "DESCRIPTIONOFOTHERTYPE",
                 "ISBUSINESSCOMBINATIONTRANS", "BUSCOMBCLARIFICATIONOFRESP",
                 "MINIMUMINVESTMENTACCEPTED", "OVER100RECIPIENTFLAG", "TOTALOFFERINGAMOUNT",
                 "TOTALAMOUNTSOLD", "TOTALREMAINING", "SALESAMTCLARIFICATIONOFRESP",
                 "HASNONACCREDITEDINVESTORS", "NUMBERNONACCREDITEDINVESTORS",
                 "TOTALNUMBERALREADYINVESTED", "SALESCOMM_DOLLARAMOUNT", "SALESCOMM_ISESTIMATE",
                 "FINDERSFEE_DOLLARAMOUNT", "FINDERSFEE_ISESTIMATE", "FINDERFEECLARIFICATIONOFRESP",
                 "GROSSPROCEEDSUSED_DOLLARAMOUNT", "GROSSPROCEEDSUSED_ISESTIMATE",
                 "GROSSPROCEEDSUSED_CLAROFRESP", "AUTHORIZEDREPRESENTATIVE"],
    "RECIPIENTS": ["ACCESSIONNUMBER", "RECIPIENT_SEQ_KEY", "RECIPIENTNAME", "RECIPIENTCRDNUMBER",
                   "ASSOCIATEDBDNAME", "ASSOCIATEDBDCRDNUMBER", "STREET1", "STREET2", "CITY",
                   "STATEORCOUNTRY", "STATEORCOUNTRYDESCRIPTION", "ZIPCODE", "STATES_OR_VALUE_LIST",
                   "DESCRIPTIONS_LIST", "FOREIGNSOLICITATION"],
    "RELATEDPERSONS": ["ACCESSIONNUMBER", "RELATEDPERSON_SEQ_KEY", "FIRSTNAME", "MIDDLENAME",
                       "LASTNAME", "STREET1", "STREET2", "CITY", "STATEORCOUNTRY",
                       "STATEORCOUNTRYDESCRIPTION", "ZIPCODE", "RELATIONSHIP_1", "RELATIONSHIP_2",
                       "RELATIONSHIP_3", "RELATIONSHIPCLARIFICATION"],
    "SIGNATURES": ["ACCESSIONNUMBER", "SIGNATURE_SEQ_KEY", "ISSUERNAME", "SIGNATURENAME",
                   "NAMEOFSIGNER", "SIGNATURETITLE", "SIGNATUREDATE"],
}


def unmapped(headers):
    """Columns that are neither mapped nor deliberately ignored, per table."""
    return {
        table: [c for c in cols
                if c not in datasets.COLUMNS[table] and c not in datasets.IGNORED_COLUMNS[table]]
        for table, cols in headers.items()
    }


def test_real_headers_are_all_mapped_or_ignored():
    assert unmapped(HEADERS) == {t: [] for t in datasets.TSV_FILES}


def test_a_new_column_is_reported():
    headers = {t: list(c) for t, c in HEADERS.items()}
    headers["OFFERING"].append("SOMENEWCOLUMN")
    assert unmapped(headers)["OFFERING"] == ["SOMENEWCOLUMN"]


def test_mapping_has_no_stale_columns():
    for table, cols in HEADERS.items():
        assert set(datasets.COLUMNS[table]) <= set(cols), table
        assert set(datasets.IGNORED_COLUMNS[table]) <= set(cols), table


def build_zip(path, rows, folder="2026Q3_d"):
    """Write a quarter zip; rows maps table -> list of {column: value} (others empty)."""
    with zipfile.ZipFile(path, "w") as zf:
        for table, cols in HEADERS.items():
            lines = ["\t".join(cols)]
            for row in rows.get(table, []):
                lines.append("\t".join(row.get(c, "") for c in cols))
            zf.writestr(f"{folder}/{table}.tsv", "\n".join(lines) + "\n")
        zf.writestr(f"{folder}/FormD_readme.html", "<p>ignored</p>")
    return path


ACC = "0009999999-26-000001"
ROWS = {
    "FORMDSUBMISSION": [{
        "ACCESSIONNUMBER": ACC, "FILE_NUM": "021-555555               ", "FILING_DATE": "30-SEP-2026",
        "SIC_CODE": "6726", "SCHEMAVERSION": "X0708", "SUBMISSIONTYPE": "D/A", "TESTORLIVE": "LIVE",
    }],
    "OFFERING": [{
        "ACCESSIONNUMBER": ACC, "INDUSTRYGROUPTYPE": "Pooled Investment Fund",
        "INVESTMENTFUNDTYPE": "Hedge Fund", "IS40ACT": "false",
        "AGGREGATENETASSETVALUERANGE": "Decline to Disclose",
        "FEDERALEXEMPTIONS_ITEMS_LIST": "06c, 3C, 3C.1", "ISAMENDMENT": "true",
        "PREVIOUSACCESSIONNUMBER": "0009999999-26-000000", "SALE_DATE": "2026-05-01",
        "YETTOOCCUR": "", "MORETHANONEYEAR": "true", "ISPOOLEDINVESTMENTFUNDTYPE": "true",
        "MINIMUMINVESTMENTACCEPTED": "100000", "TOTALOFFERINGAMOUNT": "Indefinite",
        "TOTALAMOUNTSOLD": "2500000", "TOTALREMAINING": "Indefinite",
        "TOTALNUMBERALREADYINVESTED": "4", "SALESCOMM_DOLLARAMOUNT": "0",
        "SALESCOMM_ISESTIMATE": "true", "OVER100RECIPIENTFLAG": "false",
    }],
    "ISSUERS": [
        {"ACCESSIONNUMBER": ACC, "IS_PRIMARYISSUER_FLAG": "NO", "ISSUER_SEQ_KEY": "101",
         "CIK": "0002222222", "ENTITYNAME": "Example Offshore Fund Ltd"},
        {"ACCESSIONNUMBER": ACC, "IS_PRIMARYISSUER_FLAG": "YES", "ISSUER_SEQ_KEY": "102",
         "CIK": "0001111111", "ENTITYNAME": "Example Fund LP", "STATEORCOUNTRY": "E9",
         "STATEORCOUNTRYDESCRIPTION": "CAYMAN ISLANDS", "ISSUER_PREVIOUSNAME_1": "None",
         "EDGAR_PREVIOUSNAME_1": "Old Example Fund LP", "EDGAR_PREVIOUSNAME_2": "Older Example LP",
         "YEAROFINC_TIMESPAN_CHOICE": "withinFiveYears", "YEAROFINC_VALUE_ENTERED": "2026"},
    ],
    "RELATEDPERSONS": [
        {"ACCESSIONNUMBER": ACC, "RELATEDPERSON_SEQ_KEY": "103", "FIRSTNAME": "-",
         "LASTNAME": "Example GP LLC", "RELATIONSHIP_1": "Promoter"},
        {"ACCESSIONNUMBER": ACC, "RELATEDPERSON_SEQ_KEY": "101", "FIRSTNAME": "Jane",
         "MIDDLENAME": "Q", "LASTNAME": "Smith", "RELATIONSHIP_1": "Executive Officer",
         "RELATIONSHIP_2": "Director"},
        {"ACCESSIONNUMBER": ACC, "RELATEDPERSON_SEQ_KEY": "102", "FIRSTNAME": "John",
         "LASTNAME": "Doe", "RELATIONSHIP_1": "Executive Officer"},
    ],
    "RECIPIENTS": [{
        "ACCESSIONNUMBER": ACC, "RECIPIENT_SEQ_KEY": "101", "RECIPIENTNAME": "Placement Co",
        "RECIPIENTCRDNUMBER": "None", "ASSOCIATEDBDNAME": "None", "ASSOCIATEDBDCRDNUMBER": "None",
        "STATES_OR_VALUE_LIST": "TX, FL, GA, FL", "DESCRIPTIONS_LIST": "TEXAS, FLORIDA, GEORGIA",
        "FOREIGNSOLICITATION": "false",
    }],
    "SIGNATURES": [
        {"ACCESSIONNUMBER": ACC, "SIGNATURE_SEQ_KEY": "102", "ISSUERNAME": "Example Offshore Fund Ltd",
         "NAMEOFSIGNER": "Jane Q. Smith", "SIGNATUREDATE": "2026-09-29"},
        {"ACCESSIONNUMBER": ACC, "SIGNATURE_SEQ_KEY": "101", "ISSUERNAME": "Example Fund LP",
         "NAMEOFSIGNER": "Jane Q. Smith", "SIGNATUREDATE": "2026-09-29"},
    ],
}


@pytest.fixture
def filing(tmp_path):
    (f,) = datasets.read_quarter(build_zip(tmp_path / "2026q3_d.zip", ROWS))
    return f


def test_filing_conventions(filing):
    f = filing.filing
    assert f.accession == ACC and f.source == "dataset"
    assert f.filing_date == "2026-09-30"
    assert f.file_num == "021-555555"
    assert f.submission_type == "D/A" and f.schema_version == "X0708" and f.test_or_live == "LIVE"
    assert f.previous_accession == "0009999999-26-000000"
    assert f.cik == "0001111111"  # the primary issuer, not the first row


def test_issuers_primary_first_and_renumbered(filing):
    primary, co = filing.issuers
    assert (primary.seq, primary.is_primary, primary.entity_name) == (1, True, "Example Fund LP")
    assert (co.seq, co.is_primary, co.cik) == (2, False, "0002222222")
    assert primary.previous_names == ()
    assert primary.edgar_previous_names == ("Old Example Fund LP", "Older Example LP")
    assert (primary.year_of_inc_choice, primary.year_of_inc_value) == ("withinFiveYears", 2026)
    assert primary.street1 is None


def test_offering_conventions(filing):
    o = filing.offering
    assert o.exemptions == ("06c", "3C", "3C.1")
    assert o.is_40act is False and o.is_amendment is True
    assert o.sale_yet_to_occur is None and o.sale_date == "2026-05-01"
    assert (o.total_offering_amount_text, o.total_offering_amount_num,
            o.total_offering_amount_is_indefinite) == ("Indefinite", None, True)
    assert (o.total_amount_sold_num, o.total_amount_sold_is_indefinite) == (2500000, False)
    assert (o.min_investment_text, o.min_investment_num) == ("100000", 100000)
    assert o.n_investors == 4 and o.n_non_accredited is None
    assert (o.sales_commissions_num, o.sales_commissions_is_estimate) == (0, True)
    assert o.is_equity is None


def test_children_sorted_by_seq_key_and_renumbered(filing):
    people = filing.related_persons
    assert [p.seq for p in people] == [1, 2, 3]
    assert [p.name_norm for p in people] == ["JANE Q SMITH", "JOHN DOE", "EXAMPLE GP LLC"]
    assert [p.is_entity for p in people] == [False, False, True]
    assert people[0].relationships == ("Executive Officer", "Director")
    assert [s.issuer_name for s in filing.signatures] == ["Example Fund LP", "Example Offshore Fund Ltd"]
    assert filing.signatures[0].signer_norm == "JANE Q SMITH"


def test_recipient_conventions(filing):
    (r,) = filing.recipients
    assert r.seq == 1 and r.name == "Placement Co"
    assert (r.crd, r.bd_name, r.bd_crd) == (None, None, None)
    assert r.states == ("FL", "GA", "TX")
    assert r.foreign_solicitation is False


def test_filing_without_children_or_offering(tmp_path):
    rows = {"FORMDSUBMISSION": [{"ACCESSIONNUMBER": ACC, "FILING_DATE": "2010-06-15 12:23:18"}]}
    (f,) = datasets.read_quarter(build_zip(tmp_path / "q.zip", rows, folder="2010Q2_d"))
    assert f.filing.filing_date == "2010-06-15"
    assert f.filing.cik is None and f.issuers == () and f.offering.accession == ACC


def test_missing_table_is_an_error(tmp_path):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("2026Q3_d/FORMDSUBMISSION.tsv", "\t".join(HEADERS["FORMDSUBMISSION"]) + "\n")
    with pytest.raises(ValueError, match="OFFERING"):
        list(datasets.read_quarter(path))


def test_missing_child_table_reads_as_empty(tmp_path):
    """2008q2_d.zip has no RECIPIENTS.tsv."""
    path = build_zip(tmp_path / "q.zip", ROWS)
    trimmed = tmp_path / "no_recipients.zip"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(trimmed, "w") as out:
        for name in src.namelist():
            if not name.endswith("RECIPIENTS.tsv"):
                out.writestr(name, src.read(name))
    (f,) = datasets.read_quarter(trimmed)
    assert f.recipients == () and len(f.related_persons) == 3
    assert "RECIPIENTS" not in datasets.tsv_headers(trimmed)
