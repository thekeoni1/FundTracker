# EDGAR Form D data-source reference

Facts verified against live EDGAR on 2026-10-29. Items marked **verify** were not checked and must be confirmed by the implementer before relying on them.

## 1. Access rules (all hosts)

- Max **10 requests/second**. Run at 5/s to leave headroom. SEC may throttle lower at any time.
- Every request must carry a `User-Agent` of the form `Company Name contact@domain.com`. Generic or missing agents get HTTP 403.
- Send `Accept-Encoding: gzip, deflate`.
- Filings are accepted Mon-Fri 06:00-22:00 ET. Indexes rebuild nightly starting around 22:00 ET.
- `/Archives/edgar/data/` is not browsable, but each CIK folder and accession folder is, and each exposes `index.json`, `index.xml`, `index.html`.
- Applies to `www.sec.gov`, `data.sec.gov`, `efts.sec.gov`.

## 2. A Form D filing on disk

Folder: `https://www.sec.gov/Archives/edgar/data/{cik_no_leading_zeros}/{accession_no_dashes}/`

Example checked: `https://www.sec.gov/Archives/edgar/data/2158817/000215881726000001/`

`index.json` lists:
- `primary_doc.xml` (the structured Form D, about 6 KB)
- `{accession}-index.html`, `{accession}-index-headers.html`
- `{accession}.txt` (full SGML submission wrapper)

Only `primary_doc.xml` is needed. The accession prefix can be the filer agent's CIK rather than the issuer's (e.g. `0000904454-26-000556` filed for CIK 2161368), so always build the URL from the CIK in the index row plus the accession number.

### primary_doc.xml element hierarchy (schema X0708)

```
edgarSubmission
  schemaVersion                  X0708
  submissionType                 D | D/A
  testOrLive                     LIVE
  primaryIssuer
    cik                          10-digit, zero padded
    entityName
    issuerAddress/{street1,street2,city,stateOrCountry,stateOrCountryDescription,zipCode}
    issuerPhoneNumber
    jurisdictionOfInc
    issuerPreviousNameList/value   ("None" or repeated)
    edgarPreviousNameList/value
    entityType                   Limited Partnership | Limited Liability Company | Corporation | Other ...
    entityTypeOtherDesc          (only when Other)
    yearOfInc
      withinFiveYears | overFiveYears | yetToBeFormed   (boolean true)
      value                      year (when withinFiveYears)
  issuerList/issuer ...          (co-issuers; same shape as primaryIssuer; optional, repeated)
  relatedPersonsList/relatedPersonInfo  (repeated)
    relatedPersonName/{firstName,middleName,lastName}
    relatedPersonAddress/{...}
    relatedPersonRelationshipList/relationship   (repeated: Executive Officer | Director | Promoter)
    relationshipClarification
  offeringData
    industryGroup
      industryGroupType          Pooled Investment Fund | Other | Other Technology | Commercial | ...
      investmentFundInfo         (only for Pooled Investment Fund)
        investmentFundType       Hedge Fund | Private Equity Fund | Venture Capital Fund | Other Investment Fund
        is40Act                  true|false
    issuerSize
      revenueRange | aggregateNetAssetValueRange   (funds use NAV range; usually "Decline to Disclose")
    federalExemptionsExclusions/item   (repeated: 04 | 04.1 | 04.2 | 04.3 | 05 | 06 | 06b | 06c | 3C | 3C.1 | 3C.7 | 4a5 ...)
    typeOfFiling
      newOrAmendment/isAmendment            true|false
      newOrAmendment/previousAccessionNumber (amendments only)
      dateOfFirstSale/value | dateOfFirstSale/yetToOccur
    durationOfOffering/moreThanOneYear
    typesOfSecuritiesOffered/{isEquityType,isDebtType,isOptionToAcquireType,isSecurityToBeAcquiredType,isPooledInvestmentFundType,isTenantInCommonType,isMineralPropertyType,isOtherType,descriptionOfOtherType}
    businessCombinationTransaction/{isBusinessCombinationTransaction,clarificationOfResponse}
    minimumInvestmentAccepted    integer dollars
    salesCompensationList/recipient (repeated)
      recipientName, recipientCRDNumber, associatedBDName, associatedBDCRDNumber,
      recipientAddress/{...}, statesOfSolicitationList/{state|value}, foreignSolicitation, descriptionOfCompensation
    offeringSalesAmounts/{totalOfferingAmount,totalAmountSold,totalRemaining,clarificationOfResponse}
                                 (amounts are integers OR the literal "Indefinite")
    investors/{hasNonAccreditedInvestors,numberNonAccreditedInvestors,totalNumberAlreadyInvested}
    salesCommissionsFindersFees/{salesCommissions/dollarAmount,salesCommissions/isEstimate,
                                 findersFees/dollarAmount,findersFees/isEstimate,clarificationOfResponse}
    useOfProceeds/{grossProceedsUsed/dollarAmount,grossProceedsUsed/isEstimate,clarificationOfResponse}
    signatureBlock
      authorizedRepresentative   true|false
      signature (repeated)/{issuerName,signatureName,nameOfSigner,signatureTitle,signatureDate}
```

