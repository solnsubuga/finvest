"""The email suppression list as staff see it, and lifting a suppression with two people.

An address is suppressed after a lasting bounce or a spam complaint (`email_delivery`). Staff
can send to it again only when one member of staff proposes a lift with a reason and a
different one approves it. Nothing in the suppression list is updated or deleted: a lift
outweighs the suppressing events before it, and a bounce or complaint after the lift
suppresses the address again. Migration 0153 holds the rules PostgreSQL enforces.

An address that ever sent a spam complaint is never lifted.

Every function works inside the caller's staff transaction. Addresses are only ever held as
hashes (`address_hash`); an address typed into the console is hashed and never stored.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.email import InvalidEmail, check_address
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.notifications.domain.email_events import address_hash, keyed_hash
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

RESOURCE = "email_suppression_lift"
MAX_REASON = 500
LIST_LIMIT = 200
Decision = Literal["approved", "rejected", "withdrawn"]

# The latest event that suppresses an address: a spam complaint, or a bounce that will not
# clear up by itself. Its time is compared with the latest approved lift.
LATEST_SUPPRESSING = (
    "(SELECT max(e.received_at) FROM notification_email_events e "
    "WHERE e.recipients @> ARRAY[{hash}]::text[] "
    "AND (e.delivery = 'complained' OR (e.delivery = 'bounced' AND e.permanent)))"
)
LATEST_LIFT = (
    "(SELECT max(l.decided_at) FROM notification_email_suppression_lifts l "
    "WHERE l.address_hash = {hash} AND l.status = 'approved')"
)
# A spam complaint is the person refusing our email, so an address that ever complained is
# never lifted. PostgreSQL refuses the proposal too.
EVER_COMPLAINED = (
    "(s.reason = 'complained' OR EXISTS (SELECT FROM notification_email_events e "
    "WHERE e.recipients @> ARRAY[s.address_hash]::text[] AND e.delivery = 'complained'))"
)
# True while the address is on the list and no lift has come after its latest suppressing
# event. Used by the worker before every email, and by the console.
SUPPRESSED_NOW = "({lift} IS NULL OR {lift} < coalesce({event}, s.created_at))"


def suppressed_now(hash_sql: str) -> str:
    """The SQL condition for an address still being suppressed, for a row `s` of the list."""
    return SUPPRESSED_NOW.format(
        lift=LATEST_LIFT.format(hash=hash_sql), event=LATEST_SUPPRESSING.format(hash=hash_sql)
    )


@dataclass(frozen=True, slots=True)
class Lift:
    id: str
    address_hash: str
    reason: str
    proposed_by: str
    proposed_at: datetime
    status: str
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None


@dataclass(frozen=True, slots=True)
class Suppression:
    address_hash: str
    reason: str
    created_at: datetime
    # The latest bounce or complaint for the address, which may be after a lift.
    last_event_at: datetime | None
    # False once a lift was approved after the latest suppressing event.
    suppressed: bool
    lifted_at: datetime | None
    open_lift: Lift | None
    # True when the address ever sent a spam complaint. Such an address is never lifted.
    complained: bool


_LIFT_COLUMNS = (
    "id, address_hash, reason, proposed_by, proposed_at, status, decided_by, decided_at, "
    "decision_reason"
)
_SUPPRESSIONS = (
    "SELECT s.address_hash, s.reason, s.created_at, "  # noqa: S608 - fixed SQL only
    f"{LATEST_SUPPRESSING.format(hash='s.address_hash')} AS last_event_at, "
    f"{suppressed_now('s.address_hash')} AS suppressed, "
    f"{LATEST_LIFT.format(hash='s.address_hash')} AS lifted_at, "
    f"{EVER_COMPLAINED} AS complained "
    "FROM notification_email_suppressions s "
)
_LIST = text(_SUPPRESSIONS + "ORDER BY s.created_at DESC, s.address_hash LIMIT :limit")
_ONE = text(_SUPPRESSIONS + "WHERE s.address_hash = :hash")
_OPEN = text(
    f"SELECT {_LIFT_COLUMNS} FROM notification_email_suppression_lifts "  # noqa: S608
    "WHERE status = 'proposed'"
)
_LIFT = text(
    f"SELECT {_LIFT_COLUMNS} FROM notification_email_suppression_lifts "  # noqa: S608
    "WHERE id = :id"
)
_LOCK_LIFT = text(
    "SELECT address_hash FROM notification_email_suppression_lifts "
    "WHERE id = :id AND status = 'proposed' FOR UPDATE"
)


async def list_suppressions(session: AsyncSession) -> list[Suppression]:
    """The newest suppressions first, with whether each still applies and any open lift."""
    rows = (await session.execute(_LIST, {"limit": LIST_LIMIT})).all()
    open_lifts = {row.address_hash: _lift(row) for row in (await session.execute(_OPEN)).all()}
    return [_suppression(row, open_lifts.get(row.address_hash)) for row in rows]


async def open_lifts(session: AsyncSession) -> list[Lift]:
    """Every lift waiting for a second person, oldest first."""
    rows = (await session.execute(_OPEN)).all()
    return sorted((_lift(row) for row in rows), key=lambda lift: (lift.proposed_at, lift.id))


async def suppression_for_address(session: AsyncSession, address: str) -> Suppression | None:
    """The suppression for an address typed into the console, or None if it was never on
    the list. The address itself is only hashed."""
    try:
        check_address(address.strip().lower())
    except InvalidEmail:
        raise _invalid("email", "invalid") from None
    return await suppression(session, address_hash(address))


async def suppression(session: AsyncSession, hashed: str) -> Suppression | None:
    row = (await session.execute(_ONE, {"hash": hashed})).one_or_none()
    if row is None:
        return None
    open_lifts = {r.address_hash: _lift(r) for r in (await session.execute(_OPEN)).all()}
    return _suppression(row, open_lifts.get(hashed))


async def propose(
    session: AsyncSession,
    staff: Actor,
    hashed: str,
    reason: str,
    clock: Clock,
    key: bytes | None,
) -> Lift:
    """Ask to send email to the address again. A different member of staff decides. `key` is
    the email hash key (`EmailHashSettings`); without one only the plain hash is kept."""
    acting = _staff_id(staff)
    reason = _reason(reason)
    current = await suppression(session, hashed)
    if current is None:
        raise ApplicationError("not_found")
    if not current.suppressed:
        raise _refused("not_suppressed")
    if current.complained:
        raise _refused("complained")
    lift_id = push_id(clock)
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO notification_email_suppression_lifts (id, address_hash, "
                    "address_key, reason, proposed_by, proposed_at) VALUES (:id, :hash, :key, "
                    ":reason, :staff, :now)"
                ),
                {
                    "id": lift_id,
                    "hash": hashed,
                    "key": keyed_hash(hashed, key) if key else None,
                    "reason": reason,
                    "staff": acting,
                    "now": clock.now(),
                },
            )
    except IntegrityError:
        # A lift for this address is already waiting.
        raise ApplicationError("conflict") from None
    await _audit(session, acting, "notifications.suppression_lift_proposed", lift_id, reason, clock)
    return await _by_id(session, lift_id)


async def decide(
    session: AsyncSession,
    staff: Actor,
    lift_id: str,
    decision: Decision,
    reason: str,
    clock: Clock,
) -> Lift:
    """Approve or reject someone else's lift, or withdraw your own."""
    acting = _staff_id(staff)
    reason = _reason(reason)
    hashed = await session.scalar(_LOCK_LIFT, {"id": lift_id})
    if hashed is None:
        raise ApplicationError("not_found")
    lift = await _by_id(session, lift_id)
    if (lift.proposed_by == acting) != (decision == "withdrawn"):
        # PostgreSQL refuses this too. Answering here gives the console a clean refusal.
        raise ApplicationError("forbidden")
    await session.execute(
        text(
            "UPDATE notification_email_suppression_lifts SET status = :status, "
            "decided_by = :staff, decided_at = :now, decision_reason = :reason WHERE id = :id"
        ),
        {"status": decision, "staff": acting, "now": clock.now(), "reason": reason, "id": lift_id},
    )
    await _audit(
        session, acting, f"notifications.suppression_lift_{decision}", lift_id, reason, clock
    )
    return await _by_id(session, lift_id)


