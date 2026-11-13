"""Resend's delivery events: recorded once by the API, applied by the worker.

The API checks the signature, saves the event under its `svix-id` and queues a job, all in
one short transaction, and answers. A retry of the same event finds the row and queues
nothing. The worker then records the delivery on the message it is about and, for a lasting
bounce or a spam complaint, adds the address to the suppression list, which `notifications.
send` checks before every email.
"""

from datetime import datetime, timedelta

import structlog
from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import (
    ClaimedJob,
    Handler,
    JobContext,
    JobHandler,
    JobRequest,
    PermanentJobError,
    RetryableJobError,
    enqueue,
)
from app.modules.notifications import service
from app.modules.notifications.domain.email_events import EmailEvent, address_hash, moves_forward
from app.shared.clock import Clock

APPLY_EVENT = "notifications.email_event"
# Resend can report on an email before the worker has saved Resend's id for it. An event that
# matches no message is tried again for this long, then kept as unmatched.
MATCH_WINDOW = timedelta(minutes=10)
# Enough attempts, at the worker's default backoff, to outlast the window above.
EVENT_ATTEMPTS = 12
EVENT_MAX_AGE = timedelta(days=2)

logger = structlog.get_logger()

_INSERT_EVENT = text(
    "INSERT INTO notification_email_events (svix_id, delivery, email_id, recipients, "
    "permanent, occurred_at, received_at) VALUES (:svix_id, :delivery, :email_id, "
    "CAST(:recipients AS text[]), :permanent, :occurred_at, :now) "
    "ON CONFLICT DO NOTHING"
)


async def record_event(
    session: AsyncSession, svix_id: str, event: EmailEvent, now: datetime
) -> bool:
    """Save a verified event and queue the job that applies it. False for one already seen."""
    saved: CursorResult[tuple[()]] = await session.execute(  # type: ignore[assignment]
        _INSERT_EVENT,
        {
            "svix_id": svix_id,
            "delivery": event.delivery,
            "email_id": event.email_id,
            "recipients": list(event.recipients),
            "permanent": event.permanent,
            "occurred_at": event.occurred_at,
            "now": now,
        },
    )
    if saved.rowcount != 1:
        return False
    await enqueue(
        session,
        JobRequest(
            kind=APPLY_EVENT,
            idempotency_key=f"email-event:{svix_id}",
            payload={"svix_id": svix_id},
            queue=service.MESSAGE_QUEUE,
            max_age=EVENT_MAX_AGE,
            max_attempts=EVENT_ATTEMPTS,
        ),
        now=now,
    )
    return True


async def is_suppressed(session: AsyncSession, address: str) -> bool:
    found = await session.execute(
        text("SELECT 1 FROM notification_email_suppressions WHERE address_hash = :hash"),
        {"hash": address_hash(address)},
    )
    return found.first() is not None


class DeliveryEventJobs:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {APPLY_EVENT: self.apply_event}

    async def apply_event(self, job: ClaimedJob, context: JobContext) -> None:
        svix_id = str(job.payload["svix_id"])
        now = self.clock.now()
        async with self.database.transaction() as session:
            event = (
                await session.execute(
                    text(
                        "SELECT delivery, email_id, recipients, permanent, occurred_at, "
                        "received_at, processed_at FROM notification_email_events "
                        "WHERE svix_id = :id"
                    ),
                    {"id": svix_id},
                )
            ).one_or_none()
            if event is None:
                raise PermanentJobError("The email event does not exist", code="event_missing")
            message = (
                await session.execute(
                    text(
                        "SELECT id, delivery FROM notification_messages "
                        "WHERE provider = 'resend' AND provider_message_id = :email_id"
                    ),
                    {"email_id": event.email_id},
                )
            ).one_or_none()
        if event.processed_at is not None:
            await context.apply(_nothing)
            return
        if message is None and now - event.received_at < MATCH_WINDOW:
            raise RetryableJobError("The email is not recorded yet", code="email_not_recorded")

        suppresses = event.delivery == "complained" or (
            event.delivery == "bounced" and event.permanent
        )

        async def apply(session: AsyncSession) -> dict[str, object]:
            outcome = "unmatched"
            if message is not None:
                outcome = "unchanged"
                if moves_forward(message.delivery, event.delivery):
                    # The guard repeats the check in SQL, so a later event applied in between
                    # is never overwritten by an earlier one.
                    changed = await session.execute(
                        text(
                            "UPDATE notification_messages SET delivery = :delivery, "
                            "delivery_at = :at WHERE id = :id AND delivery IS NOT DISTINCT "
                            "FROM :current"
                        ),
                        {
                            "delivery": event.delivery,
                            "at": event.occurred_at or event.received_at,
                            "id": message.id,
                            "current": message.delivery,
                        },
                    )
                    if changed.rowcount != 1:  # type: ignore[attr-defined]
                        raise RetryableJobError("The message changed", code="delivery_changed")
                    outcome = "applied"
            if suppresses:
                for recipient in event.recipients:
                    await session.execute(
                        text(
                            "INSERT INTO notification_email_suppressions (address_hash, reason, "
                            "svix_id, created_at) VALUES (:hash, :reason, :svix_id, :now) "
                            "ON CONFLICT (address_hash) DO NOTHING"
                        ),
                        {
                            "hash": recipient,
                            "reason": event.delivery,
                            "svix_id": svix_id,
                            "now": now,
                        },
                    )
            await session.execute(
                text(
                    "UPDATE notification_email_events SET processed_at = :now, "
                    "outcome = :outcome WHERE svix_id = :id AND processed_at IS NULL"
                ),
                {"now": now, "outcome": outcome, "id": svix_id},
            )
            return {"outcome": outcome}

        await context.apply(apply)
        logger.info(
            "email_event_applied",
            delivery=event.delivery,
            message_id=message.id if message else None,
            suppressed=suppresses,
        )


async def _nothing(session: AsyncSession) -> dict[str, object]:
    return {}
