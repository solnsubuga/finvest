"""Proposing, approving and rejecting ledger corrections, inside the caller's transaction.

Each function runs in a staff transaction (`Database.actor_transaction(staff)`), and the staff
member passed in must be that transaction's actor: the database refuses a proposal or decision
written in anyone else's name. It also refuses a decision by the proposer, a second decision,
and an approved entry that differs from the proposal (migration 0013).

Every step writes an audit event in the same transaction.
"""

import json
from datetime import date
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.corrections import (
    CorrectionDecision,
    CorrectionProposal,
    build_proposal,
    clean_reason,
    reversal_lines,
    staff_id,
)
from app.modules.ledger.domain.entries import InvalidEntry, Line
from app.modules.ledger.domain.errors import (
    AlreadyReversed,
    CorrectionAlreadyDecided,
    SecondPersonRequired,
    UnknownCorrection,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

_INSERT_PROPOSAL = text(
    "INSERT INTO ledger_correction_proposals (id, corrects_entry_id, reverses, entry_type, "
    "currency, effective_date, lines, reason, proposed_by, proposed_at) VALUES (:id, "
    ":corrects_entry_id, :reverses, :entry_type, :currency, :effective_date, "
    "CAST(:lines AS jsonb), :reason, :proposed_by, :proposed_at)"
)
_PROPOSAL_COLUMNS = (
    "id, corrects_entry_id, reverses, entry_type, currency, effective_date, lines, reason, "
    "proposed_by, proposed_at"
)
_SELECT_PROPOSAL = text(
    f"SELECT {_PROPOSAL_COLUMNS} FROM ledger_correction_proposals WHERE id = :id"  # noqa: S608
)
_SELECT_PENDING = text(
    f"SELECT {_PROPOSAL_COLUMNS} FROM ledger_correction_proposals AS proposal "  # noqa: S608
    "WHERE NOT EXISTS (SELECT FROM ledger_correction_decisions AS decision "
    "WHERE decision.proposal_id = proposal.id) ORDER BY proposed_at, id LIMIT :limit"
)
_INSERT_DECISION = text(
    "INSERT INTO ledger_correction_decisions (proposal_id, outcome, decided_by, decided_at, "
    "reason, entry_id) VALUES (:proposal_id, :outcome, :decided_by, :decided_at, :reason, "
    ":entry_id)"
)
_SELECT_DECISION = text(
    "SELECT proposal_id, outcome, decided_by, decided_at, reason, entry_id "
    "FROM ledger_correction_decisions WHERE proposal_id = :proposal_id"
)


async def propose_correction(
    session: AsyncSession,
    proposer: Actor,
    clock: Clock,
    *,
    corrects_entry_id: str,
    effective_date: date,
    reason: str,
    entry_type: str = "ledger.correction",
    lines: tuple[Line, ...] | None = None,
    reverse: bool = False,
    correlation_id: str | None = None,
) -> CorrectionProposal:
    """Record that `proposer` asks for a correcting entry. Nothing is posted yet.

    Give `lines` for a forward correction, which takes the money back from where it is now. Or
    pass `reverse=True` with no lines for an exact reversal of `corrects_entry_id`, when the
    money has not moved.
    """
    original = await ledger.get_entry(session, corrects_entry_id)
    if reverse:
        if lines is not None:
            raise InvalidEntry("A reversal takes its lines from the entry it reverses")
        if original.reverses_entry_id is not None:
            raise AlreadyReversed("A reversal cannot itself be reversed")
        lines = reversal_lines(original.lines)
    elif lines is None:
        raise InvalidEntry("A correction needs lines, or reverse=True")
    proposal = build_proposal(
        proposal_id=push_id(),
        proposer=proposer,
        corrects_entry_id=original.id,
        entry_type=entry_type,
        effective_date=effective_date,
        lines=lines,
        reason=reason,
        proposed_at=clock.now(),
        reverses=reverse,
    )
    await session.execute(
        _INSERT_PROPOSAL,
        {
            "id": proposal.id,
            "corrects_entry_id": proposal.corrects_entry_id,
            "reverses": proposal.reverses,
            "entry_type": proposal.entry_type,
            "currency": proposal.draft().currency.value,
            "effective_date": proposal.effective_date,
            "lines": json.dumps(
                [
                    {"account_id": line.account_id, "amount_minor": line.amount.minor}
                    for line in proposal.lines
                ]
            ),
            "reason": proposal.reason,
            "proposed_by": proposal.proposed_by,
            "proposed_at": proposal.proposed_at,
        },
    )
    await _audit(
        session,
        clock,
        proposer,
        "change",
        "ledger.correction_proposed",
        proposal,
        proposal.reason,
        correlation_id,
    )
    return proposal


async def approve_correction(
    session: AsyncSession,
    approver: Actor,
    proposal_id: str,
    clock: Clock,
    *,
    reason: str = "Approved as proposed",
    correlation_id: str | None = None,
) -> ledger.PostedEntry:
    """Post the proposed entry, as a staff member other than the proposer.

    Raises `SecondPersonRequired` for the proposer, `CorrectionAlreadyDecided` if someone got
    there first, and the usual posting errors, such as `InsufficientFunds`, if the entry no
    longer fits the books. After any of them nothing has been posted or decided, and the
    caller's transaction is still usable.
    """
    decided_by = staff_id(approver)
    proposal = await get_correction(session, proposal_id)
    if proposal.proposed_by == decided_by:
        raise SecondPersonRequired("A correction is approved by a second person")
    note = clean_reason(reason)
    try:
        async with session.begin_nested():
            posted = await ledger.post_entry(
                session, proposal.draft(), clock, correlation_id=correlation_id
            )
            if posted.replayed:
                raise CorrectionAlreadyDecided("The correction was already approved")
            await session.execute(
                _INSERT_DECISION,
                {
                    "proposal_id": proposal.id,
                    "outcome": "approved",
                    "decided_by": decided_by,
                    "decided_at": clock.now(),
                    "reason": note,
                    "entry_id": posted.id,
                },
            )
    except IntegrityError as error:
        raise _translate(error) from None
    await _audit(
        session,
        clock,
        approver,
        "approval",
        "ledger.correction_approved",
        proposal,
        note,
        correlation_id,
    )
    return posted


async def reject_correction(
    session: AsyncSession,
    approver: Actor,
    proposal_id: str,
    reason: str,
    clock: Clock,
    *,
    correlation_id: str | None = None,
) -> CorrectionDecision:
    """Close a proposal without posting anything. A reason is required."""
    decided_by = staff_id(approver)
    proposal = await get_correction(session, proposal_id)
    if proposal.proposed_by == decided_by:
        raise SecondPersonRequired("A correction is rejected by a second person")
    decision = CorrectionDecision(
        proposal_id=proposal.id,
        outcome="rejected",
        decided_by=decided_by,
        decided_at=clock.now(),
        reason=clean_reason(reason),
        entry_id=None,
    )
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT_DECISION,
                {
                    "proposal_id": decision.proposal_id,
                    "outcome": decision.outcome,
                    "decided_by": decision.decided_by,
                    "decided_at": decision.decided_at,
                    "reason": decision.reason,
                    "entry_id": None,
                },
            )
    except IntegrityError as error:
        raise _translate(error) from None
    await _audit(
        session,
        clock,
        approver,
        "approval",
        "ledger.correction_rejected",
        proposal,
        decision.reason,
        correlation_id,
    )
    return decision


