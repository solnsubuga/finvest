"""Acting for a client from their page, always with two people.

One member of staff proposes an action with a reason, and a different one approves or rejects
it. The proposer may withdraw it. Approving applies the action in the same transaction, through
the module that owns what it changes, so nothing is ever approved and not yet done. Migration
0351 enforces the two people and records the approving transaction.

Each kind needs its own permission (`PERMISSIONS`) and has its own effect (`_apply`):

- `pin_reset`, with `client_pins.reset`: ends every phone app session the client holds, so they
  log in again and choose a new PIN (`identity.pin_resets`).
- `withdrawal_limits`, with `client_limits.change`: sets the client's own stricter withdrawal
  limits, or clears them (`payouts.set_client_limits`). `details` holds each limit in whole
  shillings, or null for none of that sort.
"""

import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, get_args

import structlog
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.identity import pin_resets
from app.modules.identity.client_locks import client_status
from app.modules.identity.domain.permissions import Permission
from app.modules.payouts import payouts
from app.modules.payouts.domain.limits import ClientLimits, InvalidLimits
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

RESOURCE = "client_action"
MAX_REASON = 500
Kind = Literal["pin_reset", "withdrawal_limits"]
KINDS: tuple[Kind, ...] = get_args(Kind)
Decision = Literal["approved", "rejected", "withdrawn"]
Status = Literal["proposed", "approved", "rejected", "withdrawn"]
logger = structlog.get_logger()

# Who may propose or decide each kind. Seeing the client is `clients.view` as well.
PERMISSIONS: dict[Kind, Permission] = {
    "pin_reset": "client_pins.reset",
    "withdrawal_limits": "client_limits.change",
}
LIMIT_FIELDS = (
    "mobile_money_per_payout",
    "mobile_money_per_day",
    "bank_per_payout",
    "bank_per_day",
    "review_above",
)


@dataclass(frozen=True, slots=True)
class ClientAction:
    id: str
    client_id: str
    kind: Kind
    details: dict[str, Any]
    reason: str
    proposed_by: str
    proposed_by_name: str
    proposed_at: datetime
    status: Status
    decided_by: str | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_reason: str | None


_SELECT = (
    "SELECT a.id, a.client_id, a.kind, a.details, a.reason, a.proposed_by, "
    "p.name AS proposed_by_name, a.proposed_at, a.status, a.decided_by, "
    "d.name AS decided_by_name, a.decided_at, a.decision_reason FROM console_client_actions a "
    "JOIN identity_staff p ON p.id = a.proposed_by "
    "LEFT JOIN identity_staff d ON d.id = a.decided_by "
)
_FOR_CLIENT = text(
    _SELECT + "WHERE a.client_id = :client_id ORDER BY a.proposed_at DESC, a.id DESC"
)
_WAITING = text(_SELECT + "WHERE a.status = 'proposed' ORDER BY a.proposed_at, a.id")
_BY_ID = text(_SELECT + "WHERE a.id = :id")
_LOCK = text(
    "SELECT id FROM console_client_actions WHERE id = :id AND status = 'proposed' FOR UPDATE"
)


async def actions_for_client(session: AsyncSession, client_id: str) -> list[ClientAction]:
    """Every action proposed for the client, newest first."""
    return [_action(row) for row in await session.execute(_FOR_CLIENT, {"client_id": client_id})]


async def waiting(session: AsyncSession) -> list[ClientAction]:
    """Every action waiting for a second person, oldest first, for the approvals inbox."""
    return [_action(row) for row in await session.execute(_WAITING)]


async def propose(
    session: AsyncSession,
    staff: Actor,
    client_id: str,
    kind: Kind,
    reason: str,
    clock: Clock,
    details: dict[str, Any] | None = None,
) -> ClientAction:
    """Ask to act for the client. A different member of staff decides."""
    acting = _staff_id(staff)
    reason = _reason(reason)
    status = await client_status(session, client_id)
    if status is None:
        raise ApplicationError("not_found")
    _check_client(status)
    details = details or {}
    if kind == "withdrawal_limits":
        await _check_limits(session, details, clock)
    elif details:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["body", "limits"], code="not_allowed")],
        )
    action_id = push_id(clock)
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO console_client_actions (id, client_id, kind, details, reason, "
                    "proposed_by, proposed_at) VALUES (:id, :client_id, :kind, "
                    "CAST(:details AS jsonb), :reason, :staff, :now)"
                ),
                {
                    "id": action_id,
                    "client_id": client_id,
                    "kind": kind,
                    "details": _json(details),
                    "reason": reason,
                    "staff": acting,
                    "now": clock.now(),
                },
            )
    except IntegrityError:
        # One of this kind is already waiting for this client.
        raise ApplicationError("conflict") from None
    await _audit(
        session, acting, f"console.client.{kind}_proposed", action_id, client_id, reason, clock
    )
    return await _by_id(session, action_id)


