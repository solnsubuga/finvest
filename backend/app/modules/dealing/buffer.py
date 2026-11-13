"""The liquidity buffer rules and their two-person changes. Works in the caller's transaction.

The caller opens `Database.actor_transaction(actor)` for the same actor it passes here. The
triggers of migration 0083 hold whatever this code does: nobody approves their own change, and
a version of the rules exists only for an approved proposal with exactly its values.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.dealing.domain.buffer_changes import (
    ProposalStatus,
    Refusal,
    RulesProposal,
    check_approval,
    check_proposal,
    check_send_back,
    check_withdrawal,
)
from app.modules.dealing.domain.liquidity import BufferRules, InvalidBufferRules, rules_in_force
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

PROPOSAL_RESOURCE = "dealing_buffer_rules_proposal"

_RULE_COLUMNS = (
    "currency, effective_from, target_minor, floor_minor, from_buffer_up_to_minor, "
    "pay_within_working_days"
)
_VERSIONS = text(
    f"SELECT {_RULE_COLUMNS} FROM dealing_buffer_rules "  # noqa: S608
    "WHERE currency = :currency ORDER BY effective_from"
)
_PROPOSAL_COLUMNS = (
    f"id, {_RULE_COLUMNS}, reason, status, proposed_by, proposed_at, decided_by, decided_at, "
    "decision_note"
)
_PROPOSAL = text(
    f"SELECT {_PROPOSAL_COLUMNS} FROM dealing_buffer_rule_proposals WHERE id = :id"  # noqa: S608
)
_PROPOSAL_LOCKED = text(
    f"SELECT {_PROPOSAL_COLUMNS} FROM dealing_buffer_rule_proposals "  # noqa: S608
    "WHERE id = :id FOR UPDATE"
)
_PROPOSALS = text(
    f"SELECT {_PROPOSAL_COLUMNS} FROM dealing_buffer_rule_proposals "  # noqa: S608
    "WHERE currency = :currency ORDER BY proposed_at DESC, id DESC LIMIT :limit"
)
_WAITING = text(
    f"SELECT {_PROPOSAL_COLUMNS} FROM dealing_buffer_rule_proposals "  # noqa: S608
    "WHERE status = 'proposed' ORDER BY proposed_at, id LIMIT :limit"
)
_INSERT_PROPOSAL = text(
    f"INSERT INTO dealing_buffer_rule_proposals (id, {_RULE_COLUMNS}, reason, "  # noqa: S608
    "proposed_by, proposed_at) VALUES (:id, :currency, :effective_from, :target, :floor, "
    ":up_to, :days, :reason, :by, :now)"
)
_DECIDE = text(
    "UPDATE dealing_buffer_rule_proposals SET status = :status, decided_by = :by, "
    "decided_at = :now, decision_note = :note WHERE id = :id"
)
_INSERT_VERSION = text(
    f"INSERT INTO dealing_buffer_rules (id, {_RULE_COLUMNS}, proposal_id, "  # noqa: S608
    "created_at) VALUES (:id, :currency, :effective_from, :target, :floor, :up_to, :days, "
    ":proposal_id, :now)"
)
# One change to the rules of a currency is worked out at a time.
_LOCK_CURRENCY = text(
    "SELECT pg_advisory_xact_lock(hashtext('dealing_buffer_rules'), hashtext(:currency))"
)


@dataclass(frozen=True, slots=True)
class RulesProposalView:
    id: str
    rules: BufferRules
    reason: str
    status: ProposalStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None

    @property
    def proposal(self) -> RulesProposal:
        return RulesProposal(self.rules, self.proposed_by, self.reason, self.status)


# Queries -----------------------------------------------------------------------------------------


async def versions(session: AsyncSession, currency: Currency) -> list[BufferRules]:
    """Every approved version of the rules for a currency, oldest first."""
    rows = await session.execute(_VERSIONS, {"currency": currency.value})
    return [_rules(row) for row in rows.all()]


async def in_force(session: AsyncSession, currency: Currency, on: date) -> BufferRules:
    """The rules in force on `on`. Having none is an error, never "no buffer needed"."""
    try:
        return rules_in_force(await versions(session, currency), on)
    except InvalidBufferRules:
        raise ApplicationError("not_found") from None


async def proposals(
    session: AsyncSession, currency: Currency, *, limit: int = 50
) -> list[RulesProposalView]:
    """Changes to the rules of a currency, newest first."""
    rows = await session.execute(_PROPOSALS, {"currency": currency.value, "limit": limit})
    return [_proposal(row) for row in rows.all()]


async def waiting_proposals(session: AsyncSession, *, limit: int = 100) -> list[RulesProposalView]:
    """Changes to the rules of every currency waiting for a second person, oldest first."""
    rows = await session.execute(_WAITING, {"limit": limit})
    return [_proposal(row) for row in rows.all()]


async def get_proposal(session: AsyncSession, proposal_id: str) -> RulesProposalView:
    row = (await session.execute(_PROPOSAL, {"id": proposal_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _proposal(row)


# Commands ----------------------------------------------------------------------------------------


async def propose_rules(
    session: AsyncSession,
    staff: Actor,
    *,
    effective_from: date,
    target: Money,
    floor: Money,
    from_buffer_up_to: Money,
    pay_within_working_days: int,
    reason: str,
    clock: Clock,
) -> RulesProposalView:
    """Save a change to the rules for a second member of staff to approve. Nothing changes yet."""
    _staff_only(staff)
    try:
        rules = BufferRules(
            effective_from, target, floor, from_buffer_up_to, pay_within_working_days
        )
    except InvalidBufferRules as error:
        raise _invalid("rules", "inconsistent") from error
    await session.execute(_LOCK_CURRENCY, {"currency": rules.currency.value})
    refusal = check_proposal(
        rules, reason, await versions(session, rules.currency), business_date(clock.now())
    )
    if refusal is not None:
        raise _refused(refusal)
    proposal_id = push_id()
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT_PROPOSAL,
                {
                    **_values(rules),
                    "id": proposal_id,
                    "reason": reason.strip(),
                    "by": staff.id,
                    "now": clock.now(),
                },
            )
    except IntegrityError as error:
        # Another change for this currency is already waiting for approval.
        raise ApplicationError("conflict") from error
    await _audit(session, staff, "change", "dealing.buffer_rules.proposed", proposal_id, clock)
    return await get_proposal(session, proposal_id)


async def approve_rules(
    session: AsyncSession, staff: Actor, proposal_id: str, clock: Clock
) -> RulesProposalView:
    """Approve a change as the second person. The new version is saved in this transaction."""
    view = await _for_decision(session, staff, proposal_id)
    await session.execute(_LOCK_CURRENCY, {"currency": view.rules.currency.value})
    refusal = check_approval(
        view.proposal,
        _id(staff),
        await versions(session, view.rules.currency),
        business_date(clock.now()),
    )
    if refusal is not None:
        raise _refused(refusal)
    now = clock.now()
    await session.execute(
        _DECIDE, {"id": view.id, "status": "approved", "by": staff.id, "now": now, "note": None}
    )
    await session.execute(
        _INSERT_VERSION,
        {**_values(view.rules), "id": push_id(), "proposal_id": view.id, "now": now},
    )
    await _audit(
        session,
        staff,
        "approval",
        "dealing.buffer_rules.approved",
        view.id,
        clock,
        reason=view.reason,
    )
    return await get_proposal(session, view.id)


async def send_back_rules(
    session: AsyncSession, staff: Actor, proposal_id: str, note: str, clock: Clock
) -> RulesProposalView:
    """Decline a change as the second person, with a note for the proposer."""
    view = await _for_decision(session, staff, proposal_id)
    refusal = check_send_back(view.proposal, _id(staff), note)
    if refusal is not None:
        raise _refused(refusal)
    await session.execute(
        _DECIDE,
        {
            "id": view.id,
            "status": "sent_back",
            "by": staff.id,
            "now": clock.now(),
            "note": note.strip(),
        },
    )
    await _audit(
        session, staff, "approval", "dealing.buffer_rules.sent_back", view.id, clock, reason=note
    )
    return await get_proposal(session, view.id)


async def withdraw_rules(
    session: AsyncSession, staff: Actor, proposal_id: str, clock: Clock
) -> RulesProposalView:
    """Take back a change nobody has decided. Only its proposer can."""
    view = await _for_decision(session, staff, proposal_id)
    refusal = check_withdrawal(view.proposal, _id(staff))
    if refusal is not None:
        raise _refused(refusal)
    await session.execute(
        _DECIDE,
        {"id": view.id, "status": "withdrawn", "by": staff.id, "now": clock.now(), "note": None},
    )
    await _audit(session, staff, "change", "dealing.buffer_rules.withdrawn", view.id, clock)
    return await get_proposal(session, view.id)


# Internals ---------------------------------------------------------------------------------------


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff":
        raise ApplicationError("forbidden")


def _id(actor: Actor) -> str:
    assert actor.id is not None
    return actor.id


async def _for_decision(session: AsyncSession, staff: Actor, proposal_id: str) -> RulesProposalView:
    _staff_only(staff)
    row = (await session.execute(_PROPOSAL_LOCKED, {"id": proposal_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _proposal(row)


def _values(rules: BufferRules) -> dict[str, object]:
    return {
        "currency": rules.currency.value,
        "effective_from": rules.effective_from,
        "target": rules.target.minor,
        "floor": rules.floor.minor,
        "up_to": rules.from_buffer_up_to.minor,
        "days": rules.pay_within_working_days,
    }


def _rules(row: Row[Any]) -> BufferRules:
    currency = Currency(row.currency)
    return BufferRules(
        effective_from=row.effective_from,
        target=Money(row.target_minor, currency),
        floor=Money(row.floor_minor, currency),
        from_buffer_up_to=Money(row.from_buffer_up_to_minor, currency),
        pay_within_working_days=row.pay_within_working_days,
    )


def _proposal(row: Row[Any]) -> RulesProposalView:
    return RulesProposalView(
        id=row.id,
        rules=_rules(row),
        reason=row.reason,
        status=ProposalStatus(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
    )


_FIELDS = {
    Refusal.REASON_REQUIRED: "reason",
    Refusal.EFFECTIVE_DATE_NOT_IN_FUTURE: "effective_from",
    Refusal.DATE_ALREADY_TAKEN: "effective_from",
    Refusal.NOTHING_CHANGES: "rules",
}


def _refused(refusal: Refusal) -> ApplicationError:
    if refusal in _FIELDS:
        return _invalid(_FIELDS[refusal], refusal.value)
    if refusal is Refusal.ALREADY_DECIDED:
        return ApplicationError("conflict")
    return ApplicationError("forbidden")


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


async def _audit(
    session: AsyncSession,
    actor: Actor,
    category: AuditCategory,
    action: str,
    proposal_id: str,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=action,
            resource_type=PROPOSAL_RESOURCE,
            resource_id=proposal_id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
        ),
        clock,
    )
