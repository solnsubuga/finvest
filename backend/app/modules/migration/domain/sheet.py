"""The import spreadsheet: one fixed format for Finvest 1.0 exports and hand-kept records. Pure.

There are two sheets, each saved as CSV: one row per client, and one row per investment. The
columns are fixed. A missing or unknown column refuses the sheet, so a renamed column can never
be read as something else.

Reading a sheet never stops at the first problem. Every row is read and every problem is
listed, so staff can fix the file in one pass. A `Problem` names the sheet, the spreadsheet
row and the column, and carries a code. It never carries the cell's value, so the list is safe
to log and to show.

Amounts are parsed from text into integer `Money`, and rates into whole basis points. Nothing
passes through a float. Dates are `YYYY-MM-DD` only: a date such as 03/04/2026 reads as March
in one country and April in another, and a start date that is a month out is a money mistake.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.modules.identity.domain.accounts import InvalidIdentifier, normalize_email, normalize_phone
from app.modules.investments.domain.interest import InvalidTerms, check_rate, maturity_date
from app.modules.investments.domain.products import Product
from app.modules.kyc.domain.checks import MINIMUM_AGE, DocumentType
from app.modules.reconciliation.domain.statements import InvalidStatement, parse_amount
from app.shared.money import Money

MAX_ROWS = 100_000
MAX_NAME_LENGTH = 100
MAX_REFERENCE_LENGTH = 64

CLIENT_COLUMNS: tuple[str, ...] = (
    "legacy_client_ref",
    "source",
    "surname",
    "given_names",
    "phone",
    "email",
    "date_of_birth",
    "id_type",
    "id_number",
    "id_expires_on",
    "id_verified",
    "legacy_account_number",
    "next_of_kin_name",
    "next_of_kin_phone",
)
INVESTMENT_COLUMNS: tuple[str, ...] = (
    "legacy_investment_ref",
    "legacy_client_ref",
    "product_code",
    "status",
    "principal",
    "annual_rate_percent",
    "tax_percent",
    "start_date",
    "maturity_date",
    "interest_before_tax",
    "tax_withheld",
)

# References staff can read out over the phone: "48213", "PAPER-0012", "INV/2025/118".
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_ID_NUMBER = re.compile(r"[A-Z0-9]{5,32}")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# "12.5", "12.50" or "12.5%". At most two decimal places, so it is always whole basis points.
_PERCENT = re.compile(r"([0-9]{1,2})(?:\.([0-9]{1,2}))?\s?%?")
_YES = frozenset({"yes", "y", "true", "1"})
_NO = frozenset({"no", "n", "false", "0"})


class Sheet(StrEnum):
    CLIENTS = "clients"
    INVESTMENTS = "investments"

    @property
    def columns(self) -> tuple[str, ...]:
        return CLIENT_COLUMNS if self is Sheet.CLIENTS else INVESTMENT_COLUMNS


class Severity(StrEnum):
    # Must be fixed in the spreadsheet. The final import is refused while one remains.
    ERROR = "error"
    # The row is imported. Staff follow it up, or the client is asked during the claim.
    WARNING = "warning"


class Source(StrEnum):
    FINVEST1 = "finvest1"  # exported from Finvest 1.0
    MANUAL = "manual"  # kept by hand, in a spreadsheet or on paper


class InvestmentStatus(StrEnum):
    # Paid for and earning interest in the old system. Opened in the ledger at the switch.
    RUNNING = "running"
    # An order whose payment the old system had not confirmed. Counted, never opened.
    PENDING_PAYMENT = "pending_payment"


@dataclass(frozen=True, slots=True, order=True)
class Problem:
    """One thing wrong with a sheet. `row` is the spreadsheet row: the header is row 1."""

    sheet: Sheet
    row: int | None
    column: str | None
    code: str
    severity: Severity = Severity.ERROR


@dataclass(frozen=True, slots=True)
class ClientRow:
    row: int
    legacy_client_ref: str
    source: Source
    surname: str
    given_names: str
    phone: str | None  # E.164
    email: str | None
    date_of_birth: date | None
    id_type: DocumentType | None
    id_number: str | None
    id_expires_on: date | None
    # Whether Finvest 1.0 had checked this person's identity document.
    id_verified: bool
    legacy_account_number: str | None
    next_of_kin_name: str | None
    next_of_kin_phone: str | None

    def needs_id_check(self, on: date) -> bool:
        """Whether the client redoes the ID check when they claim the account.

        True when the old system never verified them, or the document on file has expired. A
        verified document with no expiry date on file is not treated as out of date.
        """
        if not self.id_verified or self.id_type is None or self.id_number is None:
            return True
        return self.id_expires_on is not None and self.id_expires_on < on


@dataclass(frozen=True, slots=True)
class InvestmentRow:
    row: int
    legacy_investment_ref: str
    legacy_client_ref: str
    product_code: str
    status: InvestmentStatus
    principal: Money
    # The rest is set for a running investment and `None` for an order awaiting payment.
    annual_rate_bps: int | None = None
    tax_bps: int | None = None
    start_date: date | None = None
    maturity_date: date | None = None  # also `None` for an open term
    # What the old system says was earned up to the import's "as of" date, before tax.
    interest_before_tax: Money | None = None
    # The tax the old system held on that interest. `None` when the old records do not say.
    tax_withheld: Money | None = None


@dataclass(frozen=True, slots=True)
class ParsedSheet[RowT]:
    rows: tuple[RowT, ...]
    problems: tuple[Problem, ...]
    # Every reference seen in the sheet's own reference column, with the rows it appears on,
    # including rows that had problems. Cross-checks use it so that one bad cell in a client
    # row does not also report each of that client's investments as having no client.
    references: Mapping[str, tuple[int, ...]]


def template(sheet: Sheet) -> str:
    """The header row of an empty sheet, for staff to download and fill in."""
    return ",".join(sheet.columns) + "\r\n"


class _Cells:
    """One row's cells, collecting problems as they are read."""

    def __init__(self, sheet: Sheet, row: int, cells: Mapping[str, str]) -> None:
        self._sheet, self._row, self._cells = sheet, row, cells
        self.problems: list[Problem] = []

    def problem(self, column: str | None, code: str, severity: Severity = Severity.ERROR) -> None:
        self.problems.append(Problem(self._sheet, self._row, column, code, severity))

    @property
    def has_errors(self) -> bool:
        return any(problem.severity is Severity.ERROR for problem in self.problems)

    def is_blank(self, column: str) -> bool:
        return not self._cells[column].strip()

    def text(self, column: str, *, max_length: int = MAX_NAME_LENGTH) -> str | None:
        value = " ".join(self._cells[column].split())
        if len(value) > max_length:
            self.problem(column, "too_long")
            return None
        return value or None

    def required(self, column: str, *, max_length: int = MAX_NAME_LENGTH) -> str | None:
        before = len(self.problems)
        value = self.text(column, max_length=max_length)
        if value is None and len(self.problems) == before:
            self.problem(column, "missing")
        return value

    def reference(self, column: str, *, required: bool = True) -> str | None:
        read = self.required if required else self.text
        value = read(column, max_length=MAX_REFERENCE_LENGTH)
        if value is not None and _REFERENCE.fullmatch(value) is None:
            self.problem(column, "reference_invalid")
            return None
        # "c1" and "C1" are one reference: a spreadsheet does not keep case reliably.
        return value.upper() if value is not None else None

    def day(self, column: str) -> date | None:
        value = self.text(column)
        if value is None:
            return None
        try:
            if _ISO_DATE.fullmatch(value) is None:
                raise ValueError(value)
            return date.fromisoformat(value)
        except ValueError:
            self.problem(column, "date_invalid")
            return None

    def phone(self, column: str) -> str | None:
        value = self.text(column)
        if value is None:
            return None
        try:
            return normalize_phone(value)
        except InvalidIdentifier:
            self.problem(column, "phone_invalid")
            return None

    def amount(self, column: str, product: Product | None) -> Money | None:
        value = self.text(column)
        if value is None or product is None:
            return None
        try:
            return parse_amount(value, product.currency)
        except InvalidStatement:
            self.problem(column, "amount_invalid")
            return None

    def basis_points(self, column: str) -> int | None:
        value = self.text(column)
        if value is None:
            return None
        match = _PERCENT.fullmatch(value)
        if match is None:
            self.problem(column, "rate_invalid")
            return None
        return int(match.group(1)) * 100 + int((match.group(2) or "").ljust(2, "0"))

    def blank(self, *columns: str) -> None:
        for column in columns:
            if not self.is_blank(column):
                self.problem(column, "not_expected")


