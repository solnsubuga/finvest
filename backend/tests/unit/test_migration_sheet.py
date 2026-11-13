from datetime import date

import pytest

from app.modules.investments.domain.products import SEED_PRODUCTS
from app.modules.kyc.domain.checks import DocumentType
from app.modules.migration.domain.sheet import (
    CLIENT_COLUMNS,
    INVESTMENT_COLUMNS,
    MAX_ROWS,
    InvestmentStatus,
    Problem,
    Severity,
    Sheet,
    Source,
    parse_clients,
    parse_investments,
    template,
)
from app.shared.money import Currency, Money

AS_OF = date(2026, 11, 13)
PRODUCTS = {product.code: product for product in SEED_PRODUCTS}

CLIENT = {
    "legacy_client_ref": "48213",
    "source": "finvest1",
    "surname": "Nakato",
    "given_names": "Amara Grace",
    "phone": "0772 123 456",
    "email": "Amara@Example.com",
    "date_of_birth": "1990-04-02",
    "id_type": "national_id",
    "id_number": "cm9 0012-345 abc",
    "id_expires_on": "2030-01-01",
    "id_verified": "yes",
    "legacy_account_number": "100482",
    "next_of_kin_name": "Joseph Nakato",
    "next_of_kin_phone": "+256701000111",
}
INVESTMENT = {
    "legacy_investment_ref": "INV/2026/118",
    "legacy_client_ref": "48213",
    "product_code": "TREA-91",
    "status": "running",
    "principal": "1,000,000",
    "annual_rate_percent": "12.5",
    "tax_percent": "15%",
    "start_date": "2026-10-10",
    "maturity_date": "2027-01-09",
    "interest_before_tax": "11,643",
    "tax_withheld": "1,746",
}


def sheet(columns: tuple[str, ...], *rows: dict[str, str]) -> str:
    lines = [",".join(columns)]
    lines += [",".join(f'"{row[column]}"' for column in columns) for row in rows]
    return "\r\n".join(lines) + "\r\n"


def clients(*rows: dict[str, str]) -> str:
    return sheet(CLIENT_COLUMNS, *rows)


def investments(*rows: dict[str, str]) -> str:
    return sheet(INVESTMENT_COLUMNS, *rows)


def client_problems(**changes: str) -> list[tuple[str | None, str, Severity]]:
    parsed = parse_clients(clients(CLIENT | changes), as_of=AS_OF)
    return [(p.column, p.code, p.severity) for p in parsed.problems]


def investment_problems(**changes: str) -> list[tuple[str | None, str, Severity]]:
    parsed = parse_investments(investments(INVESTMENT | changes), as_of=AS_OF, products=PRODUCTS)
    return [(p.column, p.code, p.severity) for p in parsed.problems]


ERROR, WARNING = Severity.ERROR, Severity.WARNING


def test_a_good_client_row_is_read_and_normalised() -> None:
    parsed = parse_clients(clients(CLIENT), as_of=AS_OF)

    assert parsed.problems == ()
    (row,) = parsed.rows
    assert row.row == 2  # the header is row 1, as staff see it in the spreadsheet
    assert row.legacy_client_ref == "48213"
    assert row.source is Source.FINVEST1
    assert (row.surname, row.given_names) == ("Nakato", "Amara Grace")
    assert row.phone == "+256772123456"
    assert row.email == "amara@example.com"
    assert row.date_of_birth == date(1990, 4, 2)
    assert row.id_type is DocumentType.NATIONAL_ID
    assert row.id_number == "CM90012345ABC"
    assert row.id_expires_on == date(2030, 1, 1)
    assert row.id_verified is True
    assert row.legacy_account_number == "100482"
    assert row.next_of_kin_phone == "+256701000111"
    assert not row.needs_id_check(AS_OF)
    assert parsed.references == {"48213": (2,)}


def test_a_good_investment_row_is_read_without_floats() -> None:
    parsed = parse_investments(investments(INVESTMENT), as_of=AS_OF, products=PRODUCTS)

    assert parsed.problems == ()
    (row,) = parsed.rows
    assert row.status is InvestmentStatus.RUNNING
    assert row.principal == Money(1_000_000, Currency.UGX)
    assert (row.annual_rate_bps, row.tax_bps) == (1250, 1500)
    assert type(row.annual_rate_bps) is int
    assert row.start_date == date(2026, 10, 10)
    assert row.maturity_date == date(2027, 1, 9)
    assert row.interest_before_tax == Money(11_643, Currency.UGX)
    assert row.tax_withheld == Money(1_746, Currency.UGX)


