"""What identity tells a client about changes to how they sign in. See docs/notifications.md.

Both are security alerts, which a client cannot switch off. Each is raised in the transaction
of the change, after `trail.note` has set the client as its actor, so the client is told once
and only if the change holds. Staff are never told this way: they have no inbox.
"""

from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications import contract as notifications

SecondStepChange = Literal[
    "passkey_added", "passkey_removed", "authenticator_added", "authenticator_removed"
]

# Fixed words, never anything a request sent. Each is the start of the notice.
_CHANGES: dict[SecondStepChange, str] = {
    "passkey_added": "A passkey was added to your account",
    "passkey_removed": "A passkey was removed from your account",
    "authenticator_added": "An authenticator app was set up for your account",
    "authenticator_removed": "The authenticator app was removed from your account",
}


async def password_changed(session: AsyncSession, client_id: str, key: str, now: datetime) -> None:
    await notifications.notify(
        session,
        event_name="password_changed",
        client_id=client_id,
        idempotency_key=key,
        variables={},
        link=notifications.Link("security", key),
        now=now,
    )


async def second_step_changed(
    session: AsyncSession, client_id: str, change: SecondStepChange, key: str, now: datetime
) -> None:
    await notifications.notify(
        session,
        event_name="second_step_changed",
        client_id=client_id,
        idempotency_key=f"{change}:{key}",
        variables={"change": _CHANGES[change]},
        link=notifications.Link("security", key),
        now=now,
    )
