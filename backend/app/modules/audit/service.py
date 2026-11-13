"""Recording audit events inside the caller's transaction, and reading them back.

`record` writes into the caller's open transaction, so a business change and its audit event
commit or roll back together. `record_separately` is for a denied or failed action, whose
event must outlive the rollback. The table is append-only for every database role, and row
policies let a transaction record only its own actor (migration 0012).
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.audit.domain.events import (
    ActorKind,
    AuditCategory,
    AuditEvent,
    AuditOutcome,
    AuditRecord,
    DetailValue,
    build_record,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.push_ids import push_id

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


@dataclass(frozen=True, slots=True)
class AuditEventView:
    id: str
    occurred_at: datetime
    category: AuditCategory
    action: str
    outcome: AuditOutcome
    actor_kind: ActorKind
    actor_id: str | None
    resource_type: str
    resource_id: str | None
    reason: str | None
    correlation_id: str | None
    details: dict[str, DetailValue]


_INSERT = text(
    "INSERT INTO audit_events (id, occurred_at, category, action, outcome, actor_kind, actor_id, "
    "resource_type, resource_id, reason, correlation_id, details) VALUES (:id, :occurred_at, "
    ":category, :action, :outcome, :actor_kind, :actor_id, :resource_type, :resource_id, "
    ":reason, :correlation_id, CAST(:details AS jsonb))"
)
_COLUMNS = (
    "id, occurred_at, category, action, outcome, actor_kind, actor_id, resource_type, "
    "resource_id, reason, correlation_id, details"
)
_FOR_RESOURCE = text(
    f"SELECT {_COLUMNS} FROM audit_events "  # noqa: S608 - constant column list
    "WHERE resource_type = :resource_type AND resource_id = :resource_id "
    "ORDER BY occurred_at DESC, id DESC LIMIT :limit"
)
_RECENT = text(
    f"SELECT {_COLUMNS} FROM audit_events "  # noqa: S608 - constant column list
    "ORDER BY occurred_at DESC, id DESC LIMIT :limit"
)


async def record(session: AsyncSession, event: AuditEvent, clock: Clock) -> AuditRecord:
    """Add `event` to the caller's open transaction. It commits or rolls back with that work.

    The event's actor must be the transaction's own actor; the database refuses any other.
    """
    if not session.in_transaction():
        raise RuntimeError("Audit events must be recorded inside the business transaction")
    entry = build_record(event, record_id=push_id(), occurred_at=clock.now())
    await session.execute(
        _INSERT,
        {
            "id": entry.id,
            "occurred_at": entry.occurred_at,
            "category": event.category,
            "action": event.action,
            "outcome": event.outcome,
            "actor_kind": event.actor.kind,
            "actor_id": event.actor.id,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "reason": entry.reason,
            "correlation_id": event.correlation_id,
            "details": json.dumps(entry.details, separators=(",", ":")),
        },
    )
    return entry


async def record_separately(
    database: Database, actor: Actor | None, event: AuditEvent, clock: Clock
) -> AuditRecord:
    """Record `event` in a transaction of its own, so it survives the caller's rollback.

    For a denied or failed action. Those usually end with the business transaction rolling
    back, which would take an event written by `record` with it. Call this after that
    transaction has ended, never inside it. `actor` is who the request acts for, or None when
    nobody is signed in; the event must name that same actor.
    """
    transaction = database.actor_transaction(actor) if actor else database.transaction()
    async with transaction as session:
        return await record(session, event, clock)


async def events_for_resource(
    session: AsyncSession, resource_type: str, resource_id: str, *, limit: int = DEFAULT_PAGE_SIZE
) -> list[AuditEventView]:
    """The newest events about one record. Only a staff transaction sees any rows."""
    rows = await session.execute(
        _FOR_RESOURCE,
        {"resource_type": resource_type, "resource_id": resource_id, "limit": _bounded(limit)},
    )
    return [_view(row) for row in rows.all()]


async def recent_events(
    session: AsyncSession, *, limit: int = DEFAULT_PAGE_SIZE
) -> list[AuditEventView]:
    """The newest events of any kind. Only a staff transaction sees any rows."""
    rows = await session.execute(_RECENT, {"limit": _bounded(limit)})
    return [_view(row) for row in rows.all()]


def _bounded(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_SIZE))


def _view(row: Row[Any]) -> AuditEventView:
    return AuditEventView(
        id=row.id,
        occurred_at=row.occurred_at,
        category=row.category,
        action=row.action,
        outcome=row.outcome,
        actor_kind=row.actor_kind,
        actor_id=row.actor_id,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        reason=row.reason,
        correlation_id=row.correlation_id,
        details=row.details,
    )