@pytest.mark.parametrize(
    ("text", "bps"), [("12.5", 1250), ("12.50%", 1250), ("7.25", 725), ("13 %", 1300), ("9", 900)]
)
def test_rates_become_whole_basis_points(text: str, bps: int) -> None:
    parsed = parse_investments(
        investments(INVESTMENT | {"annual_rate_percent": text}), as_of=AS_OF, products=PRODUCTS
    )
    assert parsed.rows[0].annual_rate_bps == bps


@pytest.mark.parametrize("text", ["12.555", "0.125", "twelve", "1e1", "-12.5", "12,5", "125"])
def test_a_rate_that_is_not_a_plain_percentage_is_refused(text: str) -> None:
    assert investment_problems(annual_rate_percent=text) == [
        ("annual_rate_percent", "rate_invalid", ERROR)
    ]


@pytest.mark.parametrize("text", ["0", "50.01", "99"])
def test_a_rate_outside_what_a_product_can_pay_is_refused(text: str) -> None:
    assert investment_problems(annual_rate_percent=text) == [
        ("annual_rate_percent", "rate_out_of_range", ERROR)
    ]


@pytest.mark.parametrize("text", ["10/10/2026", "2026-13-01", "2026-02-30", "10 Oct 2026", "46305"])
def test_only_iso_dates_are_accepted(text: str) -> None:
    assert investment_problems(start_date=text) == [("start_date", "date_invalid", ERROR)]


@pytest.mark.parametrize("text", ["1000000.5", "1.000.000", "1000,00", "UGX 1000000", "1e6", "-5"])
def test_an_amount_that_is_not_whole_shillings_is_refused(text: str) -> None:
    assert investment_problems(principal=text) == [("principal", "amount_invalid", ERROR)]


def test_dollar_amounts_are_read_in_cents() -> None:
    row = INVESTMENT | {
        "product_code": "usd-bond",
        "principal": "1,500.50",
        "annual_rate_percent": "7.25",
        "tax_percent": "5",
        "maturity_date": "",
        "interest_before_tax": "10.13",
        "tax_withheld": "0.50",
    }
    parsed = parse_investments(investments(row), as_of=AS_OF, products=PRODUCTS)

    assert parsed.problems == ()
    assert parsed.rows[0].product_code == "USD-BOND"
    assert parsed.rows[0].principal == Money(150_050, Currency.USD)
    assert parsed.rows[0].maturity_date is None


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"legacy_investment_ref": ""}, [("legacy_investment_ref", "missing", ERROR)]),
        ({"legacy_client_ref": "a b"}, [("legacy_client_ref", "reference_invalid", ERROR)]),
        ({"product_code": "TREA-364"}, [("product_code", "product_unknown", ERROR)]),
        ({"status": "redeemed"}, [("status", "value_not_recognised", ERROR)]),
        ({"principal": ""}, [("principal", "missing", ERROR)]),
        ({"principal": "0"}, [("principal", "amount_not_positive", ERROR)]),
        ({"principal": "50,000"}, [("principal", "below_minimum", WARNING)]),
        ({"annual_rate_percent": ""}, [("annual_rate_percent", "missing", ERROR)]),
        ({"tax_percent": ""}, [("tax_percent", "missing", ERROR)]),
        ({"start_date": ""}, [("start_date", "missing", ERROR)]),
        (
            {"start_date": "2026-11-14", "maturity_date": "2027-02-13"},
            [("start_date", "starts_after_as_of", ERROR)],
        ),
        ({"maturity_date": ""}, [("maturity_date", "missing", ERROR)]),
        ({"maturity_date": "2027-01-10"}, [("maturity_date", "maturity_differs", ERROR)]),
        (
            {"start_date": "2026-08-01", "maturity_date": "2026-10-31"},
            [("maturity_date", "past_maturity", WARNING)],
        ),
        ({"interest_before_tax": ""}, [("interest_before_tax", "missing", ERROR)]),
        ({"tax_withheld": "11,644"}, [("tax_withheld", "tax_exceeds_interest", ERROR)]),
        ({"tax_withheld": ""}, []),
    ],
)
def test_investment_problems(
    changes: dict[str, str], expected: list[tuple[str, str, Severity]]
) -> None:
    assert investment_problems(**changes) == expected


