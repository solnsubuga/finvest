"""The rules for taking back a deposit credited by mistake. Pure: no database, no clock.

A deposit was confirmed but its money never arrived. While the whole amount still waits in the
client's awaiting-start account for the investment, the deposit's own entry is reversed. One
member of staff proposes and a different one approves; the worker checks again and moves the
money. If the money has moved on, it is unwound first (investments corrections) or, once it
has left Finvest, recorded as a receivable instead. See docs/payments.md.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.modules.payments.domain.deposits import DepositStatus, Purpose
from app.shared.money import Money

CONFIRMED_ENTRY_TYPE = "payment.deposit_confirmed"


class ReversalStatus(StrEnum):
    PROPOSED = "proposed"
    # Approved by a second person. The worker carries it out.
    APPROVED = "approved"
    SENT_BACK = "sent_back"
    WITHDRAWN = "withdrawn"
    DONE = "done"
    # Approved, but the worker found the money or the deposit had changed. Nothing moved.
    FAILED = "failed"

    @property
    def is_open(self) -> bool:
        return self is ReversalStatus.PROPOSED


class Refusal(StrEnum):
    """Why a deposit cannot be reversed now."""

    NOT_CONFIRMED = "not_confirmed"
    NOT_FOR_AN_INVESTMENT = "not_for_an_investment"
    ALREADY_REVERSED = "already_reversed"
    # The deposit was saved by a reassignment, which is undone by reassigning it, not here.
    NOT_A_PAYMENT = "not_a_payment"
    # The whole amount is no longer waiting for the investment: it started (unwind it first),
    # or the money was refunded or moved. A receivable is used once the money has left.
    MONEY_MOVED_ON = "money_moved_on"


@dataclass(frozen=True, slots=True)
class Facts:
    status: DepositStatus
    purpose: Purpose
    reversed: bool
    # The entry type of the deposit's ledger entry, None if it has none.
    entry_type: str | None
    amount: Money
    # What waits in the client's awaiting-start account for the investment now.
    waiting: Money


def refusal(facts: Facts) -> Refusal | None:
    """The first reason the deposit cannot be reversed, or None if it can."""
    if facts.status is not DepositStatus.CONFIRMED:
        return Refusal.NOT_CONFIRMED
    if facts.purpose is not Purpose.INVESTMENT:
        return Refusal.NOT_FOR_AN_INVESTMENT
    if facts.reversed:
        return Refusal.ALREADY_REVERSED
    if facts.entry_type != CONFIRMED_ENTRY_TYPE:
        return Refusal.NOT_A_PAYMENT
    if facts.waiting.currency != facts.amount.currency or facts.waiting < facts.amount:
        return Refusal.MONEY_MOVED_ON
    return None
