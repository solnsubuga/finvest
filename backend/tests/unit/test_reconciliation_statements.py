from datetime import date

import pytest

from app.modules.reconciliation.domain.statements import (
    CsvFormat,
    Direction,
    InvalidStatement,
    Statement,
    StatementLine,
    StatementSource,
    parse_amount,
    parse_csv,
)
from app.shared.money import Currency, Money

DAY = date(2026, 11, 13)
BANK = CsvFormat(
    value_date="Date",
    date_format="%d/%m/%Y",
    narrative="Details",
    reference="Ref",
    money_in="Credit",
    money_out="Debit",
)
IOTEC = CsvFormat(
    value_date="date",
    date_format="%Y-%m-%d",
    narrative="note",
    reference="external_id",
    amount="amount",
    direction="type",
    in_word="collection",
    out_word="disbursement",
)


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def bank(text: str, opening: int, closing: int) -> Statement:
    return parse_csv(
        text,
        BANK,
        source=StatementSource.BANK,
        account_reference="trust",
        statement_date=DAY,
        opening_balance=ugx(opening),
        closing_balance=ugx(closing),
    )


GOOD = (
    "Date,Details,Ref,Debit,Credit\n"
    '13/11/2026,Transfer in,DEP1,,"1,500,000.00"\n'
    "13/11/2026,Payout,PAY1,400000,\n"
    "12/11/2026,Charges,,2500,\n"
)


def test_a_bank_export_is_read_into_integer_money() -> None:
    statement = bank(GOOD, 1_000_000, 2_097_500)
    assert [(line.direction, line.amount, line.reference) for line in statement.lines] == [
        (Direction.IN, ugx(1_500_000), "DEP1"),
        (Direction.OUT, ugx(400_000), "PAY1"),
        (Direction.OUT, ugx(2_500), ""),
    ]
    assert statement.lines[2].value_date == date(2026, 11, 12)
    assert statement.lines[1].signed_amount == ugx(-400_000)
    assert statement.currency is Currency.UGX


def test_a_single_amount_column_with_a_direction_word_is_read() -> None:
    text = (
        "date,type,amount,external_id,note\n"
        "2026-11-13,COLLECTION,50000,DEP9,MTN\n"
        "2026-11-13,disbursement,20000,PAY9,Airtel\n"
    )
    statement = parse_csv(
        text,
        IOTEC,
        source=StatementSource.IOTEC,
        account_reference="mtn",
        statement_date=DAY,
        opening_balance=ugx(0),
        closing_balance=ugx(30_000),
    )
    assert [line.direction for line in statement.lines] == [Direction.IN, Direction.OUT]


@pytest.mark.parametrize(("opening", "closing"), [(1_000_000, 2_097_499), (1_000_001, 2_097_500)])
def test_a_statement_that_does_not_add_up_is_refused_whole(opening: int, closing: int) -> None:
    with pytest.raises(InvalidStatement) as refused:
        bank(GOOD, opening, closing)
    assert refused.value.code == "statement_does_not_add_up"


@pytest.mark.parametrize(
    ("row", "code"),
    [
        ("13/11/2026,Both,X1,100,100", "statement_direction_invalid"),
        ("13/11/2026,Neither,X1,,", "statement_direction_invalid"),
        ("2026-11-13,Wrong date,X1,100,", "statement_date_invalid"),
        ("31/02/2026,No such day,X1,100,", "statement_date_invalid"),
        ("13/11/2026,Half a shilling,X1,100.50,", "statement_amount_invalid"),
        ("13/11/2026,Negative,X1,-100,", "statement_amount_invalid"),
        ("13/11/2026,Exponent,X1,1e5,", "statement_amount_invalid"),
        ("13/11/2026,Words,X1,UGX 100,", "statement_amount_invalid"),
        ("13/11/2026,Zero,X1,0,", "statement_amount_not_positive"),
        ("13/11/2026,Short row,X1", "statement_row_short"),
        ("13/11/2026," + "n" * 201 + ",X1,100,", "statement_text_too_long"),
    ],
)
def test_one_bad_row_refuses_the_statement_and_names_the_row(row: str, code: str) -> None:
    text = "Date,Details,Ref,Debit,Credit\n13/11/2026,Fine,OK1,,100\n" + row + "\n"
    with pytest.raises(InvalidStatement) as refused:
        bank(text, 0, 100)
    assert (refused.value.code, refused.value.row) == (code, 2)


def test_a_refusal_never_carries_what_the_row_said() -> None:
    text = "Date,Details,Ref,Debit,Credit\n13/11/2026,Nakato Sarah 0772123456,X1,abc,\n"
    with pytest.raises(InvalidStatement) as refused:
        bank(text, 0, 0)
    assert "Nakato" not in str(refused.value)
    assert "0772" not in str(refused.value)