def test_an_open_term_has_no_maturity_date() -> None:
    assert investment_problems(product_code="USD-BOND", principal="600.00", tax_withheld="") == [
        ("maturity_date", "not_expected", ERROR)
    ]


def test_an_order_awaiting_payment_carries_only_the_amount() -> None:
    blank = dict.fromkeys(INVESTMENT_COLUMNS[5:], "")
    parsed = parse_investments(
        investments(INVESTMENT | blank | {"status": "Pending payment"}),
        as_of=AS_OF,
        products=PRODUCTS,
    )
    assert parsed.problems == ()
    assert parsed.rows[0].status is InvestmentStatus.PENDING_PAYMENT
    assert parsed.rows[0].start_date is None
    assert parsed.rows[0].interest_before_tax is None

    assert investment_problems(status="pending_payment") == [
        (column, "not_expected", ERROR) for column in INVESTMENT_COLUMNS[5:]
    ]


def test_every_problem_in_a_row_is_listed_not_only_the_first() -> None:
    assert investment_problems(principal="lots", start_date="soon", tax_percent="") == [
        ("principal", "amount_invalid", ERROR),
        ("tax_percent", "missing", ERROR),
        ("start_date", "date_invalid", ERROR),
    ]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"legacy_client_ref": ""}, [("legacy_client_ref", "missing", ERROR)]),
        ({"legacy_client_ref": "x" * 65}, [("legacy_client_ref", "too_long", ERROR)]),
        ({"source": "excel"}, [("source", "value_not_recognised", ERROR)]),
        ({"surname": " "}, [("surname", "missing", ERROR)]),
        ({"given_names": "a" * 101}, [("given_names", "too_long", ERROR)]),
        ({"phone": "12345"}, [("phone", "phone_invalid", ERROR)]),
        ({"phone": ""}, [("phone", "phone_missing", WARNING)]),
        ({"email": "amara-at-example"}, [("email", "email_invalid", ERROR)]),
        ({"email": ""}, []),
        ({"date_of_birth": "2026-12-01"}, [("date_of_birth", "in_the_future", ERROR)]),
        ({"date_of_birth": "2010-01-01"}, [("date_of_birth", "under_age", WARNING)]),
        ({"date_of_birth": ""}, [("date_of_birth", "missing_for_verified_identity", ERROR)]),
        ({"id_type": "voter_card"}, [("id_type", "value_not_recognised", ERROR)]),
        ({"id_number": "12"}, [("id_number", "id_number_invalid", ERROR)]),
        ({"id_number": ""}, [("id_number", "missing_for_verified_identity", ERROR)]),
        ({"id_expires_on": "2026-11-12"}, [("id_expires_on", "id_expired", WARNING)]),
        ({"id_expires_on": ""}, [("id_expires_on", "id_expiry_unknown", WARNING)]),
        ({"id_verified": "maybe"}, [("id_verified", "yes_or_no_expected", ERROR)]),
        ({"id_verified": "no"}, [("id_verified", "id_not_verified", WARNING)]),
        (
            {"id_verified": "no", "id_number": ""},
            [("id_verified", "id_not_verified", WARNING), ("id_number", "missing", ERROR)],
        ),
        (
            {"legacy_account_number": "10 04 82"},
            [("legacy_account_number", "reference_invalid", ERROR)],
        ),
        ({"next_of_kin_phone": "none"}, [("next_of_kin_phone", "phone_invalid", ERROR)]),
    ],
)
def test_client_problems(
    changes: dict[str, str], expected: list[tuple[str, str, Severity]]
) -> None:
    assert client_problems(**changes) == expected


def test_who_redoes_the_id_check_at_claim() -> None:
    def needs(**changes: str) -> bool:
        return parse_clients(clients(CLIENT | changes), as_of=AS_OF).rows[0].needs_id_check(AS_OF)

    assert not needs()
    assert not needs(id_expires_on="")  # unknown is not the same as out of date
    assert not needs(id_expires_on="2026-11-13")  # still valid on its last day
    assert needs(id_expires_on="2026-11-12")
    assert needs(id_verified="no")
    assert needs(id_verified="no", id_type="", id_number="", date_of_birth="")


