"""Statements from ioTec, the bank and the custodian, as the ledger needs to see them. Pure.

A statement is refused whole unless it adds up: the opening balance plus every line must equal
the closing balance. Amounts are parsed from text into integer `Money` and never pass through
a float. A line keeps a fingerprint so importing the same file twice adds nothing.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from app.shared.money import Currency, Money, MoneyError

MAX_LINES = 50_000
MAX_TEXT = 200


class StatementSource(StrEnum):
    IOTEC = "iotec"
    BANK = "bank"
    CUSTODIAN = "custodian"


class Direction(StrEnum):
    IN = "in"  # money arrived in the account the statement is for
    OUT = "out"

    def signed(self, amount: Money) -> Money:
        return amount if self is Direction.IN else -amount


class InvalidStatement(ValueError):
    """The statement cannot be trusted. `code` is safe to log; `row` is the 1-based data row."""

    def __init__(self, code: str, row: int | None = None) -> None:
        super().__init__(code if row is None else f"{code} at row {row}")
        self.code = code
        self.row = row


@dataclass(frozen=True, slots=True)
class StatementLine:
    value_date: date
    direction: Direction
    amount: Money  # always positive; the direction says which way it went
    # The reference we gave the provider, when the statement echoes it. Empty if it does not.
    reference: str
    narrative: str
    fingerprint: str

    @property
    def signed_amount(self) -> Money:
        return self.direction.signed(self.amount)


@dataclass(frozen=True, slots=True)
class Statement:
    source: StatementSource
    # Which pot the statement is for: the ledger reference of the asset account, such as "mtn".
    account_reference: str
    statement_date: date
    opening_balance: Money
    closing_balance: Money
    lines: tuple[StatementLine, ...]

    def __post_init__(self) -> None:
        currency = self.opening_balance.currency
        if self.closing_balance.currency is not currency:
            raise InvalidStatement("statement_mixed_currency")
        if len(self.lines) > MAX_LINES:
            raise InvalidStatement("statement_too_long")
        total = self.opening_balance
        for row, line in enumerate(self.lines, start=1):
            if line.amount.currency is not currency:
                raise InvalidStatement("statement_mixed_currency", row)
            if not line.amount.is_positive:
                raise InvalidStatement("statement_amount_not_positive", row)
            if line.value_date > self.statement_date:
                raise InvalidStatement("statement_line_after_statement_date", row)
            total = total + line.signed_amount
        if total != self.closing_balance:
            raise InvalidStatement("statement_does_not_add_up")
        if len({line.fingerprint for line in self.lines}) != len(self.lines):
            raise InvalidStatement("statement_duplicate_fingerprint")

    @property
    def currency(self) -> Currency:
        return self.opening_balance.currency


@dataclass(frozen=True, slots=True)
class CsvFormat:
    """Which columns of a provider's CSV export hold what. One format per provider export.

    Give either `amount` with `direction` (and the two words that mean in and out), or separate
    `money_in` and `money_out` columns, as bank exports usually have.
    """

    value_date: str
    date_format: str  # for `datetime.strptime`, such as "%d/%m/%Y"
    narrative: str
    reference: str | None = None
    amount: str | None = None
    direction: str | None = None
    in_word: str = "credit"
    out_word: str = "debit"
    money_in: str | None = None
    money_out: str | None = None

    def __post_init__(self) -> None:
        single = self.amount is not None and self.direction is not None
        split = self.money_in is not None and self.money_out is not None
        if single == split:
            raise ValueError("Give amount and direction, or money_in and money_out, not both")


def parse_amount(text: str, currency: Currency) -> Money:
    """Parse "1,500,000" or "1500000.00" exactly. Refuses signs, exponents and stray text.

    Trailing zero decimals are accepted for a currency with no minor unit, because bank exports
    print shillings as "1,500,000.00". Any non-zero fraction of a shilling is refused.
    """
    cleaned = text.strip().replace(",", "")
    if currency.minor_digits == 0 and "." in cleaned:
        whole, _, fraction = cleaned.partition(".")
        if fraction.strip("0"):
            raise InvalidStatement("statement_amount_invalid")
        cleaned = whole
    try:
        amount = Money.from_major(cleaned, currency)
    except MoneyError:
        raise InvalidStatement("statement_amount_invalid") from None
    if amount.is_negative:
        raise InvalidStatement("statement_amount_invalid")
    return amount


def parse_csv(
    text: str,
    csv_format: CsvFormat,
    *,
    source: StatementSource,
    account_reference: str,
    statement_date: date,
    opening_balance: Money,
    closing_balance: Money,
) -> Statement:
    """Read a CSV export into a `Statement`, or refuse all of it with the first problem found."""
    reader = csv.DictReader(io.StringIO(text))
    headers = set(reader.fieldnames or ())
    wanted = (
        csv_format.value_date,
        csv_format.narrative,
        csv_format.reference,
        csv_format.amount,
        csv_format.direction,
        csv_format.money_in,
        csv_format.money_out,
    )
    if any(column is not None and column not in headers for column in wanted):
        raise InvalidStatement("statement_column_missing")

    currency = opening_balance.currency
    seen: Counter[tuple[str, ...]] = Counter()
    lines: list[StatementLine] = []
    for row_number, row in enumerate(reader, start=1):
        if row_number > MAX_LINES:
            raise InvalidStatement("statement_too_long")
        try:
            line = _line(row, csv_format, currency, source, account_reference, seen)
        except InvalidStatement as problem:
            raise InvalidStatement(problem.code, row_number) from None
        lines.append(line)
    return Statement(
        source, account_reference, statement_date, opening_balance, closing_balance, tuple(lines)
    )


def _line(
    row: Mapping[str, str | None],
    csv_format: CsvFormat,
    currency: Currency,
    source: StatementSource,
    account_reference: str,
    seen: Counter[tuple[str, ...]],
) -> StatementLine:
    def cell(column: str | None) -> str:
        value = row.get(column) if column is not None else ""
        if value is None:
            raise InvalidStatement("statement_row_short")
        return value.strip()

    try:
        value_date = datetime.strptime(cell(csv_format.value_date), csv_format.date_format).date()
    except ValueError:
        raise InvalidStatement("statement_date_invalid") from None

    if csv_format.amount is not None:
        amount = parse_amount(cell(csv_format.amount), currency)
        word = cell(csv_format.direction).casefold()
        if word == csv_format.in_word.casefold():
            direction = Direction.IN
        elif word == csv_format.out_word.casefold():
            direction = Direction.OUT
        else:
            raise InvalidStatement("statement_direction_invalid")
    else:
        money_in, money_out = cell(csv_format.money_in), cell(csv_format.money_out)
        if bool(money_in) == bool(money_out):
            raise InvalidStatement("statement_direction_invalid")
        direction = Direction.IN if money_in else Direction.OUT
        amount = parse_amount(money_in or money_out, currency)
    if amount.is_zero:
        raise InvalidStatement("statement_amount_not_positive")

    reference = cell(csv_format.reference)
    narrative = cell(csv_format.narrative)
    if len(reference) > MAX_TEXT or len(narrative) > MAX_TEXT:
        raise InvalidStatement("statement_text_too_long")

    identity = (
        source.value,
        account_reference,
        value_date.isoformat(),
        direction.value,
        str(amount.minor),
        currency.value,
        reference,
        narrative,
    )
    # Two honest lines can be identical (two equal payments on one day), so the fingerprint
    # also counts which one this is. The same file always yields the same fingerprints.
    seen[identity] += 1
    digest = hashlib.sha256("\x1f".join((*identity, str(seen[identity]))).encode()).hexdigest()
    return StatementLine(value_date, direction, amount, reference, narrative, digest)
