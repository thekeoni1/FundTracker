"""Parse a Form D primary_doc.xml into a FormDFiling.

The element hierarchy is in docs/edgar-formd-reference.md section 2. Every
element is treated as optional: a missing or empty one becomes None (or an
empty tuple for lists), so older schema versions and sparse filings parse
without special cases. Only XML that is not well formed raises.
"""

import dataclasses
import xml.etree.ElementTree as ET

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


def parse_formd(xml_bytes, accession, cik_hint=None, filing_date=None):
    """Parse primary_doc.xml bytes.

    accession comes from the index row, since the document does not carry it.
    cik_hint fills the primary issuer CIK when the document lacks one.
    filing_date (any format parse_date accepts) also comes from the index.
    """
    root = ET.fromstring(xml_bytes)
    _strip_namespaces(root)

    issuers = []
    primary = root.find("primaryIssuer")
    if primary is not None:
        issuers.append(_issuer(primary, accession, 1, True))
    for el in root.findall("issuerList/issuer"):
        issuers.append(_issuer(el, accession, len(issuers) + 1, False))
    if cik_hint and issuers and issuers[0].cik is None:
        issuers[0] = dataclasses.replace(issuers[0], cik=_cik(cik_hint))
    primary_cik = issuers[0].cik if issuers else _cik(cik_hint)

    od = root.find("offeringData")
    if od is None:
        od = ET.Element("offeringData")

    filing = Filing(
        accession=accession,
        cik=primary_cik,
        filing_date=n.parse_date(filing_date),
        submission_type=_text(root, "submissionType"),
        previous_accession=_text(od, "typeOfFiling/newOrAmendment/previousAccessionNumber"),
        schema_version=_text(root, "schemaVersion"),
        test_or_live=_text(root, "testOrLive"),
        source="xml",
    )
    return FormDFiling(
        filing=filing,
        offering=_offering(od, accession),
        issuers=tuple(issuers),
        related_persons=tuple(
            _related_person(el, accession, i)
            for i, el in enumerate(root.findall("relatedPersonsList/relatedPersonInfo"), start=1)
        ),
        recipients=tuple(
            _recipient(el, accession, i)
            for i, el in enumerate(od.findall("salesCompensationList/recipient"), start=1)
        ),
        signatures=tuple(
            _signature(el, accession, i)
            for i, el in enumerate(od.findall("signatureBlock/signature"), start=1)
        ),
    )