Quirks seen in real filings (the first four confirmed by WP1's fixture sampling on 2026-10-29):
- `primary_doc.xml` carries no filing date. Take it from the daily index row or the data set's FILING_DATE and pass it to the parser.
- Entity related persons are encoded three ways: `firstName="-"`, `lastName="VSC Ventures Fund I GP LLC"`; or `firstName="Axiaris Capital Management"`, `lastName="LLC"`; or reversed halves, `firstName="Management, LLC"`, `lastName="AQR Capital"` (AQR, 2015). Normalisation must join all three name parts, detect entity suffixes, and compare entity stems as sorted token sets so the reversed form still matches.
- Placeholders vary in case: `None`, `none`, `N/A`, `n/a` and `-` all mean empty, in previous-name lists, entity first names and recipient names. Treat them case-insensitively.
- Previous-name lists use either `<value>` children or `<previousName>` children.
- Schema versions by filing year: X0401 (2009), X0704 (2010 to 2011), X0705 (2012), X0706 (2013), X0707 (2015 to 2017), X0708 (2018 onward; 2014 not sampled). No older version uses an element or nesting that X0708 lacks; files differ only in which optional elements the filer filled. One X0708 parser handles all of them.
- Exemption codes: `06b` = Rule 506(b); `06c` = Rule 506(c) (general solicitation permitted); `06` alone = Rule 506 as filed before the 2013 rule change, treat as 506(b); `3C` + `3C.1` = Investment Company Act 3(c)(1) fund (max 100 beneficial owners); `3C` + `3C.7` = 3(c)(7) fund (qualified purchasers only). Hedge funds almost always carry 3C.1 or 3C.7.
- The daily index lists a co-issued filing once per issuer CIK, so the same accession appears on several rows. De-duplicate on accession before fetching.

### Worked example: a brand-new hedge fund (filed 2026-10-28)

Axiaris Capital Management Fund, LP, CIK 2158817, accession 0002158817-26-000001.

Signals: `submissionType=D`, `investmentFundType=Hedge Fund`, exemptions `06c, 3C, 3C.1`, `yearOfInc withinFiveYears` with value 2026, `dateOfFirstSale yetToOccur=true`, `totalAmountSold=0`, `totalNumberAlreadyInvested=0`, `minimumInvestmentAccepted=100000`, `totalOfferingAmount=25000000`. Related persons: one individual (Executive Officer) and one entity "Axiaris Capital Management LLC" (Executive Officer). Signer title "Managing Member of the General Partner". The submissions API shows this CIK has exactly one filing ever. This is the archetype the app should surface.

## 3. Discovery path A: daily form index (primary incremental feed)

Folder listing: `https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{n}/index.json`

Per business day there are `form.YYYYMMDD.idx`, `master.YYYYMMDD.idx`, `company.YYYYMMDD.idx`, `crawler.YYYYMMDD.idx`, `sitemap.YYYYMMDD.xml`. No compressed variants were observed in the daily folder.

`form.YYYYMMDD.idx` layout: a text header (description, "Last Data Received", comments, FTP line, blank lines, a column header line, then a line of dashes), followed by data rows sorted by form type. All `D` rows are contiguous, as are all `D/A` rows. Columns are whitespace padded:

```
Form Type   Company Name                          CIK       Date Filed  File Name
D           Axiaris Capital Management Fund, LP   2158817   20261028    edgar/data/2158817/0002158817-26-000001.txt
```

Parse by skipping to the dashes line, then reading each row from the right: the last field is the file name, then date filed, then CIK, and the remainder on the left is form type followed by company name. Do not slice by header offsets: the header text has not changed since 2009, but from 2025 the Form Type column widened from 12 to 17 characters, so header offsets mis-slice current files (found by WP3, confirmed on 17 real indexes from 2009 to 2026). Company names contain single spaces, so never split on a single space. The `File Name` path gives CIK and accession; replace the trailing `.txt` with `/primary_doc.xml` after removing dashes from the accession to reach the folder.

Volume: 2026 Q3 data set shows 10,291 `D` and 5,661 `D/A` for the quarter, roughly 160 new notices per business day across all industries, of which about 8 per day are `Hedge Fund` new notices.

Availability: the day's file appears after the nightly index build (around 22:00 ET or later). An ingest run at 06:00 local the next morning is safe.

Missing days: a weekend, a market holiday (EDGAR was closed on 2026-10-12, Columbus Day) or a day whose index is not yet built does not return 404. EDGAR answers HTTP 403 with `Content-Type: application/xml` and an S3 style body beginning `<Error><Code>AccessDenied</Code>`. This is distinct from the rate-limit 403, which carries an HTML "Undeclared Automated Tool" page; tell them apart by the body, never by status alone, and do not retry the AccessDenied form. The quarter folder's `index.json` listing omits the missing day's files, which is the cheap way to audit a `no_index` weekday if a transient AccessDenied is ever suspected (found by WP3 on 2026-10-30).

## 4. Discovery path B: "Latest Filings" Atom feed (intraday, optional)

`https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=D&count=100&output=atom` (add `&start=100`, `&start=200` to page; **verify** the maximum depth).

- `type=D` matches by prefix, so the feed mixes `D`, `D/A`, `DEF 14A`, `DEFA14A`, `DFAN14A`. Filter on `category/@term` being exactly `D` or `D/A`.
- One filing can appear several times (one entry per CIK or role). De-duplicate on accession number taken from `id` (`urn:tag:sec.gov,2008:accession-number=...`).
- `title` format: `D - Company Name (0001234567) (Filer)`.
- `summary` HTML includes "Item 3C.1" or "Item 3C.7" for funds, a handy pre-filter before fetching XML.
- Suitable for a "refresh now" button. The nightly index is the source of truth.

## 5. Backfill path: quarterly Form D data sets

Page: `https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets`

Zip URL patterns (two prefixes and two file-name shapes in use; scrape the page rather than hard-coding):
- `https://www.sec.gov/files/datastandardsinnovation/data/form-d-data-sets/2026q3_d.zip` (2026 Q2, Q3)
- `https://www.sec.gov/files/structureddata/data/form-d-data-sets/2026q1_d.zip` (2026 Q1 and earlier)
- `{yyyy}q{n}_d_0.zip` for 2008 Q2 through 2013 Q4; the link regex must accept an optional `_N` suffix or it misses 22 quarters.

Published quarterly, a few days after quarter end (the 2026 Q3 zip was built 2026-10-02). Filings after 17:30 ET on the quarter's last business day roll into the next quarter. Earliest quarter: 2008 Q1. As of 2026-10-29 there are 75 quarters, 778,966 filings in total (confirmed by WP2's full backfill).

