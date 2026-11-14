"""A client's sign-in account as staff see it, and locking or unlocking it with two people.

Every function works inside the caller's staff transaction. A proposal, its decision, the
change of status and their audit events commit together or not at all. Migration 0152 holds
the rules PostgreSQL enforces: the approver is never the proposer, and a client's status moves
between active and locked only through a lock or unlock approved in the same transaction.

A locked client cannot sign in or use a session, and is sent no messages
(`client_contact` answers nothing for them). Their money stays where it is.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import structlog
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.identity.domain.accounts import InvalidIdentifier, normalize_phone
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

RESOURCE = "client_lock"
MAX_REASON = 500
LockKind = Literal["lock", "unlock"]
Decision = Literal["approved", "rejected", "withdrawn"]
ClientStatus = Literal["active", "locked", "closed"]
logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class ClientAccount:
    id: str
    phone: str
    email: str | None
    status: ClientStatus
    created_at: datetime
    # Withdrawals only to known accounts until then, after a code sign-in or a reset.
    restricted_until: datetime | None
    has_passkey: bool
    has_authenticator: bool
    last_signed_in_at: datetime | None


@dataclass(frozen=True, slots=True)
class LockChange:
    id: str
    kind: LockKind
    client_id: str
    reason: str
    proposed_by: str
    proposed_by_name: str
    proposed_at: datetime
    status: str
    decided_by: str | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_reason: str | None


_ACCOUNT = (
    "SELECT c.id, c.phone, c.email, c.status, c.created_at, c.restricted_until, "
    "EXISTS (SELECT FROM identity_passkeys p WHERE p.actor_kind = 'client' "
    "AND p.actor_id = c.id AND p.removed_at IS NULL) AS has_passkey, "
    "EXISTS (SELECT FROM identity_authenticators a WHERE a.actor_kind = 'client' "
    "AND a.actor_id = c.id AND a.confirmed_at IS NOT NULL AND a.removed_at IS NULL) "
    "AS has_authenticator, "
    "(SELECT max(created_at) FROM identity_sessions i WHERE i.actor_kind = 'client' "
    "AND i.actor_id = c.id) AS last_signed_in_at "
    "FROM identity_clients c "
)
_BY_ID = text(_ACCOUNT + "WHERE c.id = :id")
_BY_PHONE = text(_ACCOUNT + "WHERE c.phone = :phone")
_BY_IDS = text(_ACCOUNT + "WHERE c.id = ANY(CAST(:ids AS text[]))")
_CHANGES = (
    "SELECT l.id, l.kind, l.client_id, l.reason, l.proposed_by, p.name AS proposed_by_name, "
    "l.proposed_at, l.status, l.decided_by, d.name AS decided_by_name, l.decided_at, "
    "l.decision_reason FROM identity_client_locks l "
    "JOIN identity_staff p ON p.id = l.proposed_by "
    "LEFT JOIN identity_staff d ON d.id = l.decided_by "
)
_HISTORY = text(_CHANGES + "WHERE l.client_id = :client_id ORDER BY l.proposed_at DESC, l.id DESC")
_OPEN = text(_CHANGES + "WHERE l.status = 'proposed' ORDER BY l.proposed_at, l.id")
_CHANGE = text(_CHANGES + "WHERE l.id = :id")
_LOCK = text(
    "SELECT id FROM identity_client_locks WHERE id = :id AND status = 'proposed' FOR UPDATE"
)
_STATUS = text("SELECT status FROM identity_clients WHERE id = :id FOR UPDATE")
# The same read without the row lock, for a transaction that only needs to know.
_STATUS_UNLOCKED = text("SELECT status FROM identity_clients WHERE id = :id")
_ID_FOR_PHONE = text("SELECT id FROM identity_clients WHERE phone = :phone")


async def client_status(session: AsyncSession, client_id: str) -> str | None:
    """`active`, `locked`, `unclaimed` or `closed`, or None if there is no such client.

    Reads the account alone, so any transaction may ask, such as a client sending an
    investment to another.
    """
    status = await session.scalar(_STATUS_UNLOCKED, {"id": client_id})
    return str(status) if status is not None else None


async def client_id_for_phone(session: AsyncSession, phone: str) -> str | None:
    """The client with this mobile number, written any way a person might type it, or None."""
    try:
        normalized = normalize_phone(phone)
    except InvalidIdentifier:
        return None
    found = await session.scalar(_ID_FOR_PHONE, {"phone": normalized})
    return str(found) if found is not None else None


async def client_account(session: AsyncSession, client_id: str) -> ClientAccount | None:
    row = (await session.execute(_BY_ID, {"id": client_id})).one_or_none()
    return _account(row) if row else None


async def client_accounts(session: AsyncSession, client_ids: list[str]) -> dict[str, ClientAccount]:
    """The accounts of these clients, in one query, by ID. An ID with no client is left out."""
    if not client_ids:
        return {}
    rows = await session.execute(_BY_IDS, {"ids": list(dict.fromkeys(client_ids))})
    return {row.id: _account(row) for row in rows}


async def client_by_phone(session: AsyncSession, phone: str) -> ClientAccount | None:
    """The client with this mobile number, written any way a person might type it."""
    try:
        normalized = normalize_phone(phone)
    except InvalidIdentifier:
        return None
    row = (await session.execute(_BY_PHONE, {"phone": normalized})).one_or_none()
    return _account(row) if row else None


async def lock_history(session: AsyncSession, client_id: str) -> list[LockChange]:
    """Every lock and unlock proposed for the client, newest first."""
    return [_change(row) for row in (await session.execute(_HISTORY, {"client_id": client_id}))]


async def open_locks(session: AsyncSession) -> list[LockChange]:
    """Every lock or unlock waiting for a second person, oldest first."""
    return [_change(row) for row in (await session.execute(_OPEN)).all()]


async def propose(
    session: AsyncSession, staff: Actor, client_id: str, kind: LockKind, reason: str, clock: Clock
) -> LockChange:
    """Ask for the client to be locked or unlocked. A different member of staff decides."""
    acting = _staff_id(staff)
    reason = _reason(reason)
    status = await session.scalar(_STATUS, {"id": client_id})
    if status is None:
        raise ApplicationError("not_found")
    _check(kind, status)
    change_id = push_id(clock)
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO identity_client_locks (id, kind, client_id, reason, proposed_by, "
                    "proposed_at) VALUES (:id, :kind, :client_id, :reason, :staff, :now)"
                ),
                {
                    "id": change_id,
                    "kind": kind,
                    "client_id": client_id,
                    "reason": reason,
                    "staff": acting,
                    "now": clock.now(),
                },
            )
    except IntegrityError:
        # A lock or unlock for this client is already waiting.
        raise ApplicationError("conflict") from None
    await _audit(
        session, acting, f"identity.client.{kind}_proposed", change_id, client_id, reason, clock
    )
    return await _by_id(session, change_id)


async def decide(
    session: AsyncSession,
    staff: Actor,
    change_id: str,
    decision: Decision,
    reason: str,
    clock: Clock,
) -> LockChange:
    """Approve or reject someone else's proposal, or withdraw your own.

    Approving a lock shuts the client out at once: every session they hold ends.
    """
    acting = _staff_id(staff)
    reason = _reason(reason)
    if (await session.execute(_LOCK, {"id": change_id})).one_or_none() is None:
        raise ApplicationError("not_found")
    change = await _by_id(session, change_id)
    if (change.proposed_by == acting) != (decision == "withdrawn"):
        # PostgreSQL refuses this too. Answering here gives the console a clean refusal.
        raise ApplicationError("forbidden")
    now = clock.now()
    if decision == "approved":
        status = await session.scalar(_STATUS, {"id": change.client_id})
        _check(change.kind, str(status))
    await session.execute(
        text(
            "UPDATE identity_client_locks SET status = :status, decided_by = :staff, "
            "decided_at = :now, decision_reason = :reason, decided_in = CASE WHEN "
            ":status = 'approved' THEN pg_current_xact_id() END WHERE id = :id"
        ),
        {"status": decision, "staff": acting, "now": now, "reason": reason, "id": change_id},
    )
    await _audit(
        session,
        acting,
        f"identity.client.{change.kind}_{decision}",
        change_id,
        change.client_id,
        reason,
        clock,
    )
    if decision == "approved":
        applied = await session.scalar(
            text("SELECT identity_apply_client_lock(:id)"), {"id": change_id}
        )
        if not applied:
            # The account moved since the proposal (closed, or already in that state).
            raise ApplicationError("conflict")
        if change.kind == "lock":
            await session.execute(
                text(
                    "UPDATE identity_sessions SET revoked_at = :now, revoked_reason = 'locked' "
                    "WHERE actor_kind = 'client' AND actor_id = :id AND revoked_at IS NULL"
                ),
                {"now": now, "id": change.client_id},
            )
        logger.info("client_lock_applied", kind=change.kind, change_id=change_id)
    return await _by_id(session, change_id)


def _check(kind: LockKind, status: str) -> None:
    wanted = "active" if kind == "lock" else "locked"
    if status != wanted:
        code = "already_locked" if status == "locked" else "not_locked"
        if status == "closed":
            code = "closed"
        raise ApplicationError(
            "conflict", fields=[FieldProblem(field_path=["body", "kind"], code=code)]
        )


async def _by_id(session: AsyncSession, change_id: str) -> LockChange:
    row = (await session.execute(_CHANGE, {"id": change_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _change(row)


async def _audit(
    session: AsyncSession,
    staff_id: str,
    action: str,
    change_id: str,
    client_id: str,
    reason: str,
    clock: Clock,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category="approval" if action.endswith(("approved", "rejected")) else "change",
            action=action,
            resource_type=RESOURCE,
            resource_id=change_id,
            actor=AuditActor.staff(staff_id),
            reason=reason,
            details={"client_id": client_id},
        ),
        clock,
    )


def _account(row: Row[Any]) -> ClientAccount:
    return ClientAccount(
        id=row.id,
        phone=row.phone,
        email=row.email,
        status=row.status,
        created_at=row.created_at,
        restricted_until=row.restricted_until,
        has_passkey=row.has_passkey,
        has_authenticator=row.has_authenticator,
        last_signed_in_at=row.last_signed_in_at,
    )


def _change(row: Row[Any]) -> LockChange:
    return LockChange(
        id=row.id,
        kind=row.kind,
        client_id=row.client_id,
        reason=row.reason,
        proposed_by=row.proposed_by,
        proposed_by_name=row.proposed_by_name,
        proposed_at=row.proposed_at,
        status=row.status,
        decided_by=row.decided_by,
        decided_by_name=row.decided_by_name,
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
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["body", "reason"], code="required")],
        )
    return cleaned


async def close_account(
    session: AsyncSession, request_id: str, client_id: str, now: datetime
) -> None:
    """Close the client of a closure approved in this same transaction.

    Called only by the closures module, after it has approved the request. Every session the
    client holds ends, and they can never sign in again. Migration 0232 refuses the change
    unless the approval was made in this transaction, for this client.
    """
    if not await session.scalar(text("SELECT identity_close_client(:id)"), {"id": request_id}):
        # The account moved since the request (locked, or already closed).
        raise ApplicationError("conflict")
    await session.execute(
        text(
            "UPDATE identity_sessions SET revoked_at = :now, revoked_reason = 'closed' "
            "WHERE actor_kind = 'client' AND actor_id = :id AND revoked_at IS NULL"
        ),
        {"now": now, "id": client_id},
    )
    logger.info("client_account_closed", request_id=request_id)
