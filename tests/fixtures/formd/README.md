# Form D fixtures

Real primary_doc.xml files from EDGAR, saved by scripts/fetch_fixture.py. One line per fixture: accession, issuer CIK, and what it exercises.

- `0002158817-26-000001.xml` (CIK 2158817): filed 2026-10-28, schema X0708. Axiaris Capital Management Fund, LP: Hedge Fund new notice and the reference worked example (06c, 3C.1, yetToOccur, withinFiveYears 2026, entity related person split as firstName="Axiaris Capital Management" lastName="LLC").
- `0002081663-25-000001.xml` (CIK 2081663): filed 2025-10-03, schema X0708. VSC Ventures PL10, LLC: Venture Capital Fund with an entity related person encoded as firstName="-".
- `0001741616-26-000005.xml` (CIK 1741616): filed 2026-10-27, schema X0708. Callodine Capital Fund, LP: Hedge Fund D/A amendment with previousAccessionNumber, overFiveYears.
- `0000919574-26-007205.xml` (CIK 2159130): filed 2026-10-27, schema X0708. Arrogate Capital Fund, LP: Hedge Fund new notice with 3C.7; filed by an agent, so the accession prefix is not the issuer CIK.
- `0002154220-26-000001.xml` (CIK 2154220): filed 2026-10-27, schema X0708. Armada Capital Credit LP: Other Investment Fund with 3C and 3C.1.
- `0001012975-26-000984.xml` (CIK 2134351): filed 2026-10-27, schema X0708. Endless VI A LP: Private Equity Fund new notice.
- `0001214659-26-013245.xml` (CIK 2153113): filed 2026-10-27, schema X0708. Equera, Inc: non-fund issuer (Other Technology) with revenueRange "$1 - $1,000,000".
- `0002161535-26-000001.xml` (CIK 2161535): filed 2026-10-27, schema X0708. GEF US Climate Solutions Fund III, L.P.: Private Equity Fund with a co-issuer in issuerList and two signatures (also three sales compensation recipients).
- `0002124823-26-000002.xml` (CIK 2124823): filed 2026-10-27, schema X0708. Private Market Securities Fund II, LLC: Other Investment Fund with a salesCompensationList recipient and a long statesOfSolicitationList.
- `0001437749-26-033645.xml` (CIK 1546296): filed 2026-10-27, schema X0708. Professional Diversity Network, Inc.: non-fund issuer with yearOfInc/overFiveYears and revenueRange "$5,000,001 - $25,000,000".
- `0000919574-10-003893.xml` (CIK 1467029): filed 2010-06-15, schema X0704. 400 Capital Credit Opportunities Fund LP: older-schema Hedge Fund new notice (exemption 06, 3C.7).
- `0001635511-15-000001.xml` (CIK 1635511): filed 2015-06-15, schema X0707. AQR Offshore Multi-Strategy Fund XIV, L.P.: older-schema Hedge Fund new notice (06b, 3C.7).