Older-quarter differences found by WP2:
- 2008 Q2 has no RECIPIENTS.tsv. Treat a missing child table as zero rows; only FORMDSUBMISSION and OFFERING are required.
- Older quarters write FILING_DATE as an ISO timestamp (`2010-06-15 12:23:18`) rather than `DD-MON-YYYY`.
- The data set sometimes copies STREET1 into an otherwise empty STREET2 (5 of 6 rows checked against their XML). Dropping a street2 that repeats street1 makes both sources agree.
- Recipient state lists are stored sorted in the TSV. Sort and de-duplicate them from the XML too.

Zip contents (2026q3_d.zip: 3.5 MB compressed, 21 MB raw), all tab-separated UTF-8 with an uppercase header row, inside a folder `2026Q3_d/`:

| File | Rows (2026 Q3) | Columns |
|---|---|---|
| FORMDSUBMISSION.tsv | 15,952 | ACCESSIONNUMBER, FILE_NUM, FILING_DATE, SIC_CODE, SCHEMAVERSION, SUBMISSIONTYPE, TESTORLIVE, OVER100PERSONSFLAG, OVER100ISSUERFLAG |
| ISSUERS.tsv | 16,160 | ACCESSIONNUMBER, IS_PRIMARYISSUER_FLAG, ISSUER_SEQ_KEY, CIK, ENTITYNAME, STREET1, STREET2, CITY, STATEORCOUNTRY, STATEORCOUNTRYDESCRIPTION, ZIPCODE, ISSUERPHONENUMBER, JURISDICTIONOFINC, ISSUER_PREVIOUSNAME_1..3, EDGAR_PREVIOUSNAME_1..3, ENTITYTYPE, ENTITYTYPEOTHERDESC, YEAROFINC_TIMESPAN_CHOICE, YEAROFINC_VALUE_ENTERED |
| OFFERING.tsv | 15,952 | ACCESSIONNUMBER, INDUSTRYGROUPTYPE, INVESTMENTFUNDTYPE, IS40ACT, REVENUERANGE, AGGREGATENETASSETVALUERANGE, FEDERALEXEMPTIONS_ITEMS_LIST, ISAMENDMENT, PREVIOUSACCESSIONNUMBER, SALE_DATE, YETTOOCCUR, MORETHANONEYEAR, ISEQUITYTYPE, ISDEBTTYPE, ISOPTIONTOACQUIRETYPE, ISSECURITYTOBEACQUIREDTYPE, ISPOOLEDINVESTMENTFUNDTYPE, ISTENANTINCOMMONTYPE, ISMINERALPROPERTYTYPE, ISOTHERTYPE, DESCRIPTIONOFOTHERTYPE, ISBUSINESSCOMBINATIONTRANS, BUSCOMBCLARIFICATIONOFRESP, MINIMUMINVESTMENTACCEPTED, OVER100RECIPIENTFLAG, TOTALOFFERINGAMOUNT, TOTALAMOUNTSOLD, TOTALREMAINING, SALESAMTCLARIFICATIONOFRESP, HASNONACCREDITEDINVESTORS, NUMBERNONACCREDITEDINVESTORS, TOTALNUMBERALREADYINVESTED, SALESCOMM_DOLLARAMOUNT, SALESCOMM_ISESTIMATE, FINDERSFEE_DOLLARAMOUNT, FINDERSFEE_ISESTIMATE, FINDERFEECLARIFICATIONOFRESP, GROSSPROCEEDSUSED_DOLLARAMOUNT, GROSSPROCEEDSUSED_ISESTIMATE, GROSSPROCEEDSUSED_CLAROFRESP, AUTHORIZEDREPRESENTATIVE |
| RELATEDPERSONS.tsv | 50,014 | ACCESSIONNUMBER, RELATEDPERSON_SEQ_KEY, FIRSTNAME, MIDDLENAME, LASTNAME, STREET1, STREET2, CITY, STATEORCOUNTRY, STATEORCOUNTRYDESCRIPTION, ZIPCODE, RELATIONSHIP_1..3, RELATIONSHIPCLARIFICATION |
| RECIPIENTS.tsv | 6,739 | ACCESSIONNUMBER, RECIPIENT_SEQ_KEY, RECIPIENTNAME, RECIPIENTCRDNUMBER, ASSOCIATEDBDNAME, ASSOCIATEDBDCRDNUMBER, STREET1..ZIPCODE, STATES_OR_VALUE_LIST, DESCRIPTIONS_LIST, FOREIGNSOLICITATION |
| SIGNATURES.tsv | 16,150 | ACCESSIONNUMBER, SIGNATURE_SEQ_KEY, ISSUERNAME, SIGNATURENAME, NAMEOFSIGNER, SIGNATURETITLE, SIGNATUREDATE |
| FormD_metadata.json, FormD_readme.html | | column documentation, read once |

