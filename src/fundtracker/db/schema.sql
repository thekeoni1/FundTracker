-- Raw tables, one row per source record (PLAN.md section 6).
-- Both loaders write here. Applied on every connect, so every statement is
-- idempotent. Booleans are 0, 1 or NULL; lists are JSON arrays; dates are ISO.
-- Column names match the fields of the dataclasses in model.py.

CREATE TABLE IF NOT EXISTS filing (
    accession           TEXT PRIMARY KEY,
    cik                 TEXT,
    filing_date         TEXT,
    submission_type     TEXT,
    previous_accession  TEXT,
    file_num            TEXT,
    schema_version      TEXT,
    test_or_live        TEXT,
    source              TEXT NOT NULL,
    loaded_at           TEXT NOT NULL   -- first load; kept when the filing is rewritten
);

CREATE TABLE IF NOT EXISTS issuer (
    accession              TEXT NOT NULL REFERENCES filing(accession) ON DELETE CASCADE,
    seq                    INTEGER NOT NULL,
    is_primary             INTEGER NOT NULL,
    cik                    TEXT,
    entity_name            TEXT,
    street1                TEXT,
    street2                TEXT,
    city                   TEXT,
    state_or_country       TEXT,
    state_or_country_desc  TEXT,
    zip                    TEXT,
    phone                  TEXT,
    jurisdiction           TEXT,
    entity_type            TEXT,
    entity_type_other      TEXT,
    year_of_inc_choice     TEXT,
    year_of_inc_value      INTEGER,
    previous_names         TEXT NOT NULL DEFAULT '[]',
    edgar_previous_names   TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (accession, seq)
);

CREATE TABLE IF NOT EXISTS offering (
    accession                           TEXT PRIMARY KEY REFERENCES filing(accession) ON DELETE CASCADE,
    industry_group                      TEXT,
    fund_type                           TEXT,
    is_40act                            INTEGER,
    revenue_range                       TEXT,
    nav_range                           TEXT,
    exemptions                          TEXT NOT NULL DEFAULT '[]',
    is_amendment                        INTEGER,
    sale_date                           TEXT,
    sale_yet_to_occur                   INTEGER,
    more_than_one_year                  INTEGER,
    is_equity                           INTEGER,
    is_debt                             INTEGER,
    is_option_to_acquire                INTEGER,
    is_security_to_be_acquired          INTEGER,
    is_pooled_fund                      INTEGER,
    is_tenant_in_common                 INTEGER,
    is_mineral_property                 INTEGER,
    is_other_type                       INTEGER,
    other_type_desc                     TEXT,
    is_business_combination             INTEGER,
    business_combination_clarification  TEXT,
    min_investment_text                 TEXT,
    min_investment_num                  INTEGER,
    total_offering_amount_text          TEXT,
    total_offering_amount_num           INTEGER,
    total_offering_amount_is_indefinite INTEGER,
    total_amount_sold_text              TEXT,
    total_amount_sold_num               INTEGER,
    total_amount_sold_is_indefinite     INTEGER,
    total_remaining_text                TEXT,
    total_remaining_num                 INTEGER,
    total_remaining_is_indefinite       INTEGER,
    sales_amount_clarification          TEXT,
    has_non_accredited                  INTEGER,
    n_non_accredited                    INTEGER,
    n_investors                         INTEGER,
    sales_commissions_text              TEXT,
    sales_commissions_num               INTEGER,
    sales_commissions_is_estimate       INTEGER,
    finders_fees_text                   TEXT,
    finders_fees_num                    INTEGER,
    finders_fees_is_estimate            INTEGER,
    fees_clarification                  TEXT,
    gross_proceeds_used_text            TEXT,
    gross_proceeds_used_num             INTEGER,
    gross_proceeds_used_is_estimate     INTEGER,
    gross_proceeds_clarification        TEXT,
    authorized_representative           INTEGER
);

CREATE TABLE IF NOT EXISTS related_person (
    accession              TEXT NOT NULL REFERENCES filing(accession) ON DELETE CASCADE,
    seq                    INTEGER NOT NULL,
    first                  TEXT,
    middle                 TEXT,
    last                   TEXT,
    street1                TEXT,
    street2                TEXT,
    city                   TEXT,
    state_or_country       TEXT,
    state_or_country_desc  TEXT,
    zip                    TEXT,
    relationships          TEXT NOT NULL DEFAULT '[]',
    clarification          TEXT,
    name_norm              TEXT,
    is_entity              INTEGER NOT NULL,
    PRIMARY KEY (accession, seq)
);

CREATE TABLE IF NOT EXISTS recipient (
    accession              TEXT NOT NULL REFERENCES filing(accession) ON DELETE CASCADE,
    seq                    INTEGER NOT NULL,
    name                   TEXT,
    crd                    TEXT,
    bd_name                TEXT,
    bd_crd                 TEXT,
    street1                TEXT,
    street2                TEXT,
    city                   TEXT,
    state_or_country       TEXT,
    state_or_country_desc  TEXT,
    zip                    TEXT,
    states                 TEXT NOT NULL DEFAULT '[]',
    foreign_solicitation   INTEGER,
    PRIMARY KEY (accession, seq)
);

