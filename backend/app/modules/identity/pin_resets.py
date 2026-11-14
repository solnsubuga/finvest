"""Ending a client's phone app sessions when two members of staff reset their app PIN.

A PIN belongs to one phone app session, never to the account (migration 0221), so resetting it
means ending those sessions: the client logs in again with their password and a second step and
chooses a new PIN, as they would after forgetting it. The console module decides the reset with
two people and calls this in the transaction that approved it.
"""

from datetime import datetime

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.service import Channel, queue_message

logger = structlog.get_logger()


async def end_phone_sessions(
    session: AsyncSession, client_id: str, now: datetime, *, action_id: str
) -> int:
    """End every phone app session the client holds, and with it the PIN on that phone.

    Called only by the console module, inside the staff transaction that approved a PIN reset
    by two people. A PIN belongs to one phone app session (migration 0221), so the client logs
    in again with their password and a second step and chooses a new one. A browser session
    has no PIN and is left as it is. Returns how many sessions ended.
    """
    ended = await session.scalars(
        text(
            "UPDATE identity_sessions SET revoked_at = :now, revoked_reason = 'pin_reset' "
            "WHERE actor_kind = 'client' AND actor_id = :id AND transport = 'token' "
            "AND revoked_at IS NULL RETURNING id"
        ),
        {"now": now, "id": client_id},
    )
    count = len(ended.all())
    email = await session.scalar(
        text("SELECT email FROM identity_clients WHERE id = :id AND email_verified_at IS NOT NULL"),
        {"id": client_id},
    )
    phone = await session.scalar(
        text("SELECT phone FROM identity_clients WHERE id = :id"), {"id": client_id}
    )
    sends: tuple[tuple[Channel, str | None], ...] = (("sms", phone), ("email", email))
    for channel, recipient in sends:
        if recipient:
            await queue_message(
                session,
                channel=channel,
                recipient=str(recipient),
                template="app_signed_out",
                variables={},
                idempotency_key=f"pin-reset:{action_id}:{channel}",
                now=now,
            )
    logger.info("client_phone_sessions_ended", reason="pin_reset", sessions=count)
    return count