async def decide(
    session: AsyncSession,
    staff: Actor,
    action_id: str,
    decision: Decision,
    reason: str,
    clock: Clock,
    permissions: Collection[str],
) -> ClientAction:
    """Approve or reject someone else's proposal, or withdraw your own.

    Approving applies the action in this same transaction.
    """
    acting = _staff_id(staff)
    reason = _reason(reason)
    if (await session.execute(_LOCK, {"id": action_id})).one_or_none() is None:
        raise ApplicationError("not_found")
    action = await _by_id(session, action_id)
    if PERMISSIONS[action.kind] not in permissions:
        raise ApplicationError("forbidden")
    if (action.proposed_by == acting) != (decision == "withdrawn"):
        # PostgreSQL refuses this too. Answering here gives the console a clean refusal.
        raise ApplicationError("forbidden")
    now = clock.now()
    if decision == "approved":
        _check_client(str(await client_status(session, action.client_id)))
    await session.execute(
        text(
            "UPDATE console_client_actions SET status = :status, decided_by = :staff, "
            "decided_at = :now, decision_reason = :reason, decided_in = CASE WHEN "
            ":status = 'approved' THEN pg_current_xact_id() END WHERE id = :id"
        ),
        {"status": decision, "staff": acting, "now": now, "reason": reason, "id": action_id},
    )
    await _audit(
        session,
        acting,
        f"console.client.{action.kind}_{decision}",
        action_id,
        action.client_id,
        reason,
        clock,
    )
    if decision == "approved":
        await _apply(session, action, now)
        logger.info("client_action_applied", kind=action.kind, action_id=action_id)
    return await _by_id(session, action_id)


async def _apply(session: AsyncSession, action: ClientAction, now: datetime) -> None:
    if action.kind == "pin_reset":
        await pin_resets.end_phone_sessions(session, action.client_id, now, action_id=action.id)
    elif action.kind == "withdrawal_limits":
        await payouts.set_client_limits(
            session,
            action.client_id,
            client_limits(action.details),
            action_id=action.id,
            now=now,
        )


def client_limits(details: dict[str, Any]) -> ClientLimits:
    """The limits a `withdrawal_limits` action holds, in shillings."""

    def money(name: str) -> Money | None:
        value = details.get(name)
        return None if value is None else Money(int(value), Currency.UGX)

    return ClientLimits(**{name: money(name) for name in LIMIT_FIELDS})


async def _check_limits(session: AsyncSession, details: dict[str, Any], clock: Clock) -> None:
    """Each limit is a whole number of shillings below the shared limit of its sort, if any.

    A client's limits only ever tighten the shared ones, so a figure at or above the shared
    limit would do nothing and is refused rather than shown as if it raised it.
    """
    if set(details) != set(LIMIT_FIELDS):
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["body", "limits"], code="required")],
        )
    shared = await payouts.current_limits(session, business_date(clock.now()))
    for name in LIMIT_FIELDS:
        value = details[name]
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["body", "limits", name], code="not_positive")],
            )
        ceiling = getattr(shared, name)
        if ceiling is not None and value >= ceiling.minor:
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["body", "limits", name], code="not_below_shared")],
            )
    try:
        client_limits(details)
    except InvalidLimits:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["body", "limits"], code="invalid")],
        ) from None


def _check_client(status: str) -> None:
    # Staff act only for an account that is open. A locked account is unlocked first.
    if status != "active":
        code = "closed" if status == "closed" else "not_active"
        raise ApplicationError(
            "conflict", fields=[FieldProblem(field_path=["path", "client_id"], code=code)]
        )


async def _by_id(session: AsyncSession, action_id: str) -> ClientAction:
    row = (await session.execute(_BY_ID, {"id": action_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _action(row)


async def _audit(
    session: AsyncSession,
    staff_id: str,
    action: str,
    action_id: str,
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
            resource_id=action_id,
            actor=AuditActor.staff(staff_id),
            reason=reason,
            details={"client_id": client_id},
        ),
        clock,
    )


def _action(row: Row[Any]) -> ClientAction:
    return ClientAction(
        id=row.id,
        client_id=row.client_id,
        kind=row.kind,
        details=dict(row.details),
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


def _json(details: dict[str, Any]) -> str:
    return json.dumps(details, sort_keys=True, default=str)


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
