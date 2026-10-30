"""Golden tests: every expected value below was read from the fixture XML by eye."""

import dataclasses
from pathlib import Path

import pytest

from fundtracker.edgar.formd_xml import parse_formd

FIXTURES = Path(__file__).parent / "fixtures" / "formd"


def load(accession, **kwargs):
    return parse_formd((FIXTURES / f"{accession}.xml").read_bytes(), accession, **kwargs)


def none_fields(obj):
    return {f.name for f in dataclasses.fields(obj) if getattr(obj, f.name) is None}


def test_every_fixture_parses():
    files = sorted(FIXTURES.glob("*.xml"))
    assert len(files) == 12
    for path in files:
        parsed = parse_formd(path.read_bytes(), path.stem)
        assert parsed.accession == path.stem
        assert parsed.issuers and parsed.issuers[0].is_primary


def test_axiaris_worked_example():
    f = load("0002158817-26-000001", filing_date="20261028")
    assert f.filing.cik == "0002158817"
    assert f.filing.filing_date == "2026-10-28"
    assert f.filing.submission_type == "D"
    assert f.filing.schema_version == "X0708"
    assert f.filing.test_or_live == "LIVE"
    assert f.filing.previous_accession is None
    assert f.filing.source == "xml"

    (issuer,) = f.issuers
    assert issuer.entity_name == "Axiaris Capital Management Fund, LP"
    assert (issuer.street1, issuer.street2, issuer.city, issuer.state_or_country, issuer.zip) == (
        "3030 SUMMER STREET", "UNIT 158", "HOUSTON", "TX", "77007")
    assert issuer.state_or_country_desc == "TEXAS"
    assert issuer.phone == "2489219225"
    assert issuer.jurisdiction == "DELAWARE"
    assert issuer.entity_type == "Limited Partnership"
    assert (issuer.year_of_inc_choice, issuer.year_of_inc_value) == ("withinFiveYears", 2026)
    assert issuer.previous_names == () and issuer.edgar_previous_names == ()

    o = f.offering
    assert o.industry_group == "Pooled Investment Fund"
    assert o.fund_type == "Hedge Fund"
    assert o.is_40act is False
    assert o.nav_range == "Decline to Disclose"
    assert o.revenue_range is None
    assert o.exemptions == ("06c", "3C", "3C.1")
    assert o.is_amendment is False
    assert o.sale_yet_to_occur is True and o.sale_date is None
    assert o.more_than_one_year is True
    assert o.is_pooled_fund is True and o.is_equity is None
    assert o.is_business_combination is False
    assert (o.min_investment_text, o.min_investment_num) == ("100000", 100000)
    assert (o.total_offering_amount_num, o.total_offering_amount_is_indefinite) == (25000000, False)
    assert (o.total_amount_sold_num, o.total_amount_sold_is_indefinite) == (0, False)
    assert o.total_remaining_num == 25000000
    assert o.has_non_accredited is False
    assert o.n_investors == 0
    assert o.sales_commissions_num == 0 and o.finders_fees_num == 0 and o.gross_proceeds_used_num == 0
    assert o.authorized_representative is False

    person, entity = f.related_persons
    assert (person.first, person.middle, person.last) == ("Andrew", "Cunningham", "Murtland")
    assert person.name_norm == "ANDREW CUNNINGHAM MURTLAND"
    assert person.is_entity is False
    assert person.relationships == ("Executive Officer",)
    assert person.clarification is None
    assert (entity.first, entity.middle, entity.last) == ("Axiaris Capital Management", None, "LLC")
    assert entity.name_norm == "AXIARIS CAPITAL MANAGEMENT LLC"
    assert entity.is_entity is True
    assert entity.relationships == ("Executive Officer",)
    assert (entity.city, entity.state_or_country, entity.zip) == ("Lewes", "DE", "19958")

    assert f.recipients == ()
    (sig,) = f.signatures
    assert sig.issuer_name == "Axiaris Capital Management Fund, LP"
    assert sig.signature_name == sig.signer == "Andrew Murtland"
    assert sig.signer_norm == "ANDREW MURTLAND"
    assert sig.title == "Managing Member of the General Partner"
    assert sig.date == "2026-10-27"


