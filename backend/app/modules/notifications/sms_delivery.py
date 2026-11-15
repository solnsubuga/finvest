"""Africa's Talking's delivery reports: recorded once by the API, applied by the worker.

The API checks the callback token, saves the report under its message id and status and
queues a job, all in one short transaction, and answers. The same report sent again finds the
row and queues nothing. The worker then records the delivery on the message it is about. A
report is a hint about a message already sent: it moves no money and sends nothing.
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
from app.modules.notifications.domain.sms_reports import SmsReport, moves_forward
from app.shared.clock import Clock

APPLY_REPORT = "notifications.sms_report"
PROVIDER = "africastalking"
# A report can arrive before the worker has saved Africa's Talking's id for the message. A
# report that matches no message is tried again for this long, then kept as unmatched.
MATCH_WINDOW = timedelta(minutes=10)
# Enough attempts, at the worker's default backoff, to outlast the window above.
REPORT_ATTEMPTS = 12
REPORT_MAX_AGE = timedelta(days=2)

logger = structlog.get_logger()

_INSERT_REPORT = text(
    "INSERT INTO notification_sms_reports (provider, provider_message_id, status, delivery, "
    "reason, received_at) VALUES (:provider, :message_id, :status, :delivery, :reason, :now) "
    "ON CONFLICT DO NOTHING"
)


def _job_key(message_id: str, status: str) -> str:
    return f"sms-report:{PROVIDER}:{message_id}:{status}"


async def record_report(session: AsyncSession, report: SmsReport, now: datetime) -> bool:
    """Save a report and queue the job that applies it. False for one already seen."""
    saved: CursorResult[tuple[()]] = await session.execute(  # type: ignore[assignment]
        _INSERT_REPORT,
        {
            "provider": PROVIDER,
            "message_id": report.provider_message_id,
            "status": report.status,
            "delivery": report.delivery,
            "reason": report.reason,
            "now": now,
        },
    )
    if saved.rowcount != 1:
        return False
    await enqueue(
        session,
        JobRequest(
            kind=APPLY_REPORT,
            idempotency_key=_job_key(report.provider_message_id, report.status),
            payload={"message_id": report.provider_message_id, "status": report.status},
            queue=service.MESSAGE_QUEUE,
            max_age=REPORT_MAX_AGE,
            max_attempts=REPORT_ATTEMPTS,
        ),
        now=now,
    )
    return True


class SmsReportJobs:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {APPLY_REPORT: self.apply_report}

    async def apply_report(self, job: ClaimedJob, context: JobContext) -> None:
        message_id = str(job.payload["message_id"])
        status = str(job.payload["status"])
        now = self.clock.now()
        async with self.database.transaction() as session:
            report = (
                await session.execute(
                    text(
                        "SELECT delivery, reason, received_at, processed_at "
                        "FROM notification_sms_reports WHERE provider = :provider "
                        "AND provider_message_id = :message_id AND status = :status"
                    ),
                    {"provider": PROVIDER, "message_id": message_id, "status": status},
                )
            ).one_or_none()
            if report is None:
                raise PermanentJobError("The SMS report does not exist", code="report_missing")
            message = (
                await session.execute(
                    text(
                        "SELECT id, delivery FROM notification_messages WHERE channel = 'sms' "
                        "AND provider = :provider AND provider_message_id = :message_id"
                    ),
                    {"provider": PROVIDER, "message_id": message_id},
                )
            ).one_or_none()
        if report.processed_at is not None:
            await context.apply(_nothing)
            return
        if message is None and now - report.received_at < MATCH_WINDOW:
            raise RetryableJobError("The text is not recorded yet", code="sms_not_recorded")

        async def apply(session: AsyncSession) -> dict[str, object]:
            outcome = "unmatched"
            if message is not None:
                outcome = "unchanged"
                if moves_forward(message.delivery, report.delivery):
                    # The guard repeats the check in SQL, so a later report applied in between
                    # is never overwritten by an earlier one.
                    changed = await session.execute(
                        text(
                            "UPDATE notification_messages SET delivery = :delivery, "
                            "delivery_at = :at, delivery_reason = :reason WHERE id = :id "
                            "AND delivery IS NOT DISTINCT FROM :current"
                        ),
                        {
                            "delivery": report.delivery,
                            "at": report.received_at,
                            "reason": report.reason,
                            "id": message.id,
                            "current": message.delivery,
                        },
                    )
                    if changed.rowcount != 1:  # type: ignore[attr-defined]
                        raise RetryableJobError("The message changed", code="delivery_changed")
                    outcome = "applied"
            await session.execute(
                text(
                    "UPDATE notification_sms_reports SET processed_at = :now, outcome = :outcome "
                    "WHERE provider = :provider AND provider_message_id = :message_id "
                    "AND status = :status AND processed_at IS NULL"
                ),
                {
                    "now": now,
                    "outcome": outcome,
                    "provider": PROVIDER,
                    "message_id": message_id,
                    "status": status,
                },
            )
            return {"outcome": outcome}

        await context.apply(apply)
        logger.info(
            "sms_report_applied",
            delivery=report.delivery,
            reason=report.reason,
            message_id=message.id if message else None,
        )


async def _nothing(session: AsyncSession) -> dict[str, object]:
    return {}
