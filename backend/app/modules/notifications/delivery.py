"""Sending a notification beyond the app: push, SMS and email, as the client chose.

`notify` writes the notification and queues one fan-out job in the caller's transaction. The
worker runs it:

1. **Fan-out** reads the notification and the client's channels for its kind of alert. It
   queues one push job if the client has a device, and one SMS and one email through
   `queue_message` if the client has a phone number or an email address. All of it commits
   with the job, so it happens once.
2. **Push** sends the alert to the client's devices through Expo, with no transaction open,
   and stops using a device Expo says is gone.

SMS and email go through the queue the sign-in codes use, under the `client_notice`
template, so they get its retries and its clearing of content after sending. Every key is
the notification's ID and the channel, so nothing is sent twice for one notification.

Job payloads carry the notification's ID only. The words and the addresses are read when the
job runs.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Protocol

import structlog
from sqlalchemy import text
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
    UncertainOutcomeError,
    enqueue,
    external_effect,
)
from app.infrastructure.push import (
    InvalidPush,
    PushDeferred,
    PushMessage,
    PushRejected,
    PushTransport,
    PushUncertain,
)
from app.modules.notifications import service
from app.modules.notifications.domain.events import Category, Channel
from app.modules.notifications.domain.preferences import ALWAYS_ON
from app.modules.notifications.email_delivery import is_suppressed
from app.modules.notifications.preferences import channels_for_client
from app.modules.notifications.service import queue_message
from app.shared.clock import Clock

FAN_OUT = "notifications.fan_out"
SEND_PUSH = "notifications.push"
# An alert that has not gone out within this is no longer news. The app still holds it.
PUSH_WITHIN = timedelta(hours=6)
NOTICE_WITHIN = timedelta(hours=12)

logger = structlog.get_logger()


class ClientContact(Protocol):
    """Where to reach a client, as identity's `client_contact` answers."""

    @property
    def phone(self) -> str | None: ...

    @property
    def email(self) -> str | None: ...

    @property
    def claimed(self) -> bool:
        """False for an account carried over from Finvest 1.0 that its owner has not claimed."""
        ...


# Looks up where to reach a client, in the worker's transaction. Identity owns the answer, and
# answers nothing for a client who is locked or closed.
ContactBook = Callable[[AsyncSession, str], Awaitable[ClientContact | None]]


async def queue_fan_out(session: AsyncSession, notification_id: str, now: datetime) -> None:
    """Queue the sending of one notification. Called by `notify` in the caller's transaction."""
    await enqueue(
        session,
        JobRequest(
            kind=FAN_OUT,
            idempotency_key=f"fan-out:{notification_id}",
            payload={"notification_id": notification_id},
            queue=service.MESSAGE_QUEUE,
            max_age=NOTICE_WITHIN,
        ),
        now=now,
    )


_NOTIFICATION = text(
    "SELECT id, client_id, event, category, title, body, sms, link_kind, link_id "
    "FROM notifications WHERE id = :id"
)
# The documents module emails these itself, with the PDF attached. Emailing the notice as well
# would tell the client twice. Push and the app still carry them.
EMAILED_BY_DOCUMENTS = frozenset({"statement_ready", "receipt_ready"})
# A phone shared by two people gets only the alerts of whoever registered it last.
_LIVE_TOKENS = text(
    "SELECT d.token FROM push_devices d WHERE d.client_id = :client_id AND d.removed_at IS NULL "
    "AND NOT EXISTS (SELECT FROM push_devices o WHERE o.token = d.token "
    "AND o.client_id <> d.client_id AND o.registered_at > d.registered_at) "
    "ORDER BY d.registered_at DESC LIMIT 100"
)
_REMOVE_TOKENS = text(
    "UPDATE push_devices SET removed_at = :now, removed_reason = 'not_registered' "
    "WHERE token = ANY(CAST(:tokens AS text[])) AND removed_at IS NULL"
)


