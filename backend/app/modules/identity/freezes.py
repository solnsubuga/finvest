"""A client freezes their own account, for example after losing their phone.

Freezing locks the account at once and ends every session the client holds, so nobody holding
the phone or a password can sign in. Unfreezing is not here: the client contacts support, and
staff unlock the account with two people (client_locks.py), after checking who they are.

Migration 0235 holds the rules PostgreSQL enforces: only the acting client freezes, and only
themselves, through `identity_freeze_client`.
"""

from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

RESOURCE = "client_freeze"
MAX_REASON = 500
logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class Freeze:
    id: str
    client_id: str
    reason: str | None
    frozen_at: datetime


_FREEZES = text(
    "SELECT id, client_id, reason, frozen_at FROM identity_client_freezes "
    "WHERE client_id = :client_id ORDER BY frozen_at DESC, id DESC"
)


async def freeze(session: AsyncSession, client: Actor, reason: str | None, clock: Clock) -> Freeze:
    """Lock the signed-in client's account and end every session they hold."""
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    client_id = client.id
    cleaned = " ".join(reason.split()) if reason else None
    if cleaned is not None and len(cleaned) > MAX_REASON:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=["body", "reason"], code="too_long")],
        )
    now = clock.now()
    freeze_id = push_id(clock)
    frozen = await session.scalar(
        text("SELECT identity_freeze_client(:id, :reason, :now)"),
        {"id": freeze_id, "reason": cleaned or None, "now": now},
    )
    if not frozen:
        # Already locked or closed.
        raise ApplicationError("conflict")
    await session.execute(
        text(
            "UPDATE identity_sessions SET revoked_at = :now, revoked_reason = 'frozen' "
            "WHERE actor_kind = 'client' AND actor_id = :id AND revoked_at IS NULL"
        ),
        {"now": now, "id": client_id},
    )
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="identity.client.frozen",
            resource_type=RESOURCE,
            resource_id=freeze_id,
            actor=AuditActor.client(client_id),
            details={"client_id": client_id},
        ),
        clock,
    )
    logger.info("client_account_frozen", freeze_id=freeze_id)
    return Freeze(id=freeze_id, client_id=client_id, reason=cleaned or None, frozen_at=now)


async def freezes(session: AsyncSession, client_id: str) -> list[Freeze]:
    """Every time the client froze their account, newest first. For the client and staff."""
    rows = await session.execute(_FREEZES, {"client_id": client_id})
    return [
        Freeze(id=r.id, client_id=r.client_id, reason=r.reason, frozen_at=r.frozen_at) for r in rows
    ]
