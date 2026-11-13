"""Audit events for sign-in: who gained or lost a way in, and when a session opened.

Each event is written in the transaction that made the change, as the person it is about, so
the two commit or roll back together. Details carry method names and identifiers only.
"""

from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession
from structlog.contextvars import get_contextvars

from app.infrastructure.database.engine import apply_actor
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.audit.service import record
from app.shared.actors import Actor
from app.shared.clock import Clock


async def note(
    session: AsyncSession,
    clock: Clock,
    actor: Actor,
    action: str,
    *,
    resource_type: str,
    resource_id: str | None,
    details: Mapping[str, object] | None = None,
) -> None:
    """Record a security event about `actor` in the caller's transaction."""
    if actor.kind == "system":
        raise ValueError("Sign-in events are about a client or a member of staff")
    # The audit table accepts an event only from a transaction acting as the same person.
    await apply_actor(session, actor)
    request_id = get_contextvars().get("request_id")
    await record(
        session,
        AuditEvent(
            category="security",
            action=action,
            resource_type=resource_type,
            actor=AuditActor(actor.kind, actor.id),
            resource_id=resource_id,
            correlation_id=request_id if isinstance(request_id, str) else None,
            details=details or {},
        ),
        clock,
    )
