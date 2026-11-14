"""The worker's side of messages: send what was queued, and clear old content."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.email import (
    EmailDeferred,
    EmailMessage,
    EmailRejected,
    EmailTransport,
    InvalidEmail,
)
from app.infrastructure.jobs import (
    ClaimedJob,
    Handler,
    JobContext,
    JobHandler,
    PermanentJobError,
    RetryableJobError,
    UncertainOutcomeError,
    external_effect,
)
from app.infrastructure.jobs.queue import from_json
from app.infrastructure.sms import (
    InvalidSms,
    SmsDeferred,
    SmsMessage,
    SmsRejected,
    SmsTransport,
    SmsUncertain,
)
from app.modules.notifications.domain.templates import InvalidTemplate, render, render_sms
from app.modules.notifications.email_delivery import is_suppressed
from app.modules.notifications.service import PURGE_MESSAGES, SEND_MESSAGE
from app.shared.clock import Clock
from app.shared.email_layout import PRODUCTION, Links

# A queued message with no delivery deadline keeps its content at most this long. One with a
# deadline is cleared by the first purge after it.
UNSENT_CONTENT = timedelta(days=1)
# After this, only the delivery facts remain: no address and no number.
RECIPIENT_RETENTION = timedelta(days=30)

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class _Sent:
    provider: str
    message_id: str | None


def handlers(
    database: Database,
    clock: Clock,
    email: EmailTransport,
    sms: SmsTransport,
    link_origins: Sequence[str] = (),
    links: Links = PRODUCTION,
) -> dict[str, JobHandler | Handler]:
    """`link_origins` are the only sites a link in a message may point to. `links` are the app
    and website an email's own buttons and footer open, from the worker's settings."""

    async def deliver(channel: str, recipient: str, template: str, key: str, values: dict) -> _Sent:  # type: ignore[type-arg]
        """Render and send. Raises the job error that says whether a retry is safe."""
        if channel == "email":
            rendered = render(template, values, link_origins=link_origins, links=links)
            message = EmailMessage(
                to=recipient,
                subject=rendered.subject,
                html=rendered.html,
                text=rendered.text,
                idempotency_key=key,
                tags={"template": template},
            )
            try:
                result = await email.send(message)
            except EmailDeferred as error:
                raise RetryableJobError(error.safe_message, code=error.code) from None
            return _Sent(result.provider, result.provider_message_id)
        text_message = SmsMessage(
            to=recipient,
            text=render_sms(template, values, link_origins=link_origins),
            idempotency_key=key,
        )
        try:
            sent = await sms.send(text_message)
        except SmsDeferred as error:
            raise RetryableJobError(error.safe_message, code=error.code) from None
        except SmsUncertain as error:
            # It may have been delivered. Sending again could text the person twice, so the
            # job stops here. A code that did not arrive is asked for again by the person.
            raise UncertainOutcomeError(error.safe_message) from None
        return _Sent(sent.provider, sent.provider_message_id)

    async def send_message(job: ClaimedJob, context: JobContext) -> None:
        key = str(job.payload["message_key"])
        async with database.transaction() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, channel, recipient, template, variables, status "
                        "FROM notification_messages WHERE idempotency_key = :key"
                    ),
                    {"key": key},
                )
            ).one_or_none()
        if row is None:
            raise PermanentJobError("The queued message does not exist", code="message_missing")

        async def finished(
            session: AsyncSession, status: str, sent: _Sent | None, failure: str = "rejected"
        ) -> None:
            await session.execute(
                text(
                    "UPDATE notification_messages SET status = :status, "
                    "variables = '{}'::jsonb, provider = :provider, "
                    "provider_message_id = :message_id, failure_code = :failure, "
                    "finished_at = :now WHERE id = :id AND status = 'queued'"
                ),
                {
                    "status": status,
                    "provider": sent.provider if sent else None,
                    "message_id": sent.message_id if sent else None,
                    "failure": None if status == "sent" else failure,
                    "now": clock.now(),
                    "id": row.id,
                },
            )

        if row.status != "queued" or row.recipient is None:
            # Sent by an earlier attempt whose result was saved, or cleared since.
            await context.apply(_nothing)
            return
        if row.channel == "email":
            async with database.transaction() as session:
                suppressed = await is_suppressed(session, row.recipient)
            if suppressed:
                # The address bounced for good or reported our mail as spam. Sending again
                # would only harm the sending domain's standing with every other inbox.

                async def suppress(session: AsyncSession) -> dict[str, object]:
                    await finished(session, "failed", None, "suppressed")
                    return {"suppressed": True}

                await context.apply(suppress)
                logger.warning("message_suppressed", message_id=row.id, template=row.template)
                return
        try:
            # No transaction is open while the provider is called.
            sent = await deliver(
                row.channel, row.recipient, row.template, key, from_json(row.variables)
            )
        except (
            InvalidEmail,
            InvalidSms,
            InvalidTemplate,
            EmailRejected,
            SmsRejected,
            UncertainOutcomeError,
        ) as error:
            # The content is cleared in its own transaction, then the job fails for good.
            # The real reason is kept, so support can tell "may have arrived" from "refused".
            code = getattr(error, "code", "message_invalid")
            async with database.transaction() as session:
                await finished(session, "failed", None, code)
            logger.warning("message_refused", message_id=row.id, error_code=code)
            if isinstance(error, UncertainOutcomeError):
                raise
            raise PermanentJobError(getattr(error, "safe_message", ""), code=code) from None

        async def record(session: AsyncSession) -> dict[str, object]:
            await finished(session, "sent", sent)
            return {"provider": sent.provider}

        await context.apply(record)
        logger.info("message_sent", message_id=row.id, channel=row.channel, template=row.template)

    async def purge_messages(job: ClaimedJob, context: JobContext) -> None:
        now = clock.now()

        async def purge(session: AsyncSession) -> dict[str, object]:
            await session.execute(
                text(
                    "UPDATE notification_messages SET status = 'failed', "
                    "variables = '{}'::jsonb, failure_code = 'expired', finished_at = :now "
                    "WHERE status = 'queued' "
                    "AND coalesce(deliver_by, created_at + :unsent) < :now"
                ),
                {"now": now, "unsent": UNSENT_CONTENT},
            )
            await session.execute(
                text(
                    "UPDATE notification_messages SET recipient = NULL "
                    "WHERE recipient IS NOT NULL AND status <> 'queued' AND created_at < :old"
                ),
                {"old": now - RECIPIENT_RETENTION},
            )
            return {}

        await context.apply(purge)

    # Sending reaches a provider. If the worker dies mid-send, or an error comes back that was
    # not sorted above, the message may have gone out, so the job stops and is not sent again.
    # The person asks for a new code or link, and the unsent content is cleared within a day.
    return {SEND_MESSAGE: external_effect(send_message), PURGE_MESSAGES: purge_messages}


async def _nothing(session: AsyncSession) -> dict[str, object]:
    return {}