class DeliveryJobs:
    def __init__(
        self, database: Database, clock: Clock, push: PushTransport, contacts: ContactBook
    ) -> None:
        self._database = database
        self._clock = clock
        self._push = push
        self._contacts = contacts

    def handlers(self) -> dict[str, JobHandler | Handler]:
        # Fan-out only writes to the database. A push reaches Expo, so a crash mid-send stops
        # the job rather than showing the client the same alert twice.
        return {FAN_OUT: self.fan_out, SEND_PUSH: external_effect(self.send_push)}

    async def fan_out(self, job: ClaimedJob, context: JobContext) -> None:
        notification_id = str(job.payload["notification_id"])
        now = self._clock.now()

        async def queue_sends(session: AsyncSession) -> dict[str, object]:
            row = (await session.execute(_NOTIFICATION, {"id": notification_id})).one_or_none()
            if row is None:
                raise PermanentJobError(
                    "The notification does not exist", code="notification_missing"
                )
            channels = (await channels_for_client(session, row.client_id))[Category(row.category)]
            if row.event in EMAILED_BY_DOCUMENTS:
                channels = channels - {Channel.EMAIL}
            queued: list[str] = []
            if Channel.PUSH in channels:
                tokens = (
                    (await session.execute(_LIVE_TOKENS, {"client_id": row.client_id}))
                    .scalars()
                    .all()
                )
                if tokens:
                    await enqueue(
                        session,
                        JobRequest(
                            kind=SEND_PUSH,
                            idempotency_key=f"push:{notification_id}",
                            payload={"notification_id": notification_id},
                            queue=service.MESSAGE_QUEUE,
                            max_age=PUSH_WITHIN,
                        ),
                        now=now,
                    )
                    queued.append("push")
            if channels & {Channel.SMS, Channel.EMAIL}:
                contact = await self._contacts(session, row.client_id)
                if contact is not None and not contact.claimed:
                    # Nobody has proved this number or address is still theirs. The claim
                    # invitation reaches them; the notices wait in the app until they claim.
                    contact = None
                by_sms = Channel.SMS in channels
                if (
                    not by_sms
                    and Category(row.category) in ALWAYS_ON
                    and Channel.EMAIL in channels
                    and contact
                    and contact.phone
                    and (not contact.email or await is_suppressed(session, contact.email))
                ):
                    # A security alert must reach the client outside the app. Their email
                    # address bounced for good or reported us as spam, so text them instead.
                    by_sms = True
                if by_sms and contact and contact.phone:
                    await queue_message(
                        session,
                        channel="sms",
                        recipient=contact.phone,
                        template="client_notice",
                        variables={"sms": row.sms},
                        idempotency_key=f"notice:{notification_id}:sms",
                        now=now,
                        deliver_within=NOTICE_WITHIN,
                    )
                    queued.append("sms")
                if Channel.EMAIL in channels and contact and contact.email:
                    await queue_message(
                        session,
                        channel="email",
                        recipient=contact.email,
                        template="client_notice",
                        variables={"title": row.title, "body": row.body},
                        idempotency_key=f"notice:{notification_id}:email",
                        now=now,
                        deliver_within=NOTICE_WITHIN,
                    )
                    queued.append("email")
            # The channels, never the words or the addresses.
            return {"queued": queued}

        result = await context.apply(queue_sends)
        logger.info(
            "notification_fanned_out",
            notification_id=notification_id,
            channels=result.get("queued"),
        )

    async def send_push(self, job: ClaimedJob, context: JobContext) -> None:
        notification_id = str(job.payload["notification_id"])
        async with self._database.transaction() as session:
            row = (await session.execute(_NOTIFICATION, {"id": notification_id})).one_or_none()
            tokens = (
                (await session.execute(_LIVE_TOKENS, {"client_id": row.client_id})).scalars().all()
                if row is not None
                else []
            )
        if row is None:
            raise PermanentJobError("The notification does not exist", code="notification_missing")
        if not tokens:
            # Signed out everywhere since the fan-out: nothing to send to.
            await context.apply(_nothing)
            return
        data = {"notification_id": row.id}
        if row.link_kind:
            data |= {"kind": row.link_kind, "id": row.link_id}
        try:
            # No transaction is open while Expo is called.
            message = PushMessage(tokens=tuple(tokens), title=row.title, body=row.body, data=data)
            sent = await self._push.send(message)
        except (InvalidPush, PushRejected) as error:
            code = getattr(error, "code", "push_invalid")
            logger.warning("push_refused", notification_id=notification_id, error_code=code)
            raise PermanentJobError(
                getattr(error, "safe_message", "The push was refused"), code=code
            ) from None
        except PushDeferred as error:
            raise RetryableJobError(error.safe_message, code=error.code) from None
        except PushUncertain as error:
            # The alert may be on the client's phone. Sending again could show it twice, and the
            # notification is in the app either way, so the job stops here.
            raise UncertainOutcomeError(error.safe_message) from None
        now = self._clock.now()

        async def record(session: AsyncSession) -> dict[str, object]:
            if sent.dead_tokens:
                await session.execute(
                    _REMOVE_TOKENS, {"tokens": list(sent.dead_tokens), "now": now}
                )
            return {
                "provider": sent.provider,
                "delivered": sent.delivered,
                "removed": len(sent.dead_tokens),
            }

        await context.apply(record)
        logger.info("push_sent", notification_id=notification_id, delivered=sent.delivered)


async def _nothing(session: AsyncSession) -> dict[str, object]:
    return {}