def test_a_row_with_an_error_is_left_out_and_a_warning_row_is_kept() -> None:
    text = clients(
        CLIENT,
        CLIENT | {"legacy_client_ref": "48214", "phone": "nope"},
        CLIENT | {"legacy_client_ref": "48215", "phone": ""},
    )
    parsed = parse_clients(text, as_of=AS_OF)

    assert [row.legacy_client_ref for row in parsed.rows] == ["48213", "48215"]
    assert parsed.problems == (
        Problem(Sheet.CLIENTS, 3, "phone", "phone_invalid"),
        Problem(Sheet.CLIENTS, 4, "phone", "phone_missing", Severity.WARNING),
    )
    # The reference of the bad row is still known, so its investments are not orphaned.
    assert parsed.references == {"48213": (2,), "48214": (3,), "48215": (4,)}


def test_problems_never_carry_a_cell_value() -> None:
    parsed = parse_clients(clients(CLIENT | {"phone": "07-SECRET"}), as_of=AS_OF)
    assert "SECRET" not in repr(parsed.problems)


def test_a_sheet_with_the_wrong_columns_is_refused_whole() -> None:
    renamed = clients(CLIENT).replace("surname", "last_name", 1)
    parsed = parse_clients(renamed, as_of=AS_OF)

    assert parsed.rows == ()
    assert parsed.problems == (
        Problem(Sheet.CLIENTS, None, "surname", "column_missing"),
        Problem(Sheet.CLIENTS, None, "last_name", "column_unknown"),
    )


def test_a_repeated_column_is_refused() -> None:
    doubled = clients(CLIENT).replace("email,", "email,email,", 1)
    assert parse_clients(doubled, as_of=AS_OF).problems == (
        Problem(Sheet.CLIENTS, None, None, "column_repeated"),
    )


def test_an_empty_file_lists_every_column_as_missing() -> None:
    parsed = parse_investments("", as_of=AS_OF, products=PRODUCTS)
    assert [p.column for p in parsed.problems] == list(INVESTMENT_COLUMNS)
    assert {p.code for p in parsed.problems} == {"column_missing"}


def test_columns_may_come_in_any_order_and_with_an_excel_byte_order_mark() -> None:
    reordered = tuple(reversed(CLIENT_COLUMNS))
    parsed = parse_clients("﻿" + sheet(reordered, CLIENT), as_of=AS_OF)
    assert parsed.problems == ()
    assert parsed.rows[0].surname == "Nakato"


def test_blank_rows_are_skipped_and_row_numbers_still_match_the_spreadsheet() -> None:
    empty = ",".join("" for _ in CLIENT_COLUMNS)
    text = (
        clients(CLIENT).rstrip("\r\n")
        + f"\r\n{empty}\r\n"
        + clients(CLIENT | {"legacy_client_ref": "48214", "phone": "x"}).split("\r\n", 1)[1]
    )
    parsed = parse_clients(text + "\r\n\r\n", as_of=AS_OF)

    assert [row.row for row in parsed.rows] == [2]
    assert parsed.problems == (Problem(Sheet.CLIENTS, 4, "phone", "phone_invalid"),)


def test_a_row_with_too_many_or_too_few_cells_is_reported() -> None:
    text = clients(CLIENT).rstrip("\r\n") + ",extra\r\nonly,three,cells\r\n"
    assert parse_clients(text, as_of=AS_OF).problems == (
        Problem(Sheet.CLIENTS, 2, None, "row_wrong_length"),
        Problem(Sheet.CLIENTS, 3, None, "row_wrong_length"),
    )


def test_a_sheet_longer_than_the_limit_is_refused() -> None:
    header = ",".join(CLIENT_COLUMNS)
    text = header + "\r\n" + "x\r\n" * (MAX_ROWS + 1)
    parsed = parse_clients(text, as_of=AS_OF)
    assert parsed.rows == ()
    assert parsed.problems == (Problem(Sheet.CLIENTS, None, None, "too_many_rows"),)


def test_the_template_is_the_header_row() -> None:
    assert parse_clients(template(Sheet.CLIENTS), as_of=AS_OF).problems == ()
    assert template(Sheet.INVESTMENTS).strip().split(",") == list(INVESTMENT_COLUMNS)
