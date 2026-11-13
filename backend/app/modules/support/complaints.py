"""The complaints register. Every function works inside the caller's transaction.

A complaint, its history row, its audit event and the notification to the client commit
together or not at all. Migration 0100 holds what PostgreSQL enforces: what a complaint said
when it was logged never changes, closing records an outcome, and a closed complaint is final.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.notifications import contract as notifications
from app.modules.support.domain.complaints import (
    OPEN_STATUSES,
    WORKING_DAYS_TO_REPLY,
    Category,
    Closure,
    InvalidComplaint,
    NewComplaint,
    Outcome,
    ReceivedVia,
    Status,
    can_move,
    is_overdue,
    reply_due,
    working_days_between,
)
from app.modules.support.domain.complaints import note as clean_note
from app.shared.actors import Actor
from app.shared.business_day import KAMPALA, business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id, validate_push_id

__all__ = ["WORKING_DAYS_TO_REPLY"]

RESOURCE = "complaint"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
Filter = Literal["open", "closed", "all"]
EventAction = Literal["logged", "assigned", "status", "note", "escalated", "closed"]


@dataclass(frozen=True, slots=True)
class ComplaintView:
    id: str
    reference: str
    client_id: str
    conversation_id: str | None
    category: Category
    received_via: ReceivedVia
    summary: str
    details: str
    amount: Money | None
    status: Status
    owner_id: str | None
    received_at: datetime
    due_on: date
    overdue: bool
    closed_at: datetime | None
    outcome: Outcome | None
    resolution: str | None
    client_accepted: bool | None


@dataclass(frozen=True, slots=True)
class ComplaintEvent:
    """One line of a complaint's history. Staff notes are for staff, never shown to a client."""

    id: str
    action: EventAction
    actor_kind: Literal["client", "staff"]
    actor_id: str
    from_status: Status | None
    to_status: Status | None
    note: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class Summary:
    open: int
    overdue: int
    # Since the first day of the calendar quarter.
    received_this_quarter: int
    closed_this_quarter: int
    upheld_this_quarter: int
    # Average working days from received to closed, in tenths of a day. None with nothing closed.
    average_days_to_close_tenths: int | None


_COLUMNS = (
    "id, reference, client_id, conversation_id, category, received_via, summary, details, "
    "amount_minor, amount_currency, status, owner_id, received_at, due_on, closed_at, outcome, "
    "resolution, client_accepted"
)
_INSERT = text(
    "INSERT INTO complaints (id, client_id, conversation_id, category, received_via, summary, "
    "details, amount_minor, amount_currency, owner_id, received_at, due_on, logged_by_kind, "
    "logged_by, updated_at) VALUES (:id, :client_id, :conversation_id, :category, :received_via, "
    ":summary, :details, :amount_minor, :amount_currency, :owner_id, :now, :due_on, :kind, "
    ":actor, :now)"
)
_BY_ID = text(f"SELECT {_COLUMNS} FROM complaints WHERE id = :id")  # noqa: S608
_BY_ID_LOCKED = text(f"SELECT {_COLUMNS} FROM complaints WHERE id = :id FOR UPDATE")  # noqa: S608
_FOR_CLIENT = text(
    f"SELECT {_COLUMNS} FROM complaints WHERE client_id = :client_id "  # noqa: S608
    "ORDER BY received_at DESC, id DESC LIMIT :limit"
)
# Open complaints by deadline, the most urgent first. Closed ones, the latest first.
_OPEN = text(
    f"SELECT {_COLUMNS} FROM complaints WHERE status <> 'closed' "  # noqa: S608
    "ORDER BY due_on, received_at, id LIMIT :limit"
)
_CLOSED = text(
    f"SELECT {_COLUMNS} FROM complaints WHERE status = 'closed' "  # noqa: S608
    "ORDER BY closed_at DESC, id DESC LIMIT :limit"
)
_ALL = text(
    f"SELECT {_COLUMNS} FROM complaints "  # noqa: S608 - constant column list
    "ORDER BY (status = 'closed'), due_on, received_at DESC, id LIMIT :limit"
)
_EVENTS = text(
    "SELECT id, action, actor_kind, actor_id, from_status, to_status, note, occurred_at "
    "FROM complaint_events WHERE complaint_id = :id ORDER BY occurred_at, id"
)
_ADD_EVENT = text(
    "INSERT INTO complaint_events (id, complaint_id, action, actor_kind, actor_id, from_status, "
    "to_status, note, occurred_at) VALUES (:id, :complaint_id, :action, :kind, :actor, :from, "
    ":to, :note, :now)"
)
_ASSIGN = text("UPDATE complaints SET owner_id = :owner_id, updated_at = :now WHERE id = :id")
_MOVE = text(
    "UPDATE complaints SET status = :status, updated_at = :now, "
    "escalated_at = CASE WHEN :escalated THEN :now ELSE escalated_at END "
    "WHERE id = :id"
)
_CLOSE = text(
    "UPDATE complaints SET status = 'closed', closed_at = :now, closed_by = :staff, "
    "outcome = :outcome, resolution = :resolution, client_accepted = :accepted, "
    "updated_at = :now WHERE id = :id"
)
_COUNTS = text(
    "SELECT count(*) FILTER (WHERE status <> 'closed') AS open, "
    "count(*) FILTER (WHERE status <> 'closed' AND due_on < :today) AS overdue, "
    "count(*) FILTER (WHERE received_at >= :quarter) AS received, "
    "count(*) FILTER (WHERE closed_at >= :quarter) AS closed, "
    "count(*) FILTER (WHERE closed_at >= :quarter AND outcome IN ('upheld', 'partly_upheld')) "
    "AS upheld FROM complaints"
)
_CLOSED_TIMES = text("SELECT received_at, closed_at FROM complaints WHERE closed_at >= :quarter")


