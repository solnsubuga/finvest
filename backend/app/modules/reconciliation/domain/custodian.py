"""Checking the custodian's holdings statement against the bills the dealing desk holds. Pure.

The custodian's statement can come in any layout, so a reader (the Claude API, or a stub) turns
it into rows: an instrument, the face value exactly as printed, the maturity date, and where on
the statement the row is. The reader only reads. Everything that decides anything is here:
turning the printed amount into whole minor units, and comparing the statement with Finvest's
own holdings. No amount the reader gives is ever trusted as a number: it is parsed from the
text the reader copied, by the same rules as a bank statement, and a row that cannot be parsed
is shown to staff as unreadable instead of being guessed.

Holdings are compared by currency and maturity date. Finvest's products and the custodian's
instrument names need not agree, but a bill or bond is paid on its maturity date, so the face
value maturing on each day must be the same on both sides.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol

from app.modules.reconciliation.domain.statements import InvalidStatement, parse_amount
from app.shared.money import Currency, Money

# What a reader may hand back, at most. A statement longer than this is refused, not cut.
MAX_ROWS = 500
MAX_TEXT = 300
MAX_LOCATION = 60
MAX_NOTE = 1000

# The kinds of file staff may upload. Spreadsheets are turned into text before reading.
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CONTENT_TYPES = frozenset({"application/pdf", "text/csv", XLSX, "image/png", "image/jpeg"})


class AmountScale(StrEnum):
    """What the statement says its amounts are counted in, such as "UGX '000"."""

    ONE = "one"
    THOUSAND = "thousand"
    MILLION = "million"

    @property
    def factor(self) -> int:
        return {"one": 1, "thousand": 1_000, "million": 1_000_000}[self.value]


class CheckStatus(StrEnum):
    READING = "reading"
    UNREADABLE = "unreadable"
    TO_REVIEW = "to_review"
    WAITING_SIGN_OFF = "waiting_sign_off"
    SIGNED_OFF = "signed_off"


class ItemKind(StrEnum):
    MATCH = "match"
    AMOUNT_DIFFERS = "amount_differs"
    # Finvest holds bills maturing that day, and the statement shows none.
    MISSING_AT_CUSTODIAN = "missing_at_custodian"
    # The statement shows bills maturing that day, and Finvest holds none.
    NOT_IN_FINVEST = "not_in_finvest"
    ROW_UNREADABLE = "row_unreadable"
    STATEMENT_DATE_DIFFERS = "statement_date_differs"
    STATEMENT_DATE_UNREAD = "statement_date_unread"


class CustodianCheckError(Exception):
    """A request the check refuses. `code` is safe to show and log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# The reader port --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StatementFile:
    content_type: str
    data: bytes

    def __repr__(self) -> str:
        # Never the contents: a statement lists holdings.
        return f"StatementFile({self.content_type}, {len(self.data)} bytes)"


@dataclass(frozen=True, slots=True)
class ReadRow:
    """One holding as the reader found it. Every field is text copied from the statement."""

    instrument: str
    code: str | None
    face_value: str
    currency: str | None
    # The maturity date as YYYY-MM-DD, or None when the row shows none the reader could read.
    matures_on: str | None
    # Where the row is, such as "page 2" or "Holdings row 14", so staff can find it.
    location: str
    # The row as printed, so staff can see a misread next to what was read.
    source_text: str


@dataclass(frozen=True, slots=True)
class Reading:
    statement_date: str | None
    custodian_name: str | None
    account_reference: str | None
    amount_scale: AmountScale
    rows: tuple[ReadRow, ...]
    # Which model or stub read it, kept with the check for the audit trail.
    reader: str


class ReaderUnavailable(Exception):
    """The reader did not answer. Asking again later is safe: reading changes nothing."""