def test_vc_entity_with_dash_first_name():
    f = load("0002081663-25-000001")
    assert f.issuers[0].entity_name == "VSC Ventures PL10, LLC"
    assert f.issuers[0].entity_type == "Limited Liability Company"
    assert f.offering.fund_type == "Venture Capital Fund"
    assert f.offering.revenue_range == "Decline to Disclose"
    assert f.offering.exemptions == ("06b", "3C", "3C.1")
    (rp,) = f.related_persons
    assert (rp.first, rp.last) == ("-", "VSC Ventures Fund I GP LLC")
    assert rp.name_norm == "VSC VENTURES FUND I GP LLC"
    assert rp.is_entity is True
    assert rp.relationships == ("Promoter",)
    o = f.offering
    assert (o.total_offering_amount_text, o.total_offering_amount_num, o.total_offering_amount_is_indefinite) == (
        "Indefinite", None, True)
    assert (o.total_remaining_num, o.total_remaining_is_indefinite) == (None, True)
    assert o.min_investment_num == 0
    assert f.signatures[0].signer == "Kirk Carson"


def test_hedge_fund_amendment():
    f = load("0001741616-26-000005")
    assert f.filing.submission_type == "D/A"
    assert f.filing.previous_accession == "0001741616-26-000003"
    assert f.offering.is_amendment is True
    issuer = f.issuers[0]
    assert issuer.entity_name == "Callodine Capital Fund, LP"
    assert (issuer.year_of_inc_choice, issuer.year_of_inc_value) == ("overFiveYears", None)
    o = f.offering
    assert o.fund_type == "Hedge Fund"
    assert o.exemptions == ("06b", "3C", "3C.7")
    assert o.sale_date == "2018-07-05" and o.sale_yet_to_occur is None
    assert o.min_investment_num == 1000000
    assert o.total_amount_sold_num == 305445848
    assert o.n_investors == 61
    names = [p.name_norm for p in f.related_persons]
    assert names == ["CALLODINE CAPITAL MANAGEMENT LP", "CALLODINE CAPITAL PARTNERS LLC", "JAMES MORROW"]
    assert [p.is_entity for p in f.related_persons] == [True, True, False]
    # Recipients filed with recipientName "none" and an associated broker-dealer.
    assert [r.name for r in f.recipients] == [None, None]
    assert [r.bd_name for r in f.recipients] == [
        "Manning & Napier Investor Services, Inc.", "Agecroft Partners, LLC"]
    assert all(r.crd is None for r in f.recipients)
    assert [r.states for r in f.recipients] == [("All States",), ("All States",)]
    assert [r.foreign_solicitation for r in f.recipients] == [False, False]
    assert f.signatures[0].signer == "James Morrow"


def test_3c7_fund_filed_by_agent():
    f = load("0000919574-26-007205")
    # The accession prefix is the filing agent; the issuer CIK comes from the XML.
    assert f.filing.cik == "0002159130"
    assert f.issuers[0].entity_name == "Arrogate Capital Fund, LP"
    assert f.offering.fund_type == "Hedge Fund"
    assert "3C.7" in f.offering.exemptions and "3C.1" not in f.offering.exemptions
    assert f.offering.sale_yet_to_occur is True
    (rp,) = f.related_persons
    assert rp.name_norm == "ARROGATE CAPITAL MANAGEMENT LLC" and rp.is_entity
    assert rp.relationships == ("Executive Officer", "Promoter")
    assert f.signatures[0].signer == "Christian Bradeen"