CREATE TABLE IF NOT EXISTS signature (
    accession       TEXT NOT NULL REFERENCES filing(accession) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    issuer_name     TEXT,
    signature_name  TEXT,
    signer          TEXT,
    title           TEXT,
    date            TEXT,
    signer_norm     TEXT,
    PRIMARY KEY (accession, seq)
);

CREATE TABLE IF NOT EXISTS ingest_log (
    source       TEXT NOT NULL,   -- e.g. daily, dataset
    key          TEXT NOT NULL,   -- e.g. 2026-10-28, 2026q3
    status       TEXT NOT NULL,
    n_rows       INTEGER,
    started_at   TEXT,
    finished_at  TEXT,
    error        TEXT,
    PRIMARY KEY (source, key)
);

CREATE INDEX IF NOT EXISTS filing_filing_date ON filing(filing_date);
CREATE INDEX IF NOT EXISTS filing_cik ON filing(cik);
CREATE INDEX IF NOT EXISTS offering_fund_type ON offering(fund_type, is_amendment);
CREATE INDEX IF NOT EXISTS related_person_name_norm ON related_person(name_norm);
CREATE INDEX IF NOT EXISTS signature_signer_norm ON signature(signer_norm);
CREATE INDEX IF NOT EXISTS issuer_cik ON issuer(cik);
CREATE INDEX IF NOT EXISTS issuer_phone ON issuer(phone);
CREATE INDEX IF NOT EXISTS issuer_address ON issuer(zip, street1);

-- Derived tables (PLAN.md section 6), refilled by derive.run on every run.
-- derive deletes and reinserts their rows and never drops them, so indexes
-- added by hand survive. review is user state and derive never touches it.

CREATE TABLE IF NOT EXISTS fund (
    cik                                 TEXT PRIMARY KEY,
    name                                TEXT,
    family_stem                         TEXT,
    fund_type                           TEXT,      -- classification fields from the latest filing
    industry_group                      TEXT,
    first_filing_date                   TEXT,
    first_accession                     TEXT,
    latest_filing_date                  TEXT,
    latest_accession                    TEXT,
    n_filings                           INTEGER NOT NULL,
    exemptions                          TEXT NOT NULL DEFAULT '[]',   -- normalised, 06 as 06b
    state                               TEXT,
    country                             TEXT,
    is_hedge_candidate                  INTEGER NOT NULL,
    candidate_reason                    TEXT NOT NULL DEFAULT '',
    -- offering terms from the first filing, the notice that launched the fund
    sale_yet_to_occur                   INTEGER,
    total_amount_sold_num               INTEGER,
    n_investors                         INTEGER,
    min_investment_num                  INTEGER,
    total_offering_amount_num           INTEGER,
    total_offering_amount_is_indefinite INTEGER,
    year_of_inc_choice                  TEXT,
    year_of_inc_value                   INTEGER
);

CREATE TABLE IF NOT EXISTS link_key (
    kind        TEXT NOT NULL,   -- entity_stem, entity_tokens, signer, phone, address
    key         TEXT NOT NULL,
    cik         TEXT NOT NULL,
    first_date  TEXT,            -- earliest filing of this CIK that carries the key
    over_cap    INTEGER NOT NULL DEFAULT 0,  -- held by more CIKs than derive.KEY_CAPS allows
    display     TEXT             -- entity and signer keys: the name as this CIK's filings most often write it
);                               -- one row per (kind, key, cik); derive writes each once

CREATE TABLE IF NOT EXISTS manager (
    manager_id       TEXT PRIMARY KEY,   -- m plus the CIK of the member with the earliest first filing
    display_name     TEXT,
    key_kind         TEXT,               -- kind of the key that gave the cluster its first edge
    first_seen_date  TEXT,
    n_funds          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fund_manager (
    cik         TEXT PRIMARY KEY,
    manager_id  TEXT NOT NULL,
    evidence    TEXT                     -- the key that linked this CIK, as kind:key
);

CREATE TABLE IF NOT EXISTS launch (
    launch_id        TEXT PRIMARY KEY,   -- accession of the earliest D filing in the launch
    manager_id       TEXT NOT NULL,
    display_name     TEXT,
    anchor_cik       TEXT,               -- the fund that filed launch_id
    fund_type        TEXT,
    window_start     TEXT NOT NULL,
    window_end       TEXT NOT NULL,
    fund_ciks        TEXT NOT NULL DEFAULT '[]',
    newness_score    INTEGER,
    newness_reasons  TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS review (
    launch_id   TEXT PRIMARY KEY,        -- a launch_id, or a CIK for a fund-level note
    status      TEXT NOT NULL DEFAULT 'new',   -- new, reviewed, watching, contacted, ignore
    notes       TEXT,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS link_key_kind_key ON link_key(kind, key);
CREATE INDEX IF NOT EXISTS fund_first_filing_date ON fund(first_filing_date);
CREATE INDEX IF NOT EXISTS fund_candidate ON fund(is_hedge_candidate, fund_type);
CREATE INDEX IF NOT EXISTS fund_manager_manager_id ON fund_manager(manager_id);
CREATE INDEX IF NOT EXISTS launch_window_start ON launch(window_start);
CREATE INDEX IF NOT EXISTS launch_manager_id ON launch(manager_id);