class ReaderRefused(Exception):
    """The reader will not read this file, and asking again will not help. `code` is safe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StatementReader(Protocol):
    @property
    def name(self) -> str: ...

    async def read(self, file: StatementFile, *, key: str) -> Reading: ...


def check_reading(reading: Reading) -> Reading:
    """Refuse a reading that is too long, rather than keep part of it."""
    if len(reading.rows) > MAX_ROWS:
        raise ReaderRefused("statement_too_many_rows")
    return reading


# Rows ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedRow:
    face: Money
    matures_on: date


def parse_row(row: ReadRow, scale: AmountScale) -> ParsedRow:
    """The face value in whole minor units and the maturity date, or `CustodianCheckError`
    naming why the row cannot be counted."""
    currency = _currency(row.currency)
    try:
        printed = parse_amount(row.face_value, currency)
    except InvalidStatement:
        raise CustodianCheckError("row_amount_unreadable") from None
    if not printed.is_positive:
        raise CustodianCheckError("row_amount_unreadable")
    matures_on = parse_day(row.matures_on)
    if matures_on is None:
        raise CustodianCheckError("row_maturity_unreadable")
    try:
        face = Money(printed.minor * scale.factor, currency)
    except ValueError:
        raise CustodianCheckError("row_amount_unreadable") from None
    return ParsedRow(face=face, matures_on=matures_on)


def parse_day(text: str | None) -> date | None:
    if text is None:
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def _currency(text: str | None) -> Currency:
    # A statement that prints no currency is in shillings: the custodian holds UGX bills.
    if text is None or not text.strip():
        return Currency.UGX
    cleaned = text.strip().upper()
    if cleaned in {"UGX", "USH", "SHS", "UGX SHS"}:
        return Currency.UGX
    if cleaned in {"USD", "US$"}:
        return Currency.USD
    raise CustodianCheckError("row_currency_unknown")


# Comparison -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Held:
    """Face value Finvest holds of one product and maturity, from the dealing desk."""

    product_code: str
    currency: Currency
    matures_on: date
    face_minor: int


@dataclass(frozen=True, slots=True)
class CountedRow:
    """A statement row as it is counted: read and parsed, or corrected by staff."""

    row_id: str
    # None when the row is unreadable, or staff left it out.
    parsed: ParsedRow | None
    problem: str | None = None
    left_out: bool = False


@dataclass(frozen=True, slots=True)
class Item:
    key: str
    kind: ItemKind
    currency: Currency | None = None
    matures_on: date | None = None
    finvest_face_minor: int = 0
    custodian_face_minor: int = 0
    product_codes: tuple[str, ...] = ()
    row_ids: tuple[str, ...] = ()
    problem: str | None = None

    @property
    def difference_minor(self) -> int:
        """What the custodian shows above Finvest's own records. Negative when it shows less."""
        return self.custodian_face_minor - self.finvest_face_minor

    @property
    def needs_note(self) -> bool:
        return self.kind is not ItemKind.MATCH


def compare(
    held: Sequence[Held],
    rows: Sequence[CountedRow],
    *,
    statement_date: date,
    read_date: date | None,
) -> tuple[Item, ...]:
    """Every maturity on either side, with whether the two agree, then each row that could not
    be counted, then the statement's own date if it is not the day staff said.

    The order is stable: dates first by currency and day, so the same inputs always give the
    same list.
    """
    ours: dict[tuple[Currency, date], list[Held]] = {}
    for each in held:
        ours.setdefault((each.currency, each.matures_on), []).append(each)
    theirs: dict[tuple[Currency, date], list[CountedRow]] = {}
    unreadable: list[Item] = []
    for row in rows:
        if row.left_out:
            continue
        if row.parsed is None:
            unreadable.append(
                Item(
                    key=f"row:{row.row_id}",
                    kind=ItemKind.ROW_UNREADABLE,
                    row_ids=(row.row_id,),
                    problem=row.problem or "row_unreadable",
                )
            )
            continue
        key = (row.parsed.face.currency, row.parsed.matures_on)
        theirs.setdefault(key, []).append(row)
    items: list[Item] = []
    for currency, day in sorted(ours.keys() | theirs.keys()):
        mine = ours.get((currency, day), [])
        finvest = sum(each.face_minor for each in mine)
        counted = theirs.get((currency, day), [])
        custodian = sum(row.parsed.face.minor for row in counted if row.parsed is not None)
        if not counted:
            kind = ItemKind.MISSING_AT_CUSTODIAN
        elif not finvest:
            kind = ItemKind.NOT_IN_FINVEST
        elif finvest == custodian:
            kind = ItemKind.MATCH
        else:
            kind = ItemKind.AMOUNT_DIFFERS
        items.append(
            Item(
                key=f"maturity:{currency.value}:{day.isoformat()}",
                kind=kind,
                currency=currency,
                matures_on=day,
                finvest_face_minor=finvest,
                custodian_face_minor=custodian,
                product_codes=tuple(
                    sorted({h.product_code for h in ours.get((currency, day), [])})
                ),
                row_ids=tuple(row.row_id for row in counted),
            )
        )
    items.extend(unreadable)
    if read_date is None:
        items.append(Item(key="statement_date", kind=ItemKind.STATEMENT_DATE_UNREAD))
    elif read_date != statement_date:
        items.append(
            Item(key="statement_date", kind=ItemKind.STATEMENT_DATE_DIFFERS, matures_on=read_date)
        )
    return tuple(items)


def unexplained(items: Sequence[Item], notes: Mapping[str, str]) -> tuple[str, ...]:
    """Keys of the differences that still have no note from the reviewer."""
    return tuple(
        item.key for item in items if item.needs_note and not (notes.get(item.key) or "").strip()
    )


def check_note(note: str) -> str:
    cleaned = note.strip()
    if not cleaned or len(cleaned) > MAX_NOTE:
        raise CustodianCheckError("note_invalid")
    return cleaned


def may_sign_off(*, submitted_by: str | None, signer: str) -> None:
    """The second person: never whoever sent the check for sign-off."""
    if submitted_by is None:
        raise CustodianCheckError("check_not_waiting")
    if submitted_by == signer:
        raise CustodianCheckError("check_needs_second_person")