def test_other_investment_fund_with_3c1():
    f = load("0002154220-26-000001")
    assert f.issuers[0].entity_name == "Armada Capital Credit LP"
    assert f.offering.fund_type == "Other Investment Fund"
    assert f.offering.exemptions == ("06b", "3C", "3C.1")
    assert f.offering.sale_date == "2026-10-12"
    assert f.offering.total_amount_sold_num == 300000
    assert f.offering.n_investors == 2
    (rp,) = f.related_persons
    assert (rp.first, rp.middle, rp.last) == ("Armada", "Credit Management", "LLC")
    assert rp.name_norm == "ARMADA CREDIT MANAGEMENT LLC" and rp.is_entity


def test_private_equity_fund():
    f = load("0001012975-26-000984")
    issuer = f.issuers[0]
    assert f.filing.cik == "0002134351"
    assert issuer.entity_name == "Endless VI A LP"
    assert (issuer.city, issuer.state_or_country, issuer.zip) == ("LEEDS", "X0", "LS1 2HL")
    assert f.offering.fund_type == "Private Equity Fund"
    assert f.offering.revenue_range == "Decline to Disclose"
    (rp,) = f.related_persons
    assert (rp.first, rp.last) == ("N/A", "Endless VI (GP) LLP")
    assert rp.name_norm == "ENDLESS VI GP LLP" and rp.is_entity
    assert f.signatures[0].title == "Finance Director"


def test_non_fund_issuer_with_revenue_range():
    f = load("0001214659-26-013245")
    issuer = f.issuers[0]
    assert issuer.entity_name == "Equera, Inc"
    assert issuer.entity_type == "Corporation"
    assert (issuer.year_of_inc_choice, issuer.year_of_inc_value) == ("withinFiveYears", 2025)
    o = f.offering
    assert o.industry_group == "Other Technology"
    assert o.fund_type is None and o.is_40act is None
    assert o.revenue_range == "$1 - $1,000,000"
    assert o.nav_range is None
    assert o.exemptions == ("06b",)
    assert (o.total_offering_amount_num, o.total_amount_sold_num, o.total_remaining_num) == (
        1000000, 75000, 925000)
    assert o.gross_proceeds_used_num == 156000
    assert [p.name_norm for p in f.related_persons] == ["DANIELLE FINCH", "EMMANUEL YAW MANU ANNOR"]
    assert f.related_persons[0].relationships == ("Executive Officer", "Director")
    assert f.related_persons[1].state_or_country == "S6"
    assert f.signatures[0].title == "CEO & Founder"


def test_co_issuers_and_two_signatures():
    f = load("0002161535-26-000001")
    primary, co = f.issuers
    assert (primary.seq, primary.is_primary, primary.cik) == (1, True, "0002161535")
    assert primary.entity_name == "GEF US Climate Solutions Fund III, L.P."
    assert primary.jurisdiction == "DELAWARE"
    assert (co.seq, co.is_primary, co.cik) == (2, False, "0002161347")
    assert co.entity_name == "GEF US Climate Solutions Parallel Fund III, L.P."
    assert co.jurisdiction == "ONTARIO, CANADA"
    assert f.filing.cik == "0002161535"
    assert f.offering.exemptions == ("06b", "3C", "3C.1", "3C.7")
    assert [s.issuer_name for s in f.signatures] == [primary.entity_name, co.entity_name]
    assert [s.seq for s in f.signatures] == [1, 2]
    assert {s.signer for s in f.signatures} == {"Stuart Michael Barkoff"}
    assert f.signatures[0].title == "Managing Director of the Top GP"
    # Related persons: entities with firstName "n/a", plus two individuals.
    assert [p.is_entity for p in f.related_persons] == [True, True, False, False, True, True]
    assert f.related_persons[1].name_norm == "GEF US CSF III GP LLC"
    assert f.related_persons[2].name_norm == "STUART MICHAEL BARKOFF"
    # Three recipients; None placeholders for CRD and broker-dealer become None.
    names = [r.name for r in f.recipients]
    assert names == ["Asante Capital Group Advisors LLC", "Impactus Partners Ltd", "FirstPoint Equity Capital Ltd"]
    asante, impactus, firstpoint = f.recipients
    assert (asante.crd, asante.bd_name, asante.bd_crd) == ("155178", None, None)
    assert (firstpoint.crd, firstpoint.bd_name, firstpoint.bd_crd) == (None, "BA Securities, LLC", "153489")
    assert (asante.city, asante.state_or_country, asante.zip) == ("London", "X0", "W1S 3AN")
    assert [r.foreign_solicitation for r in f.recipients] == [False, True, True]
    # Filed as AL, AK, AZ, ...; stored sorted to match the data sets.
    assert len(asante.states) == 51 and asante.states[:3] == ("AK", "AL", "AR")
    assert "ALABAMA" not in asante.states