async def _by_id(session: AsyncSession, lift_id: str) -> Lift:
    row = (await session.execute(_LIFT, {"id": lift_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _lift(row)


async def _audit(
    session: AsyncSession, staff_id: str, action: str, lift_id: str, reason: str, clock: Clock
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category="approval" if action.endswith(("approved", "rejected")) else "change",
            action=action,
            resource_type=RESOURCE,
            resource_id=lift_id,
            actor=AuditActor.staff(staff_id),
            reason=reason,
        ),
        clock,
    )


def _suppression(row: Row[Any], open_lift: Lift | None) -> Suppression:
    return Suppression(
        address_hash=row.address_hash,
        reason=row.reason,
        created_at=row.created_at,
        last_event_at=row.last_event_at,
        suppressed=row.suppressed,
        lifted_at=row.lifted_at,
        open_lift=open_lift,
        complained=row.complained,
    )


def _lift(row: Row[Any]) -> Lift:
    return Lift(
        id=row.id,
        address_hash=row.address_hash,
        reason=row.reason,
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        status=row.status,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
    )


def _staff_id(staff: Actor) -> str:
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    return staff.id


def _reason(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > MAX_REASON:
        raise _invalid("reason", "required")
    return cleaned


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=["body", field], code=code)]
    )


def _refused(code: str) -> ApplicationError:
    return ApplicationError(
        "conflict", fields=[FieldProblem(field_path=["body", "address_hash"], code=code)]
    )
