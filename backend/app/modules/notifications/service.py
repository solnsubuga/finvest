"""Queue an email or a text message inside the caller's transaction. The worker sends it."""

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.email import check_address
from app.infrastructure.jobs import JobRequest, enqueue
from app.infrastructure.jobs.queue import to_json
from app.infrastructure.sms import check_phone
from app.modules.notifications.domain.templates import Template, render, render_sms
from app.shared.push_ids import push_id

SEND_MESSAGE = "notifications.send"
PURGE_MESSAGES = "notifications.purge"
Channel = Literal["email", "sms"]
# The job queue messages wait on. One name, so tests can give themselves a queue of their own.
MESSAGE_QUEUE = "default"


async def queue_message(
    session: AsyncSession,
    *,
    channel: Channel,
    recipient: str,
    template: Template,
    variables: Mapping[str, object],
    idempotency_key: str,
    now: datetime,
    deliver_within: timedelta = timedelta(hours=6),
) -> None:
    """Add one message to the queue, in the transaction that decided to send it.

    The same `idempotency_key` queues nothing a second time. `deliver_within` is how long the
    message is still worth sending: a sign-in code that arrives after it has expired is noise.
    Raises `InvalidEmail`, `InvalidSms` or `InvalidTemplate` before anything is written.
    """
    if not session.in_transaction():
        raise RuntimeError("A message is queued inside the business transaction")
    if deliver_within <= timedelta(0):
        raise ValueError("A message needs time to be delivered")
    # Rendering now proves the template and its variables are right before anything is stored.
    if channel == "email":
        check_address(recipient)
        render(template, variables)
    else:
        check_phone(recipient)
        render_sms(template, variables)
    # No RETURNING: the API role may queue a message but has no right to read one.
    await session.execute(
        text(
            "INSERT INTO notification_messages (id, idempotency_key, channel, recipient, "
            "template, variables, created_at) VALUES (:id, :key, :channel, :recipient, "
            ":template, CAST(:variables AS jsonb), :now) ON CONFLICT (idempotency_key) DO NOTHING"
        ),
        {
            "id": push_id(),
            "key": idempotency_key,
            "channel": channel,
            "recipient": recipient,
            "template": template,
            "variables": to_json({name: str(value) for name, value in variables.items()}),
            "now": now,
        },
    )
    # The job names the message by its key, so the payload holds no address and no content.
    await enqueue(
        session,
        JobRequest(
            kind=SEND_MESSAGE,
            idempotency_key=idempotency_key,
            payload={"message_key": idempotency_key},
            queue=MESSAGE_QUEUE,
            max_age=deliver_within,
        ),
        now=now,
    )