def test_recipient_with_states_of_solicitation():
    f = load("0002124823-26-000002")
    assert f.filing.submission_type == "D/A"
    assert f.filing.previous_accession == "0002124823-26-000001"
    assert f.issuers[0].entity_name == "Private Market Securities Fund II, LLC Series A"
    (r,) = f.recipients
    assert r.name == "PHX Financial, Inc."
    assert (r.crd, r.bd_name, r.bd_crd) == ("144403", None, None)
    assert (r.city, r.state_or_country, r.zip) == ("New York", "NY", "10005")
    assert len(r.states) == 41
    assert r.states[:4] == ("AL", "AR", "AZ", "CA") and r.states[-1] == "WY"
    assert r.foreign_solicitation is False
    o = f.offering
    assert o.total_amount_sold_num == 59409033
    assert o.n_investors == 349
    assert (o.sales_commissions_num, o.finders_fees_num, o.gross_proceeds_used_num) == (
        5790903, 529478, 1188180)


def test_over_five_years_and_edgar_previous_name():
    f = load("0001437749-26-033645")
    issuer = f.issuers[0]
    assert issuer.entity_name == "Professional Diversity Network, Inc."
    assert (issuer.year_of_inc_choice, issuer.year_of_inc_value) == ("overFiveYears", None)
    assert issuer.previous_names == ()
    # This filer lists previous names as <previousName>, not <value>.
    assert issuer.edgar_previous_names == ("Professional Diversity Network, LLC",)
    o = f.offering
    assert o.industry_group == "Other Technology"
    assert o.revenue_range == "$5,000,001 - $25,000,000"
    assert o.is_equity is True
    assert o.min_investment_num == 83334
    assert (o.total_offering_amount_num, o.total_amount_sold_num, o.total_remaining_num) == (249999, 249999, 0)
    assert len(f.related_persons) == 6
    assert f.related_persons[0].relationships == ("Executive Officer", "Director")
    assert not any(p.is_entity for p in f.related_persons)


# Fields that come back None for the two older-schema fixtures. Each was checked
# against the XML: the element is absent or empty in that filing. The same
# fields are None for X0708 filings that leave the same optional elements out
# (compare Axiaris), so nothing here is a schema difference. See PROGRESS.md
# open question 1.
OLDER_SCHEMA_NONE_FIELDS = {
    "0000919574-10-003893": {
        "schema": "X0704",
        "filing": {"file_num", "filing_date", "previous_accession"},
        "issuer": {"entity_type_other", "street2"},
        "offering": {
            "business_combination_clarification", "fees_clarification", "finders_fees_is_estimate",
            "is_debt", "is_equity", "is_mineral_property", "is_option_to_acquire", "is_other_type",
            "is_security_to_be_acquired", "is_tenant_in_common", "n_non_accredited", "other_type_desc",
            "revenue_range", "sale_yet_to_occur", "sales_amount_clarification",
            "sales_commissions_is_estimate", "total_offering_amount_num", "total_remaining_num",
        },
    },
    "0001635511-15-000001": {
        "schema": "X0707",
        "filing": {"file_num", "filing_date", "previous_accession"},
        "issuer": {"entity_type_other"},
        "offering": {
            "business_combination_clarification", "fees_clarification", "finders_fees_is_estimate",
            "gross_proceeds_used_is_estimate", "is_debt", "is_equity", "is_mineral_property",
            "is_option_to_acquire", "is_other_type", "is_security_to_be_acquired", "is_tenant_in_common",
            "n_non_accredited", "other_type_desc", "revenue_range", "sale_yet_to_occur",
            "sales_amount_clarification", "sales_commissions_is_estimate", "total_offering_amount_num",
            "total_remaining_num",
        },
    },
}


