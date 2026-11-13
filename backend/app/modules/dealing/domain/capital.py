"""Finvest's own money in the liquidity buffer: paid into the trust account, or taken back out.
One member of staff proposes, a different one approves. Pure.

Finvest's own money is the only cash the buffer has beyond client money (docs/decisions.md,
2026-11-13). It sits in the trust account and is booked to `dealing.buffer_capital`, an equity
account, so the buffer counts it and no client owns it.

* Paying in is recorded once the money is on the trust account: the proposer gives the bank's
  reference and the value date, and the second person checks it against the statement.
* Taking out is approved before the money leaves. Approval posts it on the approval's business
  day, and the transfer is then made quoting the movement's ID, so the statement matches it.
  Only what Finvest paid in can come out, and never more than the buffer holds, so a payout
  owed to a client is never paid with their own money leaving for Finvest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.modules.ledger.domain.entries import (
    AccountKind,
    AccountSpec,
    EntryDraft,
    credit,
    debit,
)
from app.shared.money import Currency, Money

BANK_TRUST = "bank.trust"
CAPITAL = "dealing.buffer_capital"
SOURCE_TYPE = "dealing.buffer_capital"
PAID_IN_ENTRY = "dealing.buffer_capital_in"
TAKEN_OUT_ENTRY = "dealing.buffer_capital_out"
MAX_REASON_LENGTH = 500
MAX_REFERENCE_LENGTH = 100


class Direction(StrEnum):
    IN = "in"
    OUT = "out"


class MovementStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    DECLINED = "declined"


class Refusal(StrEnum):
    REASON_REQUIRED = "reason_required"
    AMOUNT_NOT_POSITIVE = "amount_not_positive"
    # Paying in needs the bank's reference and the day it arrived, today or earlier.
    REFERENCE_REQUIRED = "reference_required"
    VALUE_DATE_REQUIRED = "value_date_required"
    VALUE_DATE_IN_FUTURE = "value_date_in_future"
    # Taking out is dated on approval and quotes the movement's own ID.
    NOT_FOR_TAKING_OUT = "not_for_taking_out"
    MORE_THAN_PAID_IN = "more_than_paid_in"
    MORE_THAN_THE_BUFFER = "more_than_the_buffer"
    ALREADY_DECIDED = "already_decided"
    SAME_PERSON = "same_person"


@dataclass(frozen=True, slots=True)
class Proposal:
    direction: Direction
    amount: Money
    reason: str
    # Paying in only: the bank's reference and the day the money arrived.
    bank_reference: str | None
    value_date: date | None


def check_proposal(proposal: Proposal, today: date) -> Refusal | None:
    if not proposal.reason.strip() or len(proposal.reason.strip()) > MAX_REASON_LENGTH:
        return Refusal.REASON_REQUIRED
    if not proposal.amount.is_positive:
        return Refusal.AMOUNT_NOT_POSITIVE
    reference = (proposal.bank_reference or "").strip()
    if proposal.direction is Direction.OUT:
        if reference or proposal.value_date is not None:
            return Refusal.NOT_FOR_TAKING_OUT
        return None
    if not reference or len(reference) > MAX_REFERENCE_LENGTH:
        return Refusal.REFERENCE_REQUIRED
    if proposal.value_date is None:
        return Refusal.VALUE_DATE_REQUIRED
    if proposal.value_date > today:
        return Refusal.VALUE_DATE_IN_FUTURE
    return None


def check_decision(status: MovementStatus, proposed_by: str, decided_by: str) -> Refusal | None:
    if status is not MovementStatus.PROPOSED:
        return Refusal.ALREADY_DECIDED
    if proposed_by == decided_by:
        return Refusal.SAME_PERSON
    return None


def check_taking_out(amount: Money, paid_in: Money, buffer: Money) -> Refusal | None:
    """Whether `amount` may leave now. `paid_in` is the balance of `dealing.buffer_capital` and
    `buffer` the buffer today, both before it leaves."""
    if amount > paid_in:
        return Refusal.MORE_THAN_PAID_IN
    if amount > buffer:
        return Refusal.MORE_THAN_THE_BUFFER
    return None


def accounts(currency: Currency) -> dict[str, AccountSpec]:
    """The system accounts a movement touches, by code. Opening one twice is safe."""
    return {
        BANK_TRUST: AccountSpec(BANK_TRUST, AccountKind.ASSET, currency, "Trust bank account"),
        CAPITAL: AccountSpec(
            CAPITAL, AccountKind.EQUITY, currency, "Finvest's own money in the buffer"
        ),
    }


def posting(
    movement_id: str,
    direction: Direction,
    amount: Money,
    value_date: date,
    *,
    bank_account_id: str,
    capital_account_id: str,
) -> EntryDraft:
    """The entry an approved movement posts: the trust account and Finvest's money move together."""
    if direction is Direction.IN:
        lines = (debit(bank_account_id, amount), credit(capital_account_id, amount))
        entry_type = PAID_IN_ENTRY
    else:
        lines = (debit(capital_account_id, amount), credit(bank_account_id, amount))
        entry_type = TAKEN_OUT_ENTRY
    return EntryDraft(
        entry_type,
        value_date,
        lines,
        f"dealing-buffer-capital-{movement_id}",
        source_type=SOURCE_TYPE,
        source_id=movement_id,
    )