Value conventions in the TSVs (they differ from the XML; normalise both into one model):
- `FILING_DATE` is `30-SEP-2026`; `SALE_DATE` and `SIGNATUREDATE` are ISO `2026-05-01`.
- `FILE_NUM` has trailing spaces. Strip.
- `IS_PRIMARYISSUER_FLAG` is `YES`/`NO`.
- `YEAROFINC_TIMESPAN_CHOICE` is `withinFiveYears` | `overFiveYears` | `yetToBeFormed`.
- Booleans are the string `true` or empty. Treat empty as false/unknown.
- Amount fields hold integers or the literal `Indefinite`.
- `RECIPIENTCRDNUMBER` and `ASSOCIATEDBDNAME` hold the literal `None` when absent.
- Foreign addresses use EDGAR two-character country codes in `STATEORCOUNTRY` (e.g. `E9` Cayman Islands, `F3` Chile) with the name in `STATEORCOUNTRYDESCRIPTION`.
- `SEQ_KEY` values start at 101 per filing.
- A filing can have several issuer rows (co-issuers), hence ISSUERS has more rows than FORMDSUBMISSION.

Distribution in 2026 Q3 (D and D/A together): Venture Capital Fund 2,872; Other Investment Fund 2,626; Private Equity Fund 2,595; Hedge Fund 2,394; non-fund industries make up the rest. New-notice (`D`) Hedge Fund filings: 505, of which 174 had `YETTOOCCUR=true`.

