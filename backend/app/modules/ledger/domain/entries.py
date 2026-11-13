"""Ledger accounts, journal entries and the rules for posting them. Pure: no I/O.

A journal entry is in one currency and has at least two lines. Each line carries a signed
amount for one account: a debit is positive and a credit is negative, so the lines of a valid
entry sum to zero. An account's balance is kept in its normal direction (debit for assets and
expenses, credit for liabilities, equity and income) and can never go below zero.

The database enforces every rule here again (migration 0010). These checks exist so a bad entry
is refused with a clear error before it reaches the database.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.shared.money import Currency, Money

MAX_LINES = 500
# Dotted lowercase names, such as "collections.iotec" or "payment.deposit_confirmed".
_CODE = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")
MAX_CODE_LENGTH = 100
# Opaque identifiers only: push IDs and names. Never a phone number or a person's name.
_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9._:-]{8,128}")


class AccountKind(StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"

    @property
    def normal_side(self) -> Side:
        return Side.DEBIT if self in (AccountKind.ASSET, AccountKind.EXPENSE) else Side.CREDIT


class Side(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class InvalidEntry(ValueError):
    """A journal entry or account that breaks a posting rule."""


@dataclass(frozen=True, slots=True)
class AccountSpec:
    """What identifies an account: its place in the chart, its currency and whose it is.

    `code` is the chart of accounts code, shared by every account of that sort. A client's
    account also names the client, and `reference` separates several accounts of one sort for
    one owner (one holding per investment, say). System accounts have neither.
    """

    code: str
    kind: AccountKind
    currency: Currency
    name: str
    owner_client_id: str | None = None
    reference: str | None = None

    def __post_init__(self) -> None:
        if not valid_code(self.code):
            raise InvalidEntry("Account code must be a dotted lowercase name")
        if not isinstance(self.kind, AccountKind) or not isinstance(self.currency, Currency):
            raise InvalidEntry("Unknown account kind or currency")
        if not self.name.strip() or len(self.name) > 120:
            raise InvalidEntry("Account name must be 1 to 120 characters")
        for value in (self.owner_client_id, self.reference):
            if value is not None and _IDENTIFIER.fullmatch(value) is None:
                raise InvalidEntry("Owner and reference must be opaque identifiers")


@dataclass(frozen=True, slots=True)
class Line:
    """One side of an entry. `amount` is signed: a debit is positive, a credit is negative."""

    account_id: str
    amount: Money

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Money):
            raise InvalidEntry("Line amounts are Money")
        if self.amount.is_zero:
            raise InvalidEntry("A line cannot be for a zero amount")
        if not isinstance(self.account_id, str) or _IDENTIFIER.fullmatch(self.account_id) is None:
            raise InvalidEntry("A line needs an account ID")

    @property
    def side(self) -> Side:
        return Side.DEBIT if self.amount.is_positive else Side.CREDIT


def debit(account_id: str, amount: Money) -> Line:
    return Line(account_id, _positive(amount))


def credit(account_id: str, amount: Money) -> Line:
    return Line(account_id, -_positive(amount))


@dataclass(frozen=True, slots=True)
class EntryDraft:
    """A journal entry ready to post.

    `entry_type` names the business event. `effective_date` is the value date the books show it
    on. `source_type` and `source_id` point at the business record that caused it (a payment, an
    accrual run), so the ledger can always be tied back to its reason.
    """

    entry_type: str
    effective_date: date
    lines: tuple[Line, ...]
    idempotency_key: str
    source_type: str | None = None
    source_id: str | None = None
    reverses_entry_id: str | None = None

    def __post_init__(self) -> None:
        if not valid_code(self.entry_type):
            raise InvalidEntry("Entry type must be a dotted lowercase name")
        if type(self.effective_date) is not date:
            raise InvalidEntry("The effective date is a calendar date")
        if _IDEMPOTENCY_KEY.fullmatch(self.idempotency_key) is None:
            raise InvalidEntry("An idempotency key of 8 to 128 safe characters is required")
        if (self.source_type is None) != (self.source_id is None):
            raise InvalidEntry("Source type and source ID are given together")
        if self.source_type is not None and not valid_code(self.source_type):
            raise InvalidEntry("Source type must be a dotted lowercase name")
        for value in (self.source_id, self.reverses_entry_id):
            if value is not None and _IDENTIFIER.fullmatch(value) is None:
                raise InvalidEntry("Source and reversed entry must be opaque identifiers")
        if not isinstance(self.lines, tuple):
            raise InvalidEntry("Lines are a tuple")
        if not 2 <= len(self.lines) <= MAX_LINES:
            raise InvalidEntry(f"An entry has between 2 and {MAX_LINES} lines")
        currencies = {line.amount.currency for line in self.lines}
        if len(currencies) != 1:
            raise InvalidEntry("An entry is in one currency")
        if len({line.account_id for line in self.lines}) != len(self.lines):
            raise InvalidEntry("An account appears at most once in an entry")
        if sum(line.amount.minor for line in self.lines) != 0:
            raise InvalidEntry("The lines of an entry must sum to zero")

    @property
    def currency(self) -> Currency:
        return self.lines[0].amount.currency

    def digest(self) -> str:
        """Identifies the request behind an idempotency key.

        A retry with the same key and digest is the same request and gets the saved entry. The
        same key with a different digest is a different request and is refused. Line order is
        not part of the identity.
        """
        parts = [
            "v1",
            self.entry_type,
            self.currency.value,
            self.effective_date.isoformat(),
            self.source_type or "",
            self.source_id or "",
            self.reverses_entry_id or "",
            *(f"{line.account_id}={line.amount.minor}" for line in sorted_lines(self.lines)),
        ]
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def reversal(
    original_id: str,
    original_lines: tuple[Line, ...],
    *,
    effective_date: date,
    idempotency_key: str,
    entry_type: str = "ledger.reversal",
) -> EntryDraft:
    """The entry that undoes `original`: the same accounts with every amount negated."""
    return EntryDraft(
        entry_type=entry_type,
        effective_date=effective_date,
        lines=tuple(Line(line.account_id, -line.amount) for line in original_lines),
        idempotency_key=idempotency_key,
        source_type="ledger.entry",
        source_id=original_id,
        reverses_entry_id=original_id,
    )


def sorted_lines(lines: tuple[Line, ...]) -> list[Line]:
    """Lines by account ID. Posting in this order makes every transaction lock balances in the
    same order, so two entries over the same accounts cannot deadlock."""
    return sorted(lines, key=lambda line: line.account_id)


def normal_balance(kind: AccountKind, signed_total_minor: int) -> int:
    """An account's balance in its normal direction, from the signed sum of its lines."""
    return signed_total_minor if kind.normal_side is Side.DEBIT else -signed_total_minor


def valid_code(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= MAX_CODE_LENGTH
        and _CODE.fullmatch(value) is not None
    )


def _positive(amount: Money) -> Money:
    if not isinstance(amount, Money) or not amount.is_positive:
        raise InvalidEntry("Debit and credit amounts are positive Money")
    return amount