async def log_by_client(
    session: AsyncSession, client: Actor, new: NewComplaint, clock: Clock
) -> ComplaintView:
    """A client makes a complaint from the app. They are told its reference and reply date."""
    if client.kind != "client" or client.id is None or new.received_via != "app":
        raise ApplicationError("forbidden")
    return await _log(session, client, client.id, new, None, None, clock)


async def log_by_staff(
    session: AsyncSession,
    staff: Actor,
    client_id: str,
    new: NewComplaint,
    clock: Clock,
    *,
    conversation_id: str | None = None,
    owner_id: str | None = None,
) -> ComplaintView:
    """Staff log a complaint a client made by phone, email, letter, in person or in chat."""
    _staff(staff)
    if new.received_via == "app":
        raise _invalid("received_via", "unknown")
    try:
        validate_push_id(client_id)
    except ValueError:
        raise _invalid("client_id", "invalid") from None
    return await _log(session, staff, client_id, new, conversation_id, owner_id, clock)


async def complaints_for_client(
    session: AsyncSession, client_id: str, today: date
) -> list[ComplaintView]:
    rows = await session.execute(_FOR_CLIENT, {"client_id": client_id, "limit": DEFAULT_PAGE_SIZE})
    return [_view(row, today) for row in rows.all()]


async def complaint_for_client(
    session: AsyncSession, client_id: str, complaint_id: str, today: date
) -> ComplaintView:
    row = (await session.execute(_BY_ID, {"id": complaint_id})).one_or_none()
    # The row policy hides other clients' complaints. This check is the second fence.
    if row is None or row.client_id != client_id:
        raise ApplicationError("not_found")
    return _view(row, today)


async def register(
    session: AsyncSession, today: date, *, show: Filter = "open", limit: int | None = None
) -> list[ComplaintView]:
    """The register as staff work through it."""
    query = {"open": _OPEN, "closed": _CLOSED, "all": _ALL}[show]
    size = max(1, min(limit or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE))
    return [_view(row, today) for row in (await session.execute(query, {"limit": size})).all()]