def _read[RowT](
    sheet: Sheet, text: str, reference_column: str, parse: Callable[[_Cells, int], RowT | None]
) -> ParsedSheet[RowT]:
    reader = csv.reader(io.StringIO(text.removeprefix("﻿"), newline=""))
    try:
        header = [cell.strip() for cell in next(reader, [])]
        columns = sheet.columns
        problems = [
            Problem(sheet, None, column, "column_missing")
            for column in columns
            if column not in header
        ]
        problems += [
            Problem(sheet, None, column or None, "column_unknown")
            for column in dict.fromkeys(header)
            if column not in columns
        ]
        if len(set(header)) != len(header):
            problems.append(Problem(sheet, None, None, "column_repeated"))
        if problems:
            return ParsedSheet((), tuple(problems), {})

        rows: list[RowT] = []
        references: dict[str, list[int]] = {}
        for count, values in enumerate(reader, start=1):
            if not any(value.strip() for value in values):
                continue  # Spreadsheets often save empty rows at the end.
            row = count + 1
            if count > MAX_ROWS:
                return ParsedSheet((), (Problem(sheet, None, None, "too_many_rows"),), {})
            if len(values) != len(header):
                problems.append(Problem(sheet, row, None, "row_wrong_length"))
                continue
            cells = _Cells(sheet, row, dict(zip(header, values, strict=True)))
            parsed = parse(cells, row)
            reference = " ".join(values[header.index(reference_column)].split()).upper()
            if reference:
                references.setdefault(reference, []).append(row)
            problems += cells.problems
            if parsed is not None and not cells.has_errors:
                rows.append(parsed)
    except csv.Error:
        return ParsedSheet((), (Problem(sheet, None, None, "not_a_csv_file"),), {})
    return ParsedSheet(
        tuple(rows), tuple(problems), {key: tuple(value) for key, value in references.items()}
    )