async def get_correction(session: AsyncSession, proposal_id: str) -> CorrectionProposal:
    row = (await session.execute(_SELECT_PROPOSAL, {"id": proposal_id})).one_or_none()
    if row is None:
        raise UnknownCorrection("No such correction proposal")
    return _proposal(row)


async def get_decision(session: AsyncSession, proposal_id: str) -> CorrectionDecision | None:
    """How the proposal was decided, or None while it is still waiting."""
    row = (await session.execute(_SELECT_DECISION, {"proposal_id": proposal_id})).one_or_none()
    if row is None:
        return None
    return CorrectionDecision(
        proposal_id=row.proposal_id,
        outcome=row.outcome,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        reason=row.reason,
        entry_id=row.entry_id,
    )


async def pending_corrections(
    session: AsyncSession, *, limit: int = DEFAULT_PAGE_SIZE
) -> list[CorrectionProposal]:
    """Proposals nobody has decided yet, oldest first. For the staff review queue."""
    rows = await session.execute(_SELECT_PENDING, {"limit": max(1, min(limit, MAX_PAGE_SIZE))})
    return [_proposal(row) for row in rows.all()]


def _proposal(row: Row[Any]) -> CorrectionProposal:
    currency = Currency(row.currency)
    return CorrectionProposal(
        id=row.id,
        corrects_entry_id=row.corrects_entry_id,
        reverses=row.reverses,
        entry_type=row.entry_type,
        effective_date=row.effective_date,
        lines=tuple(
            Line(str(line["account_id"]), Money(int(line["amount_minor"]), currency))
            for line in row.lines
        ),
        reason=row.reason,
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
    )


async def _audit(
    session: AsyncSession,
    clock: Clock,
    actor: Actor,
    category: AuditCategory,
    action: str,
    proposal: CorrectionProposal,
    reason: str | None,
    correlation_id: str | None,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=action,
            resource_type="ledger_correction",
            resource_id=proposal.id,
            actor=AuditActor.staff(staff_id(actor)),
            reason=reason,
            correlation_id=correlation_id,
            details={
                "corrects_entry_id": proposal.corrects_entry_id,
                "entry_type": proposal.entry_type,
                "reverses": proposal.reverses,
                "line_count": len(proposal.lines),
            },
        ),
        clock,
    )


def _translate(error: IntegrityError) -> Exception:
    constraint = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
    if constraint == "pk_ledger_correction_decisions":
        return CorrectionAlreadyDecided("The correction was already decided")
    if constraint == "ledger_correction_second_person":
        return SecondPersonRequired("A correction is decided by a second person")
    return error