async def complaint_for_staff(
    session: AsyncSession, complaint_id: str, today: date
) -> tuple[ComplaintView, list[ComplaintEvent]]:
    row = (await session.execute(_BY_ID, {"id": complaint_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    events = (await session.execute(_EVENTS, {"id": complaint_id})).all()
    return _view(row, today), [
        ComplaintEvent(
            id=event.id,
            action=event.action,
            actor_kind=event.actor_kind,
            actor_id=event.actor_id,
            from_status=event.from_status,
            to_status=event.to_status,
            note=event.note,
            occurred_at=event.occurred_at,
        )
        for event in events
    ]


async def assign(
    session: AsyncSession, staff: Actor, complaint_id: str, owner_id: str, clock: Clock
) -> ComplaintView:
    """Give the complaint an owner: the member of staff who answers for it."""
    _staff(staff)
    try:
        validate_push_id(owner_id)
    except ValueError:
        raise _invalid("owner_id", "invalid") from None
    row = await _open(session, complaint_id)
    now = clock.now()
    await session.execute(_ASSIGN, {"id": row.id, "owner_id": owner_id, "now": now})
    await _event(session, staff, row.id, "assigned", now)
    await _audit(session, staff, "complaints.assigned", row, clock)
    return await _reload(session, row.id, clock)


async def move(
    session: AsyncSession,
    staff: Actor,
    complaint_id: str,
    status: Status,
    note: str,
    clock: Clock,
) -> ComplaintView:
    """Move an open complaint on, saying why. Escalating is a move to `escalated`."""
    _staff(staff)
    reason = _note(note)
    row = await _open(session, complaint_id)
    if status == "closed" or not can_move(row.status, status):
        raise ApplicationError("conflict")
    now = clock.now()
    await session.execute(
        _MOVE,
        {"id": row.id, "status": status, "escalated": status == "escalated", "now": now},
    )
    action: EventAction = "escalated" if status == "escalated" else "status"
    await _event(session, staff, row.id, action, now, row.status, status, reason)
    await _audit(session, staff, f"complaints.{action}", row, clock, reason, status)
    return await _reload(session, row.id, clock)


async def add_note(
    session: AsyncSession, staff: Actor, complaint_id: str, note: str, clock: Clock
) -> ComplaintView:
    """Record what was found or done, without changing the status."""
    _staff(staff)
    row = await _open(session, complaint_id)
    await _event(session, staff, row.id, "note", clock.now(), note=_note(note))
    return await _reload(session, row.id, clock)


async def close(
    session: AsyncSession, staff: Actor, complaint_id: str, closure: Closure, clock: Clock
) -> ComplaintView:
    """Record the outcome and close the complaint for good. The client is told."""
    staff_id = _staff(staff)
    row = await _open(session, complaint_id)
    now = clock.now()
    await session.execute(
        _CLOSE,
        {
            "id": row.id,
            "staff": staff_id,
            "outcome": closure.outcome,
            "resolution": closure.resolution,
            "accepted": closure.client_accepted,
            "now": now,
        },
    )
    await _event(session, staff, row.id, "closed", now, row.status, "closed")
    await _audit(session, staff, "complaints.closed", row, clock, closure.outcome, "closed")
    await notifications.notify(
        session,
        event_name="complaint_closed",
        client_id=row.client_id,
        idempotency_key=row.id,
        variables={"reference": row.reference},
        link=notifications.Link("complaint", row.id),
        now=now,
    )
    return await _reload(session, row.id, clock)


async def summary(session: AsyncSession, clock: Clock) -> Summary:
    """The figures at the top of the register."""
    today = business_date(clock.now())
    # Midnight in Kampala on the first day of the calendar quarter.
    start = datetime(today.year, 3 * ((today.month - 1) // 3) + 1, 1, tzinfo=KAMPALA)
    counts = (await session.execute(_COUNTS, {"today": today, "quarter": start})).one()
    times = (await session.execute(_CLOSED_TIMES, {"quarter": start})).all()
    days = [
        working_days_between(business_date(row.received_at), business_date(row.closed_at))
        for row in times
    ]
    return Summary(
        open=counts.open,
        overdue=counts.overdue,
        received_this_quarter=counts.received,
        closed_this_quarter=counts.closed,
        upheld_this_quarter=counts.upheld,
        # Integer arithmetic, rounded half up to a tenth of a day.
        average_days_to_close_tenths=(
            (sum(days) * 20 + len(days)) // (2 * len(days)) if days else None
        ),
    )


async def _log(
    session: AsyncSession,
    actor: Actor,
    client_id: str,
    new: NewComplaint,
    conversation_id: str | None,
    owner_id: str | None,
    clock: Clock,
) -> ComplaintView:
    now = clock.now()
    complaint_id = push_id()
    due_on = reply_due(business_date(now))
    await session.execute(
        _INSERT,
        {
            "id": complaint_id,
            "client_id": client_id,
            "conversation_id": conversation_id,
            "category": new.category,
            "received_via": new.received_via,
            "summary": new.summary,
            "details": new.details,
            "amount_minor": new.amount.minor if new.amount else None,
            "amount_currency": new.amount.currency.value if new.amount else None,
            "owner_id": owner_id,
            "now": now,
            "due_on": due_on,
            "kind": actor.kind,
            "actor": actor.id,
        },
    )
    row = (await session.execute(_BY_ID, {"id": complaint_id})).one()
    await _event(session, actor, complaint_id, "logged", now, None, "new")
    await _audit(session, actor, "complaints.logged", row, clock, status="new")
    await notifications.notify(
        session,
        event_name="complaint_received",
        client_id=client_id,
        idempotency_key=complaint_id,
        variables={"reference": row.reference, "date": due_on},
        link=notifications.Link("complaint", complaint_id),
        now=now,
    )
    return _view(row, business_date(now))


async def _open(session: AsyncSession, complaint_id: str) -> Row[Any]:
    row = (await session.execute(_BY_ID_LOCKED, {"id": complaint_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    if row.status not in OPEN_STATUSES:
        raise ApplicationError("conflict")
    return row


async def _reload(session: AsyncSession, complaint_id: str, clock: Clock) -> ComplaintView:
    row = (await session.execute(_BY_ID, {"id": complaint_id})).one()
    return _view(row, business_date(clock.now()))


async def _event(
    session: AsyncSession,
    actor: Actor,
    complaint_id: str,
    action: EventAction,
    now: datetime,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
) -> None:
    await session.execute(
        _ADD_EVENT,
        {
            "id": push_id(),
            "complaint_id": complaint_id,
            "action": action,
            "kind": actor.kind,
            "actor": actor.id,
            "from": from_status,
            "to": to_status,
            "note": note,
            "now": now,
        },
    )


async def _audit(
    session: AsyncSession,
    actor: Actor,
    action: str,
    row: Row[Any],
    clock: Clock,
    reason: str | None = None,
    status: str | None = None,
) -> None:
    if actor.id is None:
        raise ApplicationError("forbidden")
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action=action,
            resource_type=RESOURCE,
            resource_id=row.id,
            actor=(
                AuditActor.staff(actor.id) if actor.kind == "staff" else AuditActor.client(actor.id)
            ),
            reason=reason,
            # The reference and the status. What the complaint says stays in the register.
            details={"reference": row.reference, **({"status": status} if status else {})},
        ),
        clock,
    )


def _staff(staff: Actor) -> str:
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    return staff.id


def _note(value: str) -> str:
    try:
        return clean_note(value)
    except InvalidComplaint as error:
        raise _invalid(error.field, error.code) from None


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


def _view(row: Row[Any], today: date) -> ComplaintView:
    return ComplaintView(
        id=row.id,
        reference=row.reference,
        client_id=row.client_id,
        conversation_id=row.conversation_id,
        category=row.category,
        received_via=row.received_via,
        summary=row.summary,
        details=row.details,
        amount=(
            Money(row.amount_minor, Currency(row.amount_currency))
            if row.amount_minor is not None
            else None
        ),
        status=row.status,
        owner_id=row.owner_id,
        received_at=row.received_at,
        due_on=row.due_on,
        overdue=is_overdue(row.status, row.due_on, today),
        closed_at=row.closed_at,
        outcome=row.outcome,
        resolution=row.resolution,
        client_accepted=row.client_accepted,
    )