def test_a_missing_column_is_refused() -> None:
    with pytest.raises(InvalidStatement) as refused:
        bank("Date,Details,Debit,Credit\n", 0, 0)
    assert refused.value.code == "statement_column_missing"


def test_a_line_dated_after_the_statement_is_refused() -> None:
    text = "Date,Details,Ref,Debit,Credit\n14/11/2026,Tomorrow,X1,,100\n"
    with pytest.raises(InvalidStatement) as refused:
        bank(text, 0, 100)
    assert (refused.value.code, refused.value.row) == ("statement_line_after_statement_date", 1)


def test_an_empty_statement_is_valid_when_the_balance_did_not_move() -> None:
    assert bank("Date,Details,Ref,Debit,Credit\n", 500, 500).lines == ()
    with pytest.raises(InvalidStatement):
        bank("Date,Details,Ref,Debit,Credit\n", 500, 501)


@pytest.mark.parametrize(
    ("text", "minor"),
    [
        ("1500000", 1_500_000),
        ("1,500,000", 1_500_000),
        ("1,500,000.00", 1_500_000),
        ("1,500", 1_500),
        ("999,999", 999_999),
        (" 75 ", 75),
    ],
)
def test_shilling_amounts_are_parsed_exactly(text: str, minor: int) -> None:
    assert parse_amount(text, Currency.UGX) == ugx(minor)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "1.5",
        "1500000.01",
        "-5",
        "+5",
        "1e3",
        "١٢٣",
        "1 500",
        "0x10",
        "1.0.0",
        ".",
        "1500,00",
        "1,5,0",
        "1,50",
        ",500",
        "1,,500",
        "1500,000",
        "1,500,00",
        "1,5000",
        "1,500,",
        "0,5",
        "1.500,00",
        "12,34,567",
    ],
)
def test_other_shilling_amounts_are_refused(text: str) -> None:
    with pytest.raises(InvalidStatement):
        parse_amount(text, Currency.UGX)


def test_dollar_amounts_keep_their_cents() -> None:
    assert parse_amount("1,234.5", Currency.USD) == Money(123_450, Currency.USD)
    for refused in ("1.005", "12,34.50", "1234,50"):
        with pytest.raises(InvalidStatement):
            parse_amount(refused, Currency.USD)


# --- Fingerprints -----------------------------------------------------------------------------


def test_the_same_file_gives_the_same_fingerprints() -> None:
    first, second = bank(GOOD, 1_000_000, 2_097_500), bank(GOOD, 1_000_000, 2_097_500)
    assert [line.fingerprint for line in first.lines] == [line.fingerprint for line in second.lines]


def test_two_identical_honest_lines_keep_separate_fingerprints() -> None:
    text = "Date,Details,Ref,Debit,Credit\n13/11/2026,Deposit,,,50000\n13/11/2026,Deposit,,,50000\n"
    statement = bank(text, 0, 100_000)
    assert len({line.fingerprint for line in statement.lines}) == 2


def test_the_fingerprint_changes_with_every_part_of_the_line() -> None:
    def one(row: str, account: str = "trust") -> str:
        statement = parse_csv(
            "Date,Details,Ref,Debit,Credit\n" + row + "\n",
            BANK,
            source=StatementSource.BANK,
            account_reference=account,
            statement_date=DAY,
            opening_balance=ugx(1_000),
            closing_balance=ugx(1_000 + (100 if row.endswith(",100") else -100)),
        )
        return statement.lines[0].fingerprint

    prints = {
        one("13/11/2026,Deposit,R1,,100"),
        one("12/11/2026,Deposit,R1,,100"),
        one("13/11/2026,Deposit,R2,,100"),
        one("13/11/2026,Other,R1,,100"),
        one("13/11/2026,Deposit,R1,100,"),
        one("13/11/2026,Deposit,R1,,100", account="other"),
    }
    assert len(prints) == 6


def test_a_statement_built_by_hand_is_checked_the_same_way() -> None:
    line = StatementLine(DAY, Direction.IN, ugx(100), "R1", "n", "f1")
    with pytest.raises(InvalidStatement) as duplicate:
        Statement(StatementSource.BANK, "trust", DAY, ugx(0), ugx(200), (line, line))
    assert duplicate.value.code == "statement_duplicate_fingerprint"
    dollars = StatementLine(DAY, Direction.IN, Money(100, Currency.USD), "R1", "n", "f2")
    with pytest.raises(InvalidStatement) as mixed:
        Statement(StatementSource.BANK, "trust", DAY, ugx(0), ugx(100), (dollars,))
    assert mixed.value.code == "statement_mixed_currency"


def test_a_format_names_one_way_of_giving_the_direction() -> None:
    with pytest.raises(ValueError):
        CsvFormat(value_date="d", date_format="%Y", narrative="n")
    with pytest.raises(ValueError):
        CsvFormat(
            value_date="d",
            date_format="%Y",
            narrative="n",
            amount="a",
            direction="t",
            money_in="i",
            money_out="o",
        )