def parse_clients(text: str, *, as_of: date) -> ParsedSheet[ClientRow]:
    """Read the clients sheet. `as_of` is the day the old system's figures were taken."""

    def parse(cells: _Cells, row: int) -> ClientRow | None:
        return _client(cells, row, as_of)

    return _read(Sheet.CLIENTS, text, "legacy_client_ref", parse)


def parse_investments(
    text: str, *, as_of: date, products: Mapping[str, Product]
) -> ParsedSheet[InvestmentRow]:
    """Read the investments sheet against the product catalogue, keyed by product code."""

    def parse(cells: _Cells, row: int) -> InvestmentRow | None:
        return _investment(cells, row, as_of, products)

    return _read(Sheet.INVESTMENTS, text, "legacy_investment_ref", parse)


def _client(cells: _Cells, row: int, as_of: date) -> ClientRow | None:
    reference = cells.reference("legacy_client_ref")
    source = _choice(cells, "source", Source)
    surname = cells.required("surname")
    given_names = cells.required("given_names")

    phone = cells.phone("phone")
    email = cells.text("email", max_length=254)
    if email is not None:
        try:
            email = normalize_email(email)
        except InvalidIdentifier:
            cells.problem("email", "email_invalid")
    if cells.is_blank("phone"):
        # The claim confirms the phone by code, so staff must find a number first. The
        # account still moves across, keeps earning and matures as before.
        cells.problem("phone", "phone_missing", Severity.WARNING)

    born = cells.day("date_of_birth")
    if born is not None:
        if born > as_of:
            cells.problem("date_of_birth", "in_the_future")
        elif _years_between(born, as_of) < MINIMUM_AGE:
            cells.problem("date_of_birth", "under_age", Severity.WARNING)

    verified = _yes_no(cells, "id_verified")
    id_type = _choice(cells, "id_type", DocumentType, required=False)
    id_number = cells.text("id_number")
    if id_number is not None:
        id_number = re.sub(r"[\s-]", "", id_number).upper()
        if _ID_NUMBER.fullmatch(id_number) is None:
            cells.problem("id_number", "id_number_invalid")
    expires = cells.day("id_expires_on")
    if verified:
        for column, value in (
            ("id_type", id_type),
            ("id_number", id_number),
            ("date_of_birth", born),
        ):
            if value is None and cells.is_blank(column):
                cells.problem(column, "missing_for_verified_identity")
        if expires is not None and expires < as_of:
            cells.problem("id_expires_on", "id_expired", Severity.WARNING)
        elif expires is None and cells.is_blank("id_expires_on"):
            cells.problem("id_expires_on", "id_expiry_unknown", Severity.WARNING)
    elif verified is False:
        cells.problem("id_verified", "id_not_verified", Severity.WARNING)
        if cells.is_blank("id_type") != cells.is_blank("id_number"):
            # Half an identity document cannot be matched to anything later.
            cells.problem("id_number" if cells.is_blank("id_number") else "id_type", "missing")

    account_number = cells.reference("legacy_account_number", required=False)
    kin_name = cells.text("next_of_kin_name")
    kin_phone = cells.phone("next_of_kin_phone")

    if (
        cells.has_errors
        or reference is None
        or source is None
        or surname is None
        or given_names is None
        or verified is None
    ):
        return None
    return ClientRow(
        row=row,
        legacy_client_ref=reference,
        source=source,
        surname=surname,
        given_names=given_names,
        phone=phone,
        email=email,
        date_of_birth=born,
        id_type=id_type,
        id_number=id_number,
        id_expires_on=expires,
        id_verified=verified,
        legacy_account_number=account_number,
        next_of_kin_name=kin_name,
        next_of_kin_phone=kin_phone,
    )


