import pytest

from fundtracker import normalize as n


@pytest.mark.parametrize("text, expected", [
    ("25000000", (25000000, False)),
    ("0", (0, False)),
    (" 1,000,000 ", (1000000, False)),
    ("$250,000", (250000, False)),
    ("100000.00", (100000, False)),
    ("Indefinite", (None, True)),
    ("indefinite", (None, True)),
    ("", (None, None)),
    (None, (None, None)),
    ("None", (None, None)),
    ("12.5", (None, None)),
    ("lots", (None, None)),
])
def test_parse_amount(text, expected):
    assert n.parse_amount(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("2026-05-01", "2026-05-01"),
    ("2010-06-15 12:23:18", "2010-06-15"),
    ("30-SEP-2026", "2026-09-30"),
    ("1-jan-2010", "2010-01-01"),
    ("20261028", "2026-10-28"),
    ("2026-02-30", None),
    ("31-FOO-2026", None),
    ("", None),
    (None, None),
])
def test_parse_date(text, expected):
    assert n.parse_date(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("true", True), ("TRUE", True), ("YES", True),
    ("false", False), ("NO", False),
    ("", None), (None, None), ("   ", None), ("maybe", None),
])
def test_parse_bool(text, expected):
    assert n.parse_bool(text) is expected


@pytest.mark.parametrize("text, expected", [
    ("None", None), (" None ", None), ("none", None), ("NONE", None), ("", None), (None, None),
    ("155178", "155178"), (" Acme ", "Acme"), ("NONE OF THE ABOVE", "NONE OF THE ABOVE"),
])
def test_none_to_null(text, expected):
    assert n.none_to_null(text) == expected


@pytest.mark.parametrize("parts, expected", [
    (("Andrew", "Cunningham", "Murtland"), "ANDREW CUNNINGHAM MURTLAND"),
    (("Axiaris Capital Management", None, "LLC"), "AXIARIS CAPITAL MANAGEMENT LLC"),
    (("-", None, "VSC Ventures Fund I GP LLC"), "VSC VENTURES FUND I GP LLC"),
    (("N/A", "", "Acme Partners, L.P."), "ACME PARTNERS LP"),
    (("n/a", None, "GEF US CSF III GP, LLC"), "GEF US CSF III GP LLC"),
    (("Mary-Kate", None, "O'Brien"), "MARY KATE OBRIEN"),
    ((None, None, None), None),
    (("-", None, ""), None),
    (("--", None, "Wellington Management Company LLP"), "WELLINGTON MANAGEMENT COMPANY LLP"),
])
def test_join_name(parts, expected):
    assert n.join_name(*parts) == expected


ENTITY_CASES = [(f"Acme {token}", True) for token in
                ["LLC", "L.L.C.", "LP", "L.P.", "Ltd", "Limited", "Inc", "Corp", "GP", "Partners",
                 "Capital", "Management", "Advisors", "Advisers", "Holdings"]]


@pytest.mark.parametrize("last, expected", ENTITY_CASES + [("Murtland", False), ("Smith Jr", False)])
def test_is_entity_by_token(last, expected):
    joined = n.join_name("Jo", None, last)
    assert n.is_entity("Jo", last, joined) is expected


@pytest.mark.parametrize("first", ["-", "--", "", "N/A", "n/a", None])
def test_is_entity_by_placeholder_first_name(first):
    assert n.is_entity(first, "Whoever", n.join_name(first, None, "Whoever"))


def test_is_entity_both_encodings():
    assert n.is_entity("Axiaris Capital Management", "LLC", "AXIARIS CAPITAL MANAGEMENT LLC")
    assert n.is_entity("-", "VSC Ventures Fund I GP LLC", "VSC VENTURES FUND I GP LLC")


@pytest.mark.parametrize("name, expected", [
    ("Axiaris Capital Management LLC", "AXIARIS CAPITAL MANAGEMENT"),
    ("The Acme Group, L.P.", "ACME GROUP"),
    ("400 Capital GP LLC", "400 CAPITAL"),
    ("Acme Ltd", "ACME"),
    ("Acme, Inc.", "ACME"),
    ("LLC", None),
])
def test_entity_stem(name, expected):
    assert n.entity_stem(name) == expected


@pytest.mark.parametrize("name, expected", [
    ("X Master Fund LP", "X"),
    ("X Offshore Fund Ltd", "X"),
    ("X Fund LP", "X"),
    ("X Fund, L.P.", "X"),
    ("X Onshore Fund LLC", "X"),
    ("X Cayman Feeder Fund", "X"),
    ("X International Fund II", "X"),
    ("X Fund III-A", "X"),
    ("X Fund IV, Series B", "X"),
    ("X SPV I", "X"),
    ("X Co-Invest Fund V", "X"),
    ("X Co-Investment Fund", "X"),
    ("FirstWave Investment Fund (Offshore), L.P.", "FIRSTWAVE INVESTMENT"),
    ("FirstWave Investment Fund (Onshore)-A, L.P.", "FIRSTWAVE INVESTMENT"),
    ("Fund LP", "FUND LP"),
    (None, None),
])
def test_fund_family_stem(name, expected):
    assert n.fund_family_stem(name) == expected


@pytest.mark.parametrize("street, zip_code, expected", [
    ("3030 SUMMER STREET", "77007", "3030SUMMERSTREET77007"),
    ("3030 Summer Street", "77007", "3030SUMMERSTREET77007"),
    ("4th Floor, 25 Old Burlington Street", "W1S 3AN", "4THFLOOR25OLDBURLINGTONSTREETW1S3AN"),
    (None, "77007", "77007"),
    (None, None, None),
])
def test_address_key(street, zip_code, expected):
    assert n.address_key(street, zip_code) == expected


@pytest.mark.parametrize("street1, street2, expected", [
    ("280 Congress Street", "280 Congress Street", None),
    ("280 Congress Street", " 280 Congress Street ", None),
    ("c/o AQR Capital Management, LLC", "Two Greenwich Plaza", "Two Greenwich Plaza"),
    ("280 Congress Street", "", None),
    (None, "Suite 5", "Suite 5"),
    ("x", None, None),
])
def test_street2(street1, street2, expected):
    assert n.street2(street1, street2) == expected
