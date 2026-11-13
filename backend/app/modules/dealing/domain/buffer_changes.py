"""Changing the liquidity buffer rules: one member of staff proposes, another approves. Pure.

A change takes effect on a later day than the one it is proposed or approved on, so the rules
in force today never change under the desk's feet. Two versions never start on the same day.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.modules.dealing.domain.liquidity import BufferRules
from app.shared.money import Currency

MAX_REASON_LENGTH = 500

# The rules Finvest starts with, from the canvas. Working values until the owner confirms them
# (docs/decisions.md). A migration seeds them, and a test compares the two.
SEED_RULES: dict[Currency, tuple[int, int, int, int]] = {
    # target, floor, from the buffer up to, paid within working days
    Currency.UGX: (150_000_000, 100_000_000, 5_000_000, 2),
}


class ProposalStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    SENT_BACK = "sent_back"
    WITHDRAWN = "withdrawn"

    @property
    def is_final(self) -> bool:
        return self is not ProposalStatus.PROPOSED


class Refusal(StrEnum):
    REASON_REQUIRED = "reason_required"
    EFFECTIVE_DATE_NOT_IN_FUTURE = "effective_date_not_in_future"
    # The rules already in force on that date are the same.
    NOTHING_CHANGES = "nothing_changes"
    DATE_ALREADY_TAKEN = "date_already_taken"
    ALREADY_DECIDED = "already_decided"
    SAME_PERSON = "same_person"
    NOT_THE_PROPOSER = "not_the_proposer"


@dataclass(frozen=True, slots=True)
class RulesProposal:
    rules: BufferRules
    proposed_by: str
    reason: str
    status: ProposalStatus = ProposalStatus.PROPOSED


def check_proposal(
    rules: BufferRules, reason: str, existing: Iterable[BufferRules], today: date
) -> Refusal | None:
    """Whether a new proposal may be saved. `existing` is the approved versions in its currency."""
    if not valid_reason(reason):
        return Refusal.REASON_REQUIRED
    if rules.effective_from <= today:
        return Refusal.EFFECTIVE_DATE_NOT_IN_FUTURE
    return _clash(rules, list(existing))


def check_approval(
    proposal: RulesProposal, approver: str, existing: Iterable[BufferRules], today: date
) -> Refusal | None:
    """Whether `approver` may approve today. The date and clashes are checked again, because
    days may pass and other versions may be approved in between."""
    if proposal.status.is_final:
        return Refusal.ALREADY_DECIDED
    if approver == proposal.proposed_by:
        return Refusal.SAME_PERSON
    if proposal.rules.effective_from <= today:
        return Refusal.EFFECTIVE_DATE_NOT_IN_FUTURE
    return _clash(proposal.rules, list(existing))


def check_send_back(proposal: RulesProposal, by: str, note: str) -> Refusal | None:
    if proposal.status.is_final:
        return Refusal.ALREADY_DECIDED
    if by == proposal.proposed_by:
        return Refusal.SAME_PERSON
    return None if valid_reason(note) else Refusal.REASON_REQUIRED


def check_withdrawal(proposal: RulesProposal, by: str) -> Refusal | None:
    if proposal.status.is_final:
        return Refusal.ALREADY_DECIDED
    return None if by == proposal.proposed_by else Refusal.NOT_THE_PROPOSER


def valid_reason(text: str) -> bool:
    return isinstance(text, str) and len(text.strip()) > 0 and len(text) <= MAX_REASON_LENGTH


def _clash(rules: BufferRules, existing: list[BufferRules]) -> Refusal | None:
    if any(version.effective_from == rules.effective_from for version in existing):
        return Refusal.DATE_ALREADY_TAKEN
    earlier = [version for version in existing if version.effective_from < rules.effective_from]
    if earlier:
        before = max(earlier, key=lambda version: version.effective_from)
        if (
            before.target,
            before.floor,
            before.from_buffer_up_to,
            before.pay_within_working_days,
        ) == (
            rules.target,
            rules.floor,
            rules.from_buffer_up_to,
            rules.pay_within_working_days,
        ):
            return Refusal.NOTHING_CHANGES
    return None