def _investment(
    cells: _Cells, row: int, as_of: date, products: Mapping[str, Product]
) -> InvestmentRow | None:
    reference = cells.reference("legacy_investment_ref")
    client = cells.reference("legacy_client_ref")
    status = _choice(cells, "status", InvestmentStatus)

    code = cells.required("product_code", max_length=MAX_REFERENCE_LENGTH)
    product = products.get(code.upper()) if code is not None else None
    if code is not None and product is None:
        cells.problem("product_code", "product_unknown")

    principal = cells.amount("principal", product)
    if cells.is_blank("principal"):
        cells.problem("principal", "missing")
    elif principal is not None and not principal.is_positive:
        cells.problem("principal", "amount_not_positive")
    elif principal is not None and product is not None and principal < product.minimum:
        # Not an error: an old investment can predate today's minimum.
        cells.problem("principal", "below_minimum", Severity.WARNING)

    if status is InvestmentStatus.PENDING_PAYMENT:
        cells.blank(
            "annual_rate_percent",
            "tax_percent",
            "start_date",
            "maturity_date",
            "interest_before_tax",
            "tax_withheld",
        )
        if cells.has_errors or reference is None or client is None or product is None:
            return None
        if principal is None:
            return None
        return InvestmentRow(row, reference, client, product.code, status, principal)

    if status is None:
        return None  # Which of the remaining columns apply depends on the status.

    rate = cells.basis_points("annual_rate_percent")
    tax_rate = cells.basis_points("tax_percent")
    for column, value in (("annual_rate_percent", rate), ("tax_percent", tax_rate)):
        if value is None and cells.is_blank(column):
            cells.problem(column, "missing")
    if rate is not None and tax_rate is not None:
        try:
            check_rate(rate, tax_rate)
        except InvalidTerms:
            cells.problem("annual_rate_percent", "rate_out_of_range")

    start = cells.day("start_date")
    matures = cells.day("maturity_date")
    if start is None and cells.is_blank("start_date"):
        cells.problem("start_date", "missing")
    elif start is not None and start > as_of:
        cells.problem("start_date", "starts_after_as_of")
    if product is not None and start is not None:
        if product.term_days is None:
            cells.blank("maturity_date")
        elif matures is None:
            if cells.is_blank("maturity_date"):
                cells.problem("maturity_date", "missing")
        elif matures != maturity_date(start, product.term_days):
            # The new system matures an investment on its start date plus the product's term.
            cells.problem("maturity_date", "maturity_differs")
        elif matures < as_of:
            # Matured in the old system and not yet paid out. It stops earning at maturity.
            cells.problem("maturity_date", "past_maturity", Severity.WARNING)

    interest = cells.amount("interest_before_tax", product)
    tax = cells.amount("tax_withheld", product)
    if cells.is_blank("interest_before_tax"):
        cells.problem("interest_before_tax", "missing")
    if interest is not None and tax is not None and tax > interest:
        cells.problem("tax_withheld", "tax_exceeds_interest")

    if cells.has_errors or reference is None or client is None or product is None:
        return None
    if principal is None or interest is None:
        return None
    return InvestmentRow(
        row=row,
        legacy_investment_ref=reference,
        legacy_client_ref=client,
        product_code=product.code,
        status=status,
        principal=principal,
        annual_rate_bps=rate,
        tax_bps=tax_rate,
        start_date=start,
        maturity_date=matures,
        interest_before_tax=interest,
        tax_withheld=tax,
    )


def _choice[ChoiceT: StrEnum](
    cells: _Cells, column: str, choices: type[ChoiceT], *, required: bool = True
) -> ChoiceT | None:
    value = cells.required(column) if required else cells.text(column)
    if value is None:
        return None
    try:
        return choices(value.casefold().replace(" ", "_"))
    except ValueError:
        cells.problem(column, "value_not_recognised")
        return None


def _yes_no(cells: _Cells, column: str) -> bool | None:
    value = cells.required(column)
    if value is None:
        return None
    word = value.casefold()
    if word not in _YES and word not in _NO:
        cells.problem(column, "yes_or_no_expected")
        return None
    return word in _YES


def _years_between(earlier: date, later: date) -> int:
    before_birthday = (later.month, later.day) < (earlier.month, earlier.day)
    return later.year - earlier.year - (1 if before_birthday else 0)