## 6. Per-issuer history: submissions API

`https://data.sec.gov/submissions/CIK{10-digit}.json`

Returns `cik`, `name`, `entityType`, `sic`, `stateOfIncorporation`, and `filings.recent` as parallel arrays: `accessionNumber`, `filingDate`, `form`, `fileNumber`, `primaryDocument`, and more. For a Form D-only issuer this is the authoritative "how many filings has this CIK ever made" check and costs one request. Use it as a fallback when local history is incomplete, not as a primary feed.

## 7. Full-text search (ad hoc only)

`https://efts.sec.gov/LATEST/search-index?q=%22hedge%20fund%22&forms=D&dateRange=custom&startdt=2026-10-01&enddt=2026-10-29&from=100&page=2`

- Page size is fixed at 100. Page with `from` (offset) and `page` together; page N uses `from=(N-1)*100`.
- Results are relevance-ordered, not date-ordered.
- Response: `hits.total.value` (exact), `hits.hits[]` with `_id` = `{adsh}:primary_doc.xml` and `_source` fields `ciks`, `display_names`, `file_date`, `form`, `file_type`, `adsh`, `file_num`, `film_num`, `biz_states`, `biz_locations`, `inc_states`, `sics`, `period_ending`, `items`, `root_forms`. Aggregations: `entity_filter`, `form_filter`, `biz_states_filter`, `sic_filter`.
- `q="hedge fund"` with `forms=D` over October 2026 returned 889 hits, so the index does cover Form D XML text.
- The path must be `/LATEST/` in capitals. A plain curl was refused during testing while a browser-like fetch of the same URL worked, so expect strict agent checks.
- Useful for a "search by manager name" box. Not needed for ingestion.

## 8. Form ADV enrichment (verified 2026-10-30)

Form D says nothing about a manager's website, headcount or assets. Form ADV, filed by investment advisers, does. Two public routes exist; only the first is usable from a script.

### 8.1 Monthly bulk files (the source to use)

