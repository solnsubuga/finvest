"""The rules for moving a deposit credited to the wrong client. Pure: no database, no clock.

A reassignment moves the whole of one confirmed bank transfer from the client it was credited
to, to the client whose money it was, for an investment of theirs that is waiting for exactly
that amount. One member of staff proposes and a different one approves. See docs/payments.md.

Mobile money is never reassigned. The provider took it from the first client's own number, so
crediting it to anyone else would accept a payment from a number registered to someone else,
which the owner's rule refuses. That money goes back to the number it came from, as a refund.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.modules.payments.domain.deposits import DepositStatus, Method, Purpose
from app.shared.money import Money

MAX_TEXT_LENGTH = 500


class ReassignmentStatus(StrEnum):
    PROPOSED = "proposed"
    # Approved by a second person, and the money moved in the same transaction.
    APPLIED = "applied"
    SENT_BACK = "sent_back"
    WITHDRAWN = "withdrawn"

    @property
    def is_open(self) -> bool:
        return self is ReassignmentStatus.PROPOSED


class Refusal(StrEnum):
    """Why a deposit cannot be moved to this investment. Each names the field it is about."""

    # The deposit.
    MOBILE_MONEY_REFUND_INSTEAD = "mobile_money_refund_instead"
    NOT_CONFIRMED = "not_confirmed"
    NOT_FOR_AN_INVESTMENT = "not_for_an_investment"
    NOT_MATCHED_TO_A_LINE = "not_matched_to_a_line"
    # The money is no longer waiting with the first client: the investment started, or the
    # money was refunded or moved. Undo the investment first (investments corrections).
    MONEY_MOVED_ON = "money_moved_on"
    # The right client's investment.
    SAME_CLIENT = "same_client"
    NOT_WAITING_FOR_PAYMENT = "not_waiting_for_payment"
    AMOUNT_DIFFERS_FROM_INVESTMENT = "amount_differs_from_investment"
    ALREADY_HAS_A_PAYMENT = "already_has_a_payment"

    @property
    def field(self) -> str:
        return "deposit_id" if self in _ABOUT_THE_DEPOSIT else "to_investment_id"


_ABOUT_THE_DEPOSIT = frozenset(
    {
        Refusal.MOBILE_MONEY_REFUND_INSTEAD,
        Refusal.NOT_CONFIRMED,
        Refusal.NOT_FOR_AN_INVESTMENT,
        Refusal.NOT_MATCHED_TO_A_LINE,
        Refusal.MONEY_MOVED_ON,
    }
)


@dataclass(frozen=True, slots=True)
class Facts:
    """What is true now about the deposit and the investment it would move to."""

    method: Method
    status: DepositStatus
    purpose: Purpose
    amount: Money
    from_client_id: str
    # The statement line the deposit's review matched. None if it has none.
    matched_line_id: str | None
    # What is still in the first client's money awaiting start for the first investment.
    still_waiting: Money
    to_client_id: str
    # The right client's investment can be paid for now: not started, cancelled or expired.
    to_payable: bool
    to_amount: Money
    to_has_payment: bool


def refusal(facts: Facts) -> Refusal | None:
    """The first reason this move cannot happen, or None. Checked on proposal and again on
    approval, because the money or the investment may have changed in between."""
    if facts.method is not Method.BANK_TRANSFER:
        return Refusal.MOBILE_MONEY_REFUND_INSTEAD
    if facts.status is not DepositStatus.CONFIRMED:
        return Refusal.NOT_CONFIRMED
    if facts.purpose is not Purpose.INVESTMENT:
        return Refusal.NOT_FOR_AN_INVESTMENT
    if facts.matched_line_id is None:
        return Refusal.NOT_MATCHED_TO_A_LINE
    if facts.still_waiting.currency is not facts.amount.currency or (
        facts.still_waiting.minor < facts.amount.minor
    ):
        return Refusal.MONEY_MOVED_ON
    if facts.to_client_id == facts.from_client_id:
        return Refusal.SAME_CLIENT
    if not facts.to_payable:
        return Refusal.NOT_WAITING_FOR_PAYMENT
    if facts.to_amount != facts.amount:
        return Refusal.AMOUNT_DIFFERS_FROM_INVESTMENT
    if facts.to_has_payment:
        return Refusal.ALREADY_HAS_A_PAYMENT
    return None


# Old Finvest 1.0 numbers can be short. Finvest account numbers are ten digits.
MIN_REFERENCE_DIGITS = 6


def clean_text(value: str) -> str | None:
    """A reason or note, trimmed, or None if it is empty or too long."""
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > MAX_TEXT_LENGTH:
        return None
    return cleaned


def reference_names(reference: str, *account_numbers: str | None) -> bool:
    """Whether a statement line's reference carries one of this client's account numbers: their
    Finvest account number, or the Finvest 1.0 one a carried-over client may still write.

    Clients are asked to write their account number on a transfer. Banks cut and change
    references, so this is a hint shown to staff, never a rule. A number shorter than
    `MIN_REFERENCE_DIGITS` letters and digits would be found inside too many references to mean
    anything, so it never matches.
    """
    squeeze = "".join(ch for ch in reference.upper() if ch.isalnum())
    for account_number in account_numbers:
        wanted = "".join(ch for ch in (account_number or "").upper() if ch.isalnum())
        if len(wanted) >= MIN_REFERENCE_DIGITS and wanted in squeeze:
            return True
    return False
