"""Corrections: entries that need two people. Pure: no I/O.

A reversal is refused once the money has moved on, because it would overdraw an account. What
puts that right is a new entry that takes the money back from where it sits now, and choosing
its lines is a judgement. So one staff member proposes the entry with a reason, and a different
staff member approves or rejects it. Only an approval posts anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from app.modules.audit.domain.events import redact_text
from app.modules.ledger.domain.entries import EntryDraft, InvalidEntry, Line
from app.shared.actors import Actor
from app.shared.push_ids import validate_push_id

CORRECTION_SOURCE = "ledger.correction"
MAX_REASON_LENGTH = 500

Outcome = Literal["approved", "rejected"]


@dataclass(frozen=True, slots=True)
class CorrectionProposal:
    """What one staff member asked for. Never changed once written."""

    id: str
    corrects_entry_id: str
    reverses: bool
    entry_type: str
    effective_date: date
    lines: tuple[Line, ...]
    reason: str
    proposed_by: str
    proposed_at: datetime

    def draft(self) -> EntryDraft:
        """The entry an approval posts. Built only from the proposal, so the approver cannot
        change it, and keyed by the proposal so a retried approval posts once."""
        return EntryDraft(
            entry_type=self.entry_type,
            effective_date=self.effective_date,
            lines=self.lines,
            idempotency_key=f"{CORRECTION_SOURCE}:{self.id}",
            source_type=CORRECTION_SOURCE,
            source_id=self.id,
            reverses_entry_id=self.corrects_entry_id if self.reverses else None,
        )


@dataclass(frozen=True, slots=True)
class CorrectionDecision:
    proposal_id: str
    outcome: Outcome
    decided_by: str
    decided_at: datetime
    reason: str | None
    entry_id: str | None


def staff_id(actor: Actor) -> str:
    """Corrections are made and decided by people on the staff, never by a client or a job."""
    if actor.kind != "staff" or actor.id is None:
        raise InvalidEntry("Only a staff member can propose or decide a correction")
    return actor.id


def clean_reason(reason: str) -> str:
    """A reason is required, bounded, and stored without phone numbers or other personal data."""
    if not isinstance(reason, str) or not reason.strip():
        raise InvalidEntry("A correction needs a reason")
    return redact_text(reason.strip(), limit=MAX_REASON_LENGTH)


def build_proposal(
    *,
    proposal_id: str,
    proposer: Actor,
    corrects_entry_id: str,
    entry_type: str,
    effective_date: date,
    lines: tuple[Line, ...],
    reason: str,
    proposed_at: datetime,
    reverses: bool = False,
) -> CorrectionProposal:
    """Validate a proposal by building the entry it would post."""
    try:
        validate_push_id(corrects_entry_id)
    except ValueError as error:
        raise InvalidEntry("A correction must name the entry it corrects") from error
    proposal = CorrectionProposal(
        id=proposal_id,
        corrects_entry_id=corrects_entry_id,
        reverses=reverses,
        entry_type=entry_type,
        effective_date=effective_date,
        lines=lines,
        reason=clean_reason(reason),
        proposed_by=staff_id(proposer),
        proposed_at=proposed_at,
    )
    proposal.draft()  # raises InvalidEntry if the lines do not make a valid entry
    return proposal


def reversal_lines(original_lines: tuple[Line, ...]) -> tuple[Line, ...]:
    """The exact mirror of an entry, for a correction that is a plain reversal."""
    return tuple(Line(line.account_id, -line.amount) for line in original_lines)
