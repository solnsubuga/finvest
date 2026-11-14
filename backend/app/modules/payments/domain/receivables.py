"""The rules for what a client owes for a deposit credited by mistake. Pure: no database.

When the money a mistaken deposit credited has already left Finvest, nothing can be reversed.
The amount is recorded as owed by the client (opened), then cleared once: recovered when the
money arrives late, or written off. Each step is proposed by one member of staff and approved by
another. See docs/payments.md, "Correcting a mistaken credit".
"""

from dataclasses import dataclass
from enum import StrEnum

from app.modules.payments.domain.deposits import DepositStatus, Purpose
from app.shared.money import Money

CONFIRMED_ENTRY_TYPE = "payment.deposit_confirmed"


class StepKind(StrEnum):
    OPEN = "open"
    RECOVER = "recover"
    WRITE_OFF = "write_off"

    @property
    def entry_type(self) -> str:
        return _ENTRY_TYPES[self]


_ENTRY_TYPES = {
    StepKind.OPEN: "payment.deposit_correction",
    StepKind.RECOVER: "payment.receivable_recovered",
    StepKind.WRITE_OFF: "payment.receivable_written_off",
}


class StepStatus(StrEnum):
    PROPOSED = "proposed"
    # Approved by a second person, and the entry posted in the same transaction.
    APPLIED = "applied"
    SENT_BACK = "sent_back"
    WITHDRAWN = "withdrawn"

    @property
    def is_open(self) -> bool:
        return self is StepStatus.PROPOSED


class Refusal(StrEnum):
    NOT_CONFIRMED = "not_confirmed"
    NOT_FOR_AN_INVESTMENT = "not_for_an_investment"
    ALREADY_REVERSED = "already_reversed"
    NOT_A_PAYMENT = "not_a_payment"
    # The investment is still running: unwind it, then reverse the deposit.
    UNWIND_FIRST = "unwind_first"
    # The whole amount still waits for the investment: reverse the deposit instead.
    REVERSE_INSTEAD = "reverse_instead"
    ALREADY_OPEN = "already_open"
    NOT_OPEN = "not_open"
    ALREADY_CLEARED = "already_cleared"


@dataclass(frozen=True, slots=True)
class Facts:
    status: DepositStatus
    purpose: Purpose
    reversed: bool
    entry_type: str | None
    amount: Money
    # What waits in the client's awaiting-start account for the investment now.
    waiting: Money
    investment_running: bool
    opened: bool
    cleared: bool


def refusal(kind: StepKind, facts: Facts) -> Refusal | None:
    """The first reason this step cannot be taken now, or None if it can."""
    if facts.status is not DepositStatus.CONFIRMED:
        return Refusal.NOT_CONFIRMED
    if facts.purpose is not Purpose.INVESTMENT:
        return Refusal.NOT_FOR_AN_INVESTMENT
    if facts.reversed:
        return Refusal.ALREADY_REVERSED
    if facts.entry_type != CONFIRMED_ENTRY_TYPE:
        return Refusal.NOT_A_PAYMENT
    if kind is StepKind.OPEN:
        if facts.opened:
            return Refusal.ALREADY_OPEN
        if facts.investment_running:
            return Refusal.UNWIND_FIRST
        if facts.waiting.currency == facts.amount.currency and facts.waiting >= facts.amount:
            return Refusal.REVERSE_INSTEAD
        return None
    if not facts.opened:
        return Refusal.NOT_OPEN
    if facts.cleared:
        return Refusal.ALREADY_CLEARED
    return None
