"""The worker's side of report schedules: each morning, queue the emails due today, then send
each one on its own.

The morning run writes one `report_sends` row per schedule, recipient and period, and queues
its email in the same transaction, so a run that goes round twice queues nothing new. Only the
send job calls a provider, Resend, and it does so with no transaction open. Each send checks
again that the schedule is running and that the recipient is active and may download the
report; one that is not is recorded as `skipped` with the reason. Payloads and logs carry IDs
and codes only, never an address or a figure.
"""

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.email import (
    DisabledTransport,
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
    JobRequest,
    PermanentJobError,
    RetryableJobError,
    enqueue,
    external_effect,
)
from app.modules.identity import service as identity
from app.modules.identity.domain.permissions import permissions_for
from app.modules.notifications import contract as notifications
from app.modules.reports import schedules
from app.modules.reports.domain.catalogue import REPORTS, may_download
from app.modules.reports.domain.schedules import due, ready_email
from app.shared.actors import SYSTEM
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.email_layout import PRODUCTION, Links
from app.shared.push_ids import push_id

QUEUE_KIND = "reports.queue_scheduled"
SEND_KIND = "reports.send_scheduled"
QUEUE = "default"
# Resend drops a repeat of a key for 24 hours, so a send is retried only within that time.
SEND_MAX_AGE = timedelta(hours=20)
logger = structlog.get_logger()

_QUEUE_SEND = text(
    "INSERT INTO report_sends (id, schedule_id, staff_id, date_from, date_to, status, "
    "created_at) VALUES (:id, :schedule_id, :staff_id, :date_from, :date_to, 'queued', :now) "
    "ON CONFLICT (schedule_id, staff_id, date_from, date_to) DO NOTHING RETURNING id"
)
_SEND = text(
    "SELECT id, schedule_id, staff_id, date_from, date_to, status FROM report_sends WHERE id = :id"
)
_FINISH = text(
    "UPDATE report_sends SET status = :status, failure_code = :failure_code, finished_at = :now "
    "WHERE id = :id AND status = 'queued'"
)


def send_key(send_id: str) -> str:
    return f"report-send:{send_id}"


class ReportJobs:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        *,
        email: EmailTransport | None = None,
        console: str = "https://admin.finvestug.com",
        links: Links = PRODUCTION,
    ) -> None:
        self._database = database
        self._clock = clock
        self._email = email or DisabledTransport()
        # The console origin the email's button opens.
        self._console = console
        self._links = links

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            QUEUE_KIND: self.queue_due,
            # Sending reaches Resend. Its idempotency key makes a lost answer safe to repeat,
            # and a crash mid-send stops the job instead of sending blind.
            SEND_KIND: external_effect(self.send),
        }

    async def queue_due(self, job: ClaimedJob, context: JobContext) -> None:
        """Queue every email that today's running schedules send. The scheduler starts this
        each morning; a day it missed is not caught up, because the next run sends the next
        period and a report for any period can be downloaded at any time."""
        scheduled = job.payload.get("scheduled_for")
        fired = datetime.fromisoformat(scheduled) if isinstance(scheduled, str) else None
        today = business_date(fired or self._clock.now())

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            now = self._clock.now()
            queued = 0
            for schedule in await schedules.active(session):
                period = due(schedule.report_key, schedule.frequency, today)
                if period is None:
                    continue
                for staff_id in schedule.recipients:
                    row = (
                        await session.execute(
                            _QUEUE_SEND,
                            {
                                "id": push_id(),
                                "schedule_id": schedule.id,
                                "staff_id": staff_id,
                                "date_from": period[0],
                                "date_to": period[1],
                                "now": now,
                            },
                        )
                    ).first()
                    if row is None:
                        continue
                    await enqueue(
                        session,
                        JobRequest(
                            kind=SEND_KIND,
                            idempotency_key=send_key(row.id),
                            payload={"send_id": row.id},
                            queue=QUEUE,
                            max_age=SEND_MAX_AGE,
                        ),
                        now=now,
                    )
                    queued += 1
            return {"queued": queued}

        result = await context.apply(effect)
        logger.info("report_sends_queued", day=today.isoformat(), count=result.get("queued"))

    async def send(self, job: ClaimedJob, context: JobContext) -> None:
        """Email one recipient that their report is ready, with a link to download it."""
        send_id = job.payload.get("send_id")
        if not isinstance(send_id, str):
            raise PermanentJobError(code="send_id_missing")
        async with self._database.actor_transaction(SYSTEM) as session:
            row = (await session.execute(_SEND, {"id": send_id})).first()
            if row is None:
                raise PermanentJobError(code="report_send_missing")
            schedule = await schedules.get_schedule(session, row.schedule_id)
            contact = (await identity.staff_contacts(session, [row.staff_id])).get(row.staff_id)
            skip: str | None = None
            if schedule.status != "active":
                skip = "schedule_stopped"
            elif contact is None or not contact.active:
                skip = "recipient_not_active"
            elif not may_download(REPORTS[schedule.report_key], permissions_for(contact.role)):
                skip = "recipient_may_not_download"
            elif await notifications.is_suppressed(session, contact.email):
                skip = "suppressed"

        if row.status != "queued":
            # Finished by an earlier attempt whose result was saved.
            await context.apply(_nothing)
            return
        if skip is not None or contact is None:
            await context.apply(self._finish(send_id, "skipped", skip))
            logger.info("report_send_skipped", send_id=send_id, error_code=skip)
            return
        rendered = ready_email(
            first_name=contact.name.split(" ")[0] or contact.name,
            key=schedule.report_key,
            frequency=schedule.frequency,
            date_from=row.date_from,
            date_to=row.date_to,
            currency=schedule.shown_currency,
            console=self._console,
            links=self._links,
        )
        try:
            await self._email.send(
                EmailMessage(
                    to=contact.email,
                    subject=rendered.subject,
                    html=rendered.html,
                    text=rendered.text,
                    idempotency_key=send_key(send_id),
                    tags={"template": "report_ready"},
                )
            )
        except EmailDeferred as error:
            raise RetryableJobError(error.safe_message, code=error.code) from None
        except (InvalidEmail, EmailRejected) as error:
            code = getattr(error, "code", "email_invalid")
            await context.apply(self._finish(send_id, "failed", code))
            logger.warning("report_send_refused", send_id=send_id, error_code=code)
            return
        await context.apply(self._finish(send_id, "sent", None))
        logger.info("report_sent", send_id=send_id, report=schedule.report_key)

    def _finish(
        self, send_id: str, status: str, failure_code: str | None
    ) -> Callable[[AsyncSession], Awaitable[Mapping[str, object]]]:
        async def effect(session: AsyncSession) -> Mapping[str, object]:
            await session.execute(
                _FINISH,
                {
                    "id": send_id,
                    "status": status,
                    "failure_code": failure_code,
                    "now": self._clock.now(),
                },
            )
            return {"status": status}

        return effect


async def _nothing(session: AsyncSession) -> Mapping[str, object]:
    return {}
