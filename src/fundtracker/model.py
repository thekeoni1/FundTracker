"""Dataclasses for one Form D filing, mirroring the raw tables in PLAN.md section 6.

Both loaders produce these: edgar/formd_xml.py from primary_doc.xml and, in
WP2, the quarterly data set reader from TSV rows. The repository writes them
without knowing which one did.

Conventions:
- Text is kept as filed, stripped of surrounding whitespace. An element that
  is missing or empty is None. The literal "None" that EDGAR uses for absent
  CRD numbers and previous names is also None.
- Dates are ISO strings (YYYY-MM-DD).
- Booleans are True, False, or None when the filing does not say.
- Amounts keep the filed text in *_text and add *_num (an int, None when
  Indefinite or unparseable) and, where EDGAR allows it, *_is_indefinite.
- Lists are tuples so instances stay hashable and comparable.
- seq numbers child rows from 1 in document order within one accession.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Filing:
    accession: str
    cik: str | None  # primary issuer CIK, 10 digits zero padded
    filing_date: str | None  # from the index or data set; primary_doc.xml has none
    submission_type: str | None  # D or D/A
    previous_accession: str | None = None
    file_num: str | None = None
    schema_version: str | None = None
    test_or_live: str | None = None
    source: str = "xml"  # xml or dataset


@dataclass(frozen=True)
class Issuer:
    accession: str
    seq: int
    is_primary: bool
    cik: str | None
    entity_name: str | None
    street1: str | None = None
    street2: str | None = None
    city: str | None = None
    state_or_country: str | None = None
    state_or_country_desc: str | None = None
    zip: str | None = None
    phone: str | None = None
    jurisdiction: str | None = None
    entity_type: str | None = None
    entity_type_other: str | None = None
    year_of_inc_choice: str | None = None  # withinFiveYears | overFiveYears | yetToBeFormed
    year_of_inc_value: int | None = None
    previous_names: tuple[str, ...] = ()
    edgar_previous_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class Offering:
    accession: str
    industry_group: str | None = None
    fund_type: str | None = None
    is_40act: bool | None = None
    revenue_range: str | None = None
    nav_range: str | None = None
    exemptions: tuple[str, ...] = ()
    is_amendment: bool | None = None
    sale_date: str | None = None
    sale_yet_to_occur: bool | None = None
    more_than_one_year: bool | None = None
    is_equity: bool | None = None
    is_debt: bool | None = None
    is_option_to_acquire: bool | None = None
    is_security_to_be_acquired: bool | None = None
    is_pooled_fund: bool | None = None
    is_tenant_in_common: bool | None = None
    is_mineral_property: bool | None = None
    is_other_type: bool | None = None
    other_type_desc: str | None = None
    is_business_combination: bool | None = None
    business_combination_clarification: str | None = None
    min_investment_text: str | None = None
    min_investment_num: int | None = None
    total_offering_amount_text: str | None = None
    total_offering_amount_num: int | None = None
    total_offering_amount_is_indefinite: bool | None = None
    total_amount_sold_text: str | None = None
    total_amount_sold_num: int | None = None
    total_amount_sold_is_indefinite: bool | None = None
    total_remaining_text: str | None = None
    total_remaining_num: int | None = None
    total_remaining_is_indefinite: bool | None = None
    sales_amount_clarification: str | None = None
    has_non_accredited: bool | None = None
    n_non_accredited: int | None = None
    n_investors: int | None = None
    sales_commissions_text: str | None = None
    sales_commissions_num: int | None = None
    sales_commissions_is_estimate: bool | None = None
    finders_fees_text: str | None = None
    finders_fees_num: int | None = None
    finders_fees_is_estimate: bool | None = None
    fees_clarification: str | None = None
    gross_proceeds_used_text: str | None = None
    gross_proceeds_used_num: int | None = None
    gross_proceeds_used_is_estimate: bool | None = None
    gross_proceeds_clarification: str | None = None
    authorized_representative: bool | None = None


@dataclass(frozen=True)
class RelatedPerson:
    accession: str
    seq: int
    first: str | None
    middle: str | None
    last: str | None
    street1: str | None = None
    street2: str | None = None
    city: str | None = None
    state_or_country: str | None = None
    state_or_country_desc: str | None = None
    zip: str | None = None
    relationships: tuple[str, ...] = ()
    clarification: str | None = None
    name_norm: str | None = None  # derived: join_name, upper case, no punctuation
    is_entity: bool = False  # derived: is_entity


@dataclass(frozen=True)
class Recipient:
    accession: str
    seq: int
    name: str | None
    crd: str | None = None
    bd_name: str | None = None
    bd_crd: str | None = None
    street1: str | None = None
    street2: str | None = None
    city: str | None = None
    state_or_country: str | None = None
    state_or_country_desc: str | None = None
    zip: str | None = None
    states: tuple[str, ...] = ()  # state codes, or values such as "All States"
    foreign_solicitation: bool | None = None


@dataclass(frozen=True)
class Signature:
    accession: str
    seq: int
    issuer_name: str | None
    signature_name: str | None
    signer: str | None
    title: str | None = None
    date: str | None = None
    signer_norm: str | None = None  # derived: upper case, no punctuation


@dataclass(frozen=True)
class FormDFiling:
    """Everything one Form D says. issuers[0] is the primary issuer."""

    filing: Filing
    offering: Offering
    issuers: tuple[Issuer, ...] = ()
    related_persons: tuple[RelatedPerson, ...] = ()
    recipients: tuple[Recipient, ...] = ()
    signatures: tuple[Signature, ...] = ()

    @property
    def accession(self):
        return self.filing.accession

    @property
    def primary_issuer(self):
        return self.issuers[0] if self.issuers else None
