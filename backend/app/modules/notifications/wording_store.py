"""The words in use for each event, and the two-person change to them.

Every function works inside the caller's transaction. A proposal, its decision and their audit
events commit together or not at all. Migration 0090 holds the rules PostgreSQL enforces: the
person who decides is never the person who proposed, and a decided wording never changes.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.notifications.domain.events import EVENTS
from app.modules.notifications.domain.wording import (
    DEFAULTS,
    EDITABLE,
    InvalidWording,
    Wording,
    check,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

RESOURCE = "notification_wording"
MAX_REASON = 500
Decision = Literal["approved", "rejected", "withdrawn"]


@dataclass(frozen=True, slots=True)
class Proposal:
    id: str
    event: str
    wording: Wording
    reason: str
    proposed_by: str
    proposed_at: datetime


@dataclass(frozen=True, slots=True)
class EventWording:
    """One message as the console's Settings screen lists it."""

    event: str
    wording: Wording
    # False while the built-in words are still in use.
    edited: bool
    changed_at: datetime | None
    proposal: Proposal | None


_ACTIVE = text(
    "SELECT title, body, sms FROM notification_wordings WHERE event = :event "
    "AND status = 'approved' ORDER BY decided_at DESC, id DESC LIMIT 1"
)
_ALL_ACTIVE = text(
    "SELECT DISTINCT ON (event) event, title, body, sms, decided_at FROM notification_wordings "
    "WHERE status = 'approved' ORDER BY event, decided_at DESC, id DESC"
)
_COLUMNS = "id, event, title, body, sms, reason, proposed_by, proposed_at"
_OPEN = text(f"SELECT {_COLUMNS} FROM notification_wordings WHERE status = 'proposed'")  # noqa: S608
_OPEN_BY_ID = text(
    f"SELECT {_COLUMNS} FROM notification_wordings "  # noqa: S608 - constant column list
    "WHERE id = :id AND status = 'proposed' FOR UPDATE"
)
_INSERT = text(
    "INSERT INTO notification_wordings (id, event, title, body, sms, reason, proposed_by, "
    "proposed_at) VALUES (:id, :event, :title, :body, :sms, :reason, :staff, :now)"
)
_DECIDE = text(
    "UPDATE notification_wordings SET status = :status, decided_by = :staff, decided_at = :now, "
    "decision_reason = :reason WHERE id = :id"
)


async def active_wording(session: AsyncSession, event_name: str) -> Wording:
    """The words to write `event_name` with now: the last approved change, or the built-in."""
    row = (await session.execute(_ACTIVE, {"event": event_name})).one_or_none()
    if row is None or event_name not in EDITABLE:
        return DEFAULTS[event_name]
    return Wording(row.title, row.body, row.sms)


async def list_wordings(session: AsyncSession) -> list[EventWording]:
    """Every editable message in catalogue order, with any change waiting for a decision."""
    active = {row.event: row for row in (await session.execute(_ALL_ACTIVE)).all()}
    proposals = {row.event: _proposal(row) for row in (await session.execute(_OPEN)).all()}
    listed = []
    for name in EVENTS:
        if name not in EDITABLE:
            continue
        row = active.get(name)
        listed.append(
            EventWording(
                event=name,
                wording=Wording(row.title, row.body, row.sms) if row else DEFAULTS[name],
                edited=row is not None,
                changed_at=row.decided_at if row else None,
                proposal=proposals.get(name),
            )
        )
    return listed


async def propose(
    session: AsyncSession,
    staff: Actor,
    event_name: str,
    wording: Wording,
    reason: str,
    clock: Clock,
) -> Proposal:
    """Put a change of words forward. Nothing a client receives changes until it is approved."""
    reason = _reason(reason)
    if event_name not in EVENTS:
        raise ApplicationError("not_found")
    try:
        check(event_name, wording)
    except InvalidWording as error:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=[field], code=code) for field, code in error.problems],
        ) from None
    proposal = Proposal(push_id(), event_name, wording, reason, _staff_id(staff), clock.now())
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": proposal.id,
                    "event": event_name,
                    "title": wording.title,
                    "body": wording.body,
                    "sms": wording.sms,
                    "reason": reason,
                    "staff": proposal.proposed_by,
                    "now": proposal.proposed_at,
                },
            )
    except IntegrityError:
        # A change to this message is already waiting. It is decided or withdrawn first.
        raise ApplicationError("conflict") from None
    await _audit(session, staff, "notifications.wording_proposed", proposal, reason, clock)
    return proposal


async def decide(
    session: AsyncSession,
    staff: Actor,
    proposal_id: str,
    decision: Decision,
    reason: str,
    clock: Clock,
) -> Proposal:
    """Approve or reject someone else's proposal, or withdraw your own."""
    reason = _reason(reason)
    row = (await session.execute(_OPEN_BY_ID, {"id": proposal_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    proposal = _proposal(row)
    own = proposal.proposed_by == _staff_id(staff)
    if own != (decision == "withdrawn"):
        # PostgreSQL refuses this too. Answering here gives the console a clean refusal.
        raise ApplicationError("forbidden")
    await session.execute(
        _DECIDE,
        {"status": decision, "staff": staff.id, "now": clock.now(), "reason": reason, "id": row.id},
    )
    await _audit(session, staff, f"notifications.wording_{decision}", proposal, reason, clock)
    return proposal


async def _audit(
    session: AsyncSession, staff: Actor, action: str, proposal: Proposal, reason: str, clock: Clock
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category="approval" if action.endswith(("approved", "rejected")) else "change",
            action=action,
            resource_type=RESOURCE,
            resource_id=proposal.id,
            actor=AuditActor.staff(_staff_id(staff)),
            reason=reason,
            details={"event": proposal.event},
        ),
        clock,
    )


def _staff_id(staff: Actor) -> str:
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    return staff.id


def _reason(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > MAX_REASON:
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["reason"], code="required")]
        )
    return cleaned


def _proposal(row: Row[Any]) -> Proposal:
    return Proposal(
        id=row.id,
        event=row.event,
        wording=Wording(row.title, row.body, row.sms),
        reason=row.reason,
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
    )