Page: `https://www.sec.gov/data-research/sec-markets-data/information-about-registered-investment-advisers-exempt-reporting-advisers`. It links one file for registered investment advisers (RIA) and one for exempt reporting advisers (ERA) per month, under `/files/investment/data/other/information-about-registered-investment-advisers-exempt-reporting-advisers/` (older months under `/files/data/`). Names vary month to month: `ia100226.zip` and `ia100226-exempt.zip` (October 2026), `ia09012026-registered.zip` and `ia09012026-exempt.zip`, `ia08032026_1.zip`. Some months are a PDF saying no data. Discover by scraping the page, as with the Form D data sets; do not build names.

Each zip holds one CSV (the registered file 42 MB, 17,210 rows, 448 columns; the exempt file 6.7 MB, 6,778 rows, 171 columns, October 2026). Headers are Form ADV item labels. The useful ones:

| Column | Meaning | In exempt file |
| --- | --- | --- |
| `Organization CRD#` | adviser identifier, key for IAPD links | yes |
| `SEC#`, `Firm Type`, `SEC Current Status`, `Latest ADV Filing Date` | registration | yes |
| `CIK#` | the adviser's own EDGAR CIK when it has one | yes |
| `Primary Business Name`, `Legal Name` | names (upper case) | yes |
| `Main Office Street Address 1`, `City`, `State`, `Country`, `Postal Code`, `Main Office Telephone Number` | office | yes |
| `Website Address` | website, often blank for small shops | yes |
| `5A` | total employees | no |
| `5B(1)` | employees performing advisory functions | no |
| `5F(2)(c)` | regulatory assets under management, formatted like `            36,321,031.00` | no |
| `5F(2)(d)` | number of accounts | no |
| `7B`, `Count of Private Funds - 7B(1)` | advises private funds, how many | yes |
| `Any Hedge Funds`, `Total number of Hedge funds` | the SEC's own fund-type count | yes |
| `Total Gross Assets of Private Funds` | gross assets of those funds | yes |

Exempt reporting advisers file only Items 1, 2, 3, 6, 7, 10 and 11, so they carry no employee or RAUM figures. Most emerging hedge fund managers are ERAs.

The private fund list with fund names (Schedule D 7.B.(1)) is not in these files. It was in the pre-2025 archive CSVs at `/foia/docs/form-adv-archive-data.htm`; since 2025 it is only on IAPD per firm.

Matching to FundTracker managers, from the first real run (2026-10 files, derive of 2026-10-30, 780 emerging hedge managers first seen 2025-04-30 to 2026-04-30). The planning estimate had 360 matching any key, 184 of them several advisers; with the 20-adviser key cap and the confidence tiers (decisions log), 212 link high, 43 medium, 37 have candidates only and 488 have none. Of the 255 linked, 145 are RIA and 110 ERA, 209 have a website and 145 an employee count and RAUM. Employees of the linked RIAs: median 10, 108 of 145 at 20 or fewer. Name links are reliable. Phone links are mostly right but sometimes reach a large firm or a filing service. Address-only links to an adviser without hedge funds are mostly wrong: Grand Cayman registered offices and shared buildings (PROGRESS.md, Session 10). Example: MALT PARTNERS MANAGEMENT LLC, CRD 337855, RIA, 2 employees, RAUM 36,321,031, 2 hedge funds, no website, linked high by name, phone and address. These numbers predate the name-token rule of Session 11, which leaves 196 high, 11 medium and 85 candidates only (207 linked: 123 RIA, 84 ERA); see the Session 11 entry in PROGRESS.md.

`ingest adv` loads both October 2026 files in about 3 s. `Any Hedge Funds` is `Y` or `N`, and `Total number of Hedge funds` is blank when it is `N`.

### 8.2 IAPD (adviserinfo.sec.gov)

Firm search API works from a script: `https://api.adviserinfo.sec.gov/search/firm?query=<name>&nrows=5` returns JSON `hits.hits[]._source` with `firm_source_id` (the CRD), `firm_ia_full_sec_number`, `firm_name`, `firm_other_names`, `firm_ia_scope` and an address JSON string. Individual search `search/individual?query=<name>` works the same way. The per-firm summary `https://api.adviserinfo.sec.gov/firm/summary/<crd>` answers 403 to scripts regardless of headers; do not build on it. The human page is `https://adviserinfo.sec.gov/firm/summary/<crd>`, the right link target from a manager page.
