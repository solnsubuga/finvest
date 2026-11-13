"""A deposit by bank transfer, checked by staff against the bank statement. Pure: no I/O.

The client makes the transfer in their own bank, then tells us about it: a receipt file, the
amount, the day it was sent, their bank and the bank's transaction number. The deposit is saved
as `in_review` and a staff member settles it:

    in_review ──► confirmed   the transfer is a line on an imported bank statement
        │
        └──────► failed       it could not be matched, with a reason the client is shown

Nothing the client says moves money. A deposit is confirmed only against a statement line that
came in, for exactly the amount owed, on or after the day the client says they sent it. A line
confirms one deposit at most. A failed deposit frees the investment, so the client can upload
another receipt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from app.shared.money import Currency, Money

# Whatever a phone camera or a bank's app produces: a photo or the PDF the bank sends.
RECEIPT_CONTENT_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png"})
MAX_RECEIPT_BYTES = 10 * 1024 * 1024
# A receipt for a transfer older than this is not for an investment the client just chose.
MAX_RECEIPT_AGE = timedelta(days=30)
# Banks credit a transfer the same day or a few working days later. A statement line later than
# this after the day the client gave is not their transfer.
MATCH_WINDOW = timedelta(days=7)
MAX_BANK_NAME = 80
MAX_TRANSACTION_NUMBER = 64

_SHA256 = re.compile(r"[0-9a-f]{64}")
_TRANSACTION_NUMBER = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ./-]*")
_SPACES = re.compile(r"\s+")


class NotMatchedReason(StrEnum):
    """Why staff could not match a receipt. The client app has words for each."""

    AMOUNT_DIFFERS = "amount_differs"
    NOT_ON_STATEMENT = "not_on_statement"
    UNREADABLE = "unreadable"
    # The transfer came from an account in someone else's name. It is refused outright.
    NAME_DIFFERS = "name_differs"
    OTHER = "other"


class InvalidReceipt(ValueError):
    """A receipt that breaks a rule. `field` and `code` are safe to return to the caller."""

    def __init__(self, field: str, code: str) -> None:
        super().__init__(f"{field}: {code}")
        self.field = field
        self.code = code


@dataclass(frozen=True, slots=True)
class ReceiptFile:
    """What the client uploaded. The file itself goes to private storage, never the database."""

    content_type: str
    byte_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ReceiptDetails:
    """What the client says they sent. Staff check every part against the receipt."""

    amount: Money
    sent_on: date
    bank_name: str
    transaction_number: str


# How each accepted type begins. The content type is the client's own claim, so the first bytes
# must agree with it before the file is stored or shown to staff.
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
}


def check_receipt_bytes(content_type: str, data: bytes) -> None:
    """Refuse a file whose first bytes are not those of its claimed type."""
    if not data.startswith(_SIGNATURES.get(content_type, ())):
        raise InvalidReceipt("file", "content_does_not_match_type")


def check_receipt_file(file: ReceiptFile) -> ReceiptFile:
    if file.content_type not in RECEIPT_CONTENT_TYPES:
        raise InvalidReceipt("file", "unsupported_content_type")
    if type(file.byte_size) is not int or not 0 < file.byte_size <= MAX_RECEIPT_BYTES:
        raise InvalidReceipt("file", "invalid_size")
    if not isinstance(file.sha256, str) or _SHA256.fullmatch(file.sha256) is None:
        raise InvalidReceipt("file", "invalid_digest")
    return file


def check_receipt_details(details: ReceiptDetails, *, today: date) -> ReceiptDetails:
    """The details tidied, or `InvalidReceipt`. `today` is the business day in Kampala."""
    amount = details.amount
    if not isinstance(amount, Money) or amount.currency is not Currency.UGX:
        raise InvalidReceipt("amount", "unsupported_currency")
    if not amount.is_positive:
        raise InvalidReceipt("amount", "invalid_amount")
    if not isinstance(details.sent_on, date) or details.sent_on > today:
        raise InvalidReceipt("sent_on", "in_the_future")
    if details.sent_on < today - MAX_RECEIPT_AGE:
        raise InvalidReceipt("sent_on", "too_old")
    bank_name = (
        _SPACES.sub(" ", details.bank_name).strip() if isinstance(details.bank_name, str) else ""
    )
    if not 2 <= len(bank_name) <= MAX_BANK_NAME:
        raise InvalidReceipt("bank_name", "invalid_bank_name")
    number = (
        _SPACES.sub(" ", details.transaction_number).strip()
        if isinstance(details.transaction_number, str)
        else ""
    )
    if len(number) > MAX_TRANSACTION_NUMBER or _TRANSACTION_NUMBER.fullmatch(number) is None:
        raise InvalidReceipt("transaction_number", "invalid_transaction_number")
    return ReceiptDetails(amount, details.sent_on, bank_name, number)


@dataclass(frozen=True, slots=True)
class LineFacts:
    """The parts of a bank statement line that decide whether it can be this deposit."""

    money_in: bool
    amount: Money
    value_date: date


def match_problem(*, owed: Money, sent_on: date, line: LineFacts) -> str | None:
    """Why `line` cannot confirm a deposit of `owed` sent on `sent_on`, or None if it can.

    The amount must be exactly what is owed: a transfer for any other amount is refused with
    `amount_differs` and the client sends a new one. Nothing is posted for part of a payment.
    """
    if not line.money_in:
        return "line_is_money_out"
    if line.amount.currency is not owed.currency or line.amount != owed:
        return "line_amount_differs"
    if not sent_on <= line.value_date <= sent_on + MATCH_WINDOW:
        return "line_outside_window"
    return None


def match_window(sent_on: date) -> tuple[date, date]:
    """The statement days a transfer sent on `sent_on` can appear on."""
    return sent_on, sent_on + MATCH_WINDOW
