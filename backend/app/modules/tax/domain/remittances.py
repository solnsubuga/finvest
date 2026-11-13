"""Paying URA the tax withheld in a month, recorded by one member of staff and approved by a
different one. Pure.

The payment is recorded once it has left the trust account: the proposer gives URA's payment
registration number (PRN), the bank's reference and the day the money left. The second person
checks those against URA's receipt and the bank statement, and approving posts it: the trust
account and `tax.withholding_payable` go down together. Nothing more than a month still owes can
be recorded against it, and the ledger refuses to take `tax.withholding_payable` below zero.
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
from app.modules.tax.domain.periods import month_end
from app.modules.tax.domain.withholding import TAX_PAYABLE
from app.shared.money import Currency, Money

BANK_TRUST = "bank.trust"
SOURCE_TYPE = "tax.remittance"
ENTRY_TYPE = "tax.remitted"
MAX_NOTE_LENGTH = 500
MAX_REFERENCE_LENGTH = 100


class RemittanceStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    DECLINED = "declined"


class Refusal(StrEnum):
    NOTE_REQUIRED = "note_required"
    AMOUNT_NOT_POSITIVE = "amount_not_positive"
    URA_REFERENCE_REQUIRED = "ura_reference_required"
    BANK_REFERENCE_REQUIRED = "bank_reference_required"
    # The month has not ended, so its figures can still change.
    MONTH_NOT_ENDED = "month_not_ended"
    PAID_IN_THE_FUTURE = "paid_in_the_future"
    PAID_BEFORE_THE_MONTH = "paid_before_the_month"
    MORE_THAN_OUTSTANDING = "more_than_outstanding"
    # The ledger refused: the trust account or the tax held could not cover it.
    NOT_ENOUGH_HELD = "not_enough_held"
    REASON_REQUIRED = "reason_required"
    ALREADY_DECIDED = "already_decided"
    SAME_PERSON = "same_person"


@dataclass(frozen=True, slots=True)
class Proposal:
    month: date  # the first day of the month the tax was withheld in
    amount: Money
    ura_reference: str  # URA's payment registration number
    bank_reference: str
    paid_on: date
    note: str


def check_proposal(proposal: Proposal, today: date) -> Refusal | None:
    note = proposal.note.strip()
    if not note or len(note) > MAX_NOTE_LENGTH:
        return Refusal.NOTE_REQUIRED
    if not proposal.amount.is_positive:
        return Refusal.AMOUNT_NOT_POSITIVE
    ura = proposal.ura_reference.strip()
    if not ura or len(ura) > MAX_REFERENCE_LENGTH:
        return Refusal.URA_REFERENCE_REQUIRED
    bank = proposal.bank_reference.strip()
    if not bank or len(bank) > MAX_REFERENCE_LENGTH:
        return Refusal.BANK_REFERENCE_REQUIRED
    if month_end(proposal.month) >= today:
        return Refusal.MONTH_NOT_ENDED
    if proposal.paid_on > today:
        return Refusal.PAID_IN_THE_FUTURE
    if proposal.paid_on < proposal.month:
        return Refusal.PAID_BEFORE_THE_MONTH
    return None


def check_outstanding(amount: Money, outstanding: Money) -> Refusal | None:
    """`outstanding` is what the month still owes before this remittance, counting the ones
    already approved and those waiting."""
    if amount > outstanding:
        return Refusal.MORE_THAN_OUTSTANDING
    return None


def check_decision(status: RemittanceStatus, proposed_by: str, decided_by: str) -> Refusal | None:
    if status is not RemittanceStatus.PROPOSED:
        return Refusal.ALREADY_DECIDED
    if proposed_by == decided_by:
        return Refusal.SAME_PERSON
    return None


def accounts(currency: Currency) -> dict[str, AccountSpec]:
    """The system accounts a remittance touches, by code. Opening one twice is safe."""
    return {
        BANK_TRUST: AccountSpec(BANK_TRUST, AccountKind.ASSET, currency, "Trust bank account"),
        TAX_PAYABLE: AccountSpec(
            TAX_PAYABLE, AccountKind.LIABILITY, currency, "Tax withheld from interest"
        ),
    }


def posting(
    remittance_id: str,
    amount: Money,
    paid_on: date,
    *,
    payable_account_id: str,
    bank_account_id: str,
) -> EntryDraft:
    """The entry an approved remittance posts, dated the day the money left."""
    return EntryDraft(
        ENTRY_TYPE,
        paid_on,
        (debit(payable_account_id, amount), credit(bank_account_id, amount)),
        f"tax-remittance-{remittance_id}",
        source_type=SOURCE_TYPE,
        source_id=remittance_id,
    )