@pytest.mark.parametrize("accession", sorted(OLDER_SCHEMA_NONE_FIELDS))
def test_older_schema_none_fields(accession):
    expected = OLDER_SCHEMA_NONE_FIELDS[accession]
    f = load(accession)
    assert f.filing.schema_version == expected["schema"]
    assert none_fields(f.filing) == expected["filing"]
    assert none_fields(f.issuers[0]) == expected["issuer"]
    assert none_fields(f.offering) == expected["offering"]


def test_x0704_values():
    f = load("0000919574-10-003893")
    issuer = f.issuers[0]
    assert issuer.entity_name == "400 Capital Credit Opportunities Fund LP"
    assert issuer.phone == "(212) 612-3104"
    assert (issuer.year_of_inc_choice, issuer.year_of_inc_value) == ("withinFiveYears", 2009)
    o = f.offering
    assert o.fund_type == "Hedge Fund"
    assert o.exemptions == ("06", "3C", "3C.7")
    assert o.sale_date == "2010-03-01"
    assert o.min_investment_num == 250000
    assert o.total_offering_amount_is_indefinite is True
    assert o.total_amount_sold_num == 11850000
    assert o.n_investors == 7
    assert o.gross_proceeds_used_is_estimate is True
    (rp,) = f.related_persons
    assert rp.name_norm == "CHRIS HENTEMANN" and not rp.is_entity
    assert rp.clarification.startswith("Mr. Hentemann is the managing member of 400 Capital GP LLC")
    assert f.signatures[0].signature_name == "/s/ Chris Hentemann"
    assert f.signatures[0].date == "2010-06-15"


def test_x0707_values_and_reversed_entity_names():
    f = load("0001635511-15-000001")
    issuer = f.issuers[0]
    assert issuer.entity_name == "AQR OFFSHORE MULTI-STRATEGY FUND XIV, L.P."
    assert (issuer.state_or_country, issuer.jurisdiction) == ("E9", "CAYMAN ISLANDS")
    assert f.offering.min_investment_num == 5000000
    assert f.offering.total_amount_sold_num == 210415188
    assert f.offering.n_investors == 1
    assert len(f.related_persons) == 12
    # AQR files its entities with the name halves reversed: first "Management, LLC",
    # last "AQR Capital". Detection still works; the joined order is as filed.
    gp = f.related_persons[10]
    assert (gp.first, gp.last) == ("Management, LLC", "AQR Capital")
    assert gp.name_norm == "MANAGEMENT LLC AQR CAPITAL"
    assert gp.is_entity is True
    assert f.signatures[0].signer_norm == "BRADLEY D ASNESS"


def test_cik_hint_and_missing_elements():
    xml = b"<edgarSubmission><submissionType>D</submissionType><primaryIssuer/></edgarSubmission>"
    f = parse_formd(xml, "0000000001-26-000001", cik_hint="2158817")
    assert f.filing.cik == f.issuers[0].cik == "0002158817"
    assert f.filing.schema_version is None
    assert f.offering.fund_type is None and f.offering.exemptions == ()
    assert f.related_persons == () and f.recipients == () and f.signatures == ()


def test_empty_submission_does_not_raise():
    f = parse_formd(b"<edgarSubmission/>", "0000000001-26-000002")
    assert f.issuers == ()
    assert f.filing.cik is None and f.filing.submission_type is None


def test_namespaced_document():
    xml = (b'<edgarSubmission xmlns="http://www.sec.gov/edgar/formd">'
           b"<schemaVersion>X0708</schemaVersion><primaryIssuer><cik>123</cik></primaryIssuer>"
           b"</edgarSubmission>")
    f = parse_formd(xml, "0000000001-26-000003")
    assert f.filing.schema_version == "X0708"
    assert f.filing.cik == "0000000123"