def _strip_namespaces(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _text(el, path):
    return n.none_to_null(el.findtext(path)) if el is not None else None


def _bool(el, path):
    return n.parse_bool(el.findtext(path)) if el is not None else None


def _values(el, path):
    return tuple(v for v in (n.none_to_null(x.text) for x in el.findall(path)) if v is not None)


def _list_values(el, path):
    """Text of every child of the list element at path, whatever the child is called.

    Previous-name lists use <value> in most filings and <previousName> in others.
    """
    return _values(el, f"{path}/*")


def _cik(text):
    text = n.none_to_null(str(text) if text is not None else None)
    return text.zfill(10) if text and text.isdigit() else text


def _address(el, prefix):
    a = el.find(prefix)
    return {
        "street1": _text(a, "street1"),
        "street2": n.street2(_text(a, "street1"), _text(a, "street2")),
        "city": _text(a, "city"),
        "state_or_country": _text(a, "stateOrCountry"),
        "state_or_country_desc": _text(a, "stateOrCountryDescription"),
        "zip": _text(a, "zipCode"),
    }


def _issuer(el, accession, seq, is_primary):
    choice = value = None
    year = el.find("yearOfInc")
    if year is not None:
        for option in ("withinFiveYears", "overFiveYears", "yetToBeFormed"):
            if n.parse_bool(year.findtext(option)):
                choice = option
                break
        value = n.parse_int(year.findtext("value"))
    return Issuer(
        accession=accession,
        seq=seq,
        is_primary=is_primary,
        cik=_cik(el.findtext("cik")),
        entity_name=_text(el, "entityName"),
        **_address(el, "issuerAddress"),
        phone=_text(el, "issuerPhoneNumber"),
        jurisdiction=_text(el, "jurisdictionOfInc"),
        entity_type=_text(el, "entityType"),
        entity_type_other=_text(el, "entityTypeOtherDesc"),
        year_of_inc_choice=choice,
        year_of_inc_value=value,
        previous_names=_list_values(el, "issuerPreviousNameList"),
        edgar_previous_names=_list_values(el, "edgarPreviousNameList"),
    )


def _related_person(el, accession, seq):
    first = _text(el, "relatedPersonName/firstName")
    middle = _text(el, "relatedPersonName/middleName")
    last = _text(el, "relatedPersonName/lastName")
    joined = n.join_name(first, middle, last)
    return RelatedPerson(
        accession=accession,
        seq=seq,
        first=first,
        middle=middle,
        last=last,
        **_address(el, "relatedPersonAddress"),
        relationships=_values(el, "relatedPersonRelationshipList/relationship"),
        clarification=_text(el, "relationshipClarification"),
        name_norm=joined,
        is_entity=n.is_entity(first, last, joined),
    )


def _recipient(el, accession, seq):
    states_list = el.find("statesOfSolicitationList")
    states = ()
    if states_list is not None:
        # Sorted and unique, as the data sets store them; order carries no meaning.
        states = tuple(sorted({
            v for v in (n.none_to_null(c.text) for c in states_list if c.tag in ("state", "value"))
            if v is not None
        }))
    return Recipient(
        accession=accession,
        seq=seq,
        name=_text(el, "recipientName"),
        crd=_text(el, "recipientCRDNumber"),
        bd_name=_text(el, "associatedBDName"),
        bd_crd=_text(el, "associatedBDCRDNumber"),
        **_address(el, "recipientAddress"),
        states=states,
        foreign_solicitation=_bool(el, "foreignSolicitation"),
    )


def _signature(el, accession, seq):
    signer = _text(el, "nameOfSigner")
    return Signature(
        accession=accession,
        seq=seq,
        issuer_name=_text(el, "issuerName"),
        signature_name=_text(el, "signatureName"),
        signer=signer,
        title=_text(el, "signatureTitle"),
        date=n.parse_date(el.findtext("signatureDate")),
        signer_norm=n.norm_text(signer) or None,
    )


def _amount(od, path, name):
    text = _text(od, path)
    num, indefinite = n.parse_amount(text)
    return {f"{name}_text": text, f"{name}_num": num, f"{name}_is_indefinite": indefinite}


def _money(od, path, name):
    text = _text(od, f"{path}/dollarAmount")
    return {
        f"{name}_text": text,
        f"{name}_num": n.parse_int(text),
        f"{name}_is_estimate": _bool(od, f"{path}/isEstimate"),
    }


def _offering(od, accession):
    sec = "typesOfSecuritiesOffered/"
    min_text = _text(od, "minimumInvestmentAccepted")
    return Offering(
        accession=accession,
        industry_group=_text(od, "industryGroup/industryGroupType"),
        fund_type=_text(od, "industryGroup/investmentFundInfo/investmentFundType"),
        is_40act=_bool(od, "industryGroup/investmentFundInfo/is40Act"),
        revenue_range=_text(od, "issuerSize/revenueRange"),
        nav_range=_text(od, "issuerSize/aggregateNetAssetValueRange"),
        exemptions=_values(od, "federalExemptionsExclusions/item"),
        is_amendment=_bool(od, "typeOfFiling/newOrAmendment/isAmendment"),
        sale_date=n.parse_date(od.findtext("typeOfFiling/dateOfFirstSale/value")),
        sale_yet_to_occur=_bool(od, "typeOfFiling/dateOfFirstSale/yetToOccur"),
        more_than_one_year=_bool(od, "durationOfOffering/moreThanOneYear"),
        is_equity=_bool(od, sec + "isEquityType"),
        is_debt=_bool(od, sec + "isDebtType"),
        is_option_to_acquire=_bool(od, sec + "isOptionToAcquireType"),
        is_security_to_be_acquired=_bool(od, sec + "isSecurityToBeAcquiredType"),
        is_pooled_fund=_bool(od, sec + "isPooledInvestmentFundType"),
        is_tenant_in_common=_bool(od, sec + "isTenantInCommonType"),
        is_mineral_property=_bool(od, sec + "isMineralPropertyType"),
        is_other_type=_bool(od, sec + "isOtherType"),
        other_type_desc=_text(od, sec + "descriptionOfOtherType"),
        is_business_combination=_bool(
            od, "businessCombinationTransaction/isBusinessCombinationTransaction"
        ),
        business_combination_clarification=_text(
            od, "businessCombinationTransaction/clarificationOfResponse"
        ),
        min_investment_text=min_text,
        min_investment_num=n.parse_int(min_text),
        **_amount(od, "offeringSalesAmounts/totalOfferingAmount", "total_offering_amount"),
        **_amount(od, "offeringSalesAmounts/totalAmountSold", "total_amount_sold"),
        **_amount(od, "offeringSalesAmounts/totalRemaining", "total_remaining"),
        sales_amount_clarification=_text(od, "offeringSalesAmounts/clarificationOfResponse"),
        has_non_accredited=_bool(od, "investors/hasNonAccreditedInvestors"),
        n_non_accredited=n.parse_int(od.findtext("investors/numberNonAccreditedInvestors")),
        n_investors=n.parse_int(od.findtext("investors/totalNumberAlreadyInvested")),
        **_money(od, "salesCommissionsFindersFees/salesCommissions", "sales_commissions"),
        **_money(od, "salesCommissionsFindersFees/findersFees", "finders_fees"),
        fees_clarification=_text(od, "salesCommissionsFindersFees/clarificationOfResponse"),
        **_money(od, "useOfProceeds/grossProceedsUsed", "gross_proceeds_used"),
        gross_proceeds_clarification=_text(od, "useOfProceeds/clarificationOfResponse"),
        authorized_representative=_bool(od, "signatureBlock/authorizedRepresentative"),
    )
