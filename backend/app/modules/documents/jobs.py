"""The worker's side of documents: build each statement or receipt once, and start the monthly run.

No handler here calls a provider. Each reads the ledger, draws the PDF and stores it in the
single fenced transaction that also completes the job, so a job run twice builds nothing the
second time. Payloads and logs carry document IDs and codes only, never a name or a figure.
"""

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime

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
    enqueue,
)
from app.modules.documents import building, pdf, service
from app.modules.documents.domain.statements import (
    Period,
    StatementDoesNotAddUp,
    StatementKind,
    month_end,
    not_final_from,
    previous_month,
)
from app.modules.documents.service import DocumentKind, DocumentStatus, DocumentView
from app.modules.ledger import closing
from app.modules.ledger import statements as ledger
from app.modules.notifications import contract as notifications
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError

MONTHLY_PAGE = 200
logger = structlog.get_logger()

_FINISH = text(
    "UPDATE documents SET status = :status, failure_code = :failure_code, "
    "finished_at = :now, file_name = :file_name, content = :content, sha256 = :sha256, "
    "byte_size = :byte_size, not_final_from = :not_final_from "
    "WHERE id = :id AND status = 'pending'"
)


def statement_reference(document_id: str) -> str:
    return f"ST-{document_id}"


def receipt_reference(document_id: str) -> str:
    return f"RC-{document_id}"


def file_name(view: DocumentView) -> str:
    if view.kind is DocumentKind.RECEIPT:
        return f"Finvest receipt {receipt_reference(view.id)}.pdf"
    assert view.period is not None
    return f"Finvest statement {view.period.label}.pdf"


# How many days into a month the run waits quietly for the ledger to close the month before.
# After that each day's run logs an error, so staff see that statements are held up.
WAIT_QUIETLY_DAYS = 5

ClosedThrough = Callable[[AsyncSession], Awaitable[date | None]]


class DocumentJobs:
    def __init__(
        self, database: Database, clock: Clock, *, closed_through: ClosedThrough | None = None
    ) -> None:
        self._database = database
        self._clock = clock
        # The last day the ledger is closed through. Tests pass their own, because closing a
        # day is one fact for the whole shared test database and is never undone.
        self._closed_through = closed_through or closing.closed_through

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {service.BUILD_KIND: self.build, service.MONTHLY_KIND: self.monthly_run}

    async def build(self, job: ClaimedJob, context: JobContext) -> None:
        document_id = job.payload.get("document_id")
        if not isinstance(document_id, str):
            raise PermanentJobError(code="document_id_missing")

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            view = await service.get_document(session, document_id)
            if view is None:
                raise PermanentJobError(code="document_missing")
            if view.status is not DocumentStatus.PENDING:
                return {"status": view.status.value}
            status, failure, content, open_from = await self._draw(session, view)
            await session.execute(
                _FINISH,
                {
                    "id": view.id,
                    "status": status.value,
                    "failure_code": failure,
                    "now": self._clock.now(),
                    "file_name": file_name(view) if content else None,
                    "content": content,
                    "sha256": hashlib.sha256(content).digest() if content else None,
                    "byte_size": len(content) if content else None,
                    "not_final_from": open_from,
                },
            )
            if status is DocumentStatus.READY and view.period is not None:
                await notifications.notify(
                    session,
                    event_name="statement_ready",
                    client_id=view.client_id,
                    idempotency_key=view.id,
                    variables={"period": view.period.label},
                    link=notifications.Link("statement", view.id),
                    now=self._clock.now(),
                )
            return {"status": status.value}

        result = await context.apply(effect)
        logger.info("document_built", document_id=document_id, status=result.get("status"))

    async def _draw(
        self, session: AsyncSession, view: DocumentView
    ) -> tuple[DocumentStatus, str | None, bytes | None, date | None]:
        open_from: date | None = None
        if view.period is not None:
            open_from = not_final_from(view.period, await self._closed_through(session))
            if open_from is not None and view.kind is DocumentKind.MONTHLY_STATEMENT:
                # The monthly run asks only for a closed month, so this is a guard. The row
                # stays pending and the build comes back later.
                raise RetryableJobError(code="ledger_not_closed")
        try:
            if view.kind is DocumentKind.RECEIPT:
                assert view.source_id is not None
                receipt, _ = await building.receipt(
                    session, deposit_id=view.source_id, reference=receipt_reference(view.id)
                )
                return DocumentStatus.READY, None, pdf.receipt_pdf(receipt), None
            assert view.period is not None
            drawn = await building.statement(
                session,
                client_id=view.client_id,
                kind=StatementKind.MONTHLY
                if view.kind is DocumentKind.MONTHLY_STATEMENT
                else StatementKind.REQUESTED,
                period=view.period,
                reference=statement_reference(view.id),
                issued_on=business_date(self._clock.now()),
                not_final_from=open_from,
            )
        except building.NotReady:
            return DocumentStatus.FAILED, "not_approved", None, None
        except StatementDoesNotAddUp:
            # A bug or bad data, never a client's fault. Nothing is sent, and staff see it.
            logger.error(
                "statement_does_not_add_up", document_id=view.id, error_code="does_not_add_up"
            )
            return DocumentStatus.FAILED, "does_not_add_up", None, None
        except ApplicationError as error:
            return DocumentStatus.FAILED, f"source_{error.code}", None, None
        if view.kind is DocumentKind.MONTHLY_STATEMENT and drawn.is_empty:
            return DocumentStatus.EMPTY, None, None, None
        return DocumentStatus.READY, None, pdf.statement_pdf(drawn), open_from

    async def monthly_run(self, job: ClaimedJob, context: JobContext) -> None:
        """Ask for each client's statement for a month once the ledger has closed that month.

        The scheduler starts this every morning. A statement is a record the client keeps, so
        a month is asked for only when the ledger is closed through its last day and nothing
        dated in it can still arrive: a late interest posting, a payment that reconciliation
        confirms, a correction. Until then the morning's run asks for nothing, and after
        `WAIT_QUIETLY_DAYS` it logs an error each day so staff see statements are held up.

        Asking happens a page at a time. Each page queues the next, so a run of any size
        finishes, and every client's statement is asked for exactly once.
        """
        data = job.payload.get("data")
        data = data if isinstance(data, Mapping) else {}
        month = data.get("month")
        if isinstance(month, str):
            await self._ask_page(job, context, month, data.get("after", ""))
            return
        scheduled = job.payload.get("scheduled_for")
        fired = datetime.fromisoformat(scheduled) if isinstance(scheduled, str) else None
        today = business_date(fired or self._clock.now())
        last = previous_month(today)
        # The month before too, in case the ledger closed both at once.
        months = (previous_month(last.start), last)

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            closed = await self._closed_through(session)
            started: list[str] = []
            for period in months:
                label = f"{period.start:%Y-%m}"
                if not_final_from(period, closed) is not None:
                    continue
                if await service.monthly_asked(session, label):
                    continue
                await enqueue(
                    session,
                    JobRequest(
                        kind=service.MONTHLY_KIND,
                        idempotency_key=f"documents.monthly:{label}:start",
                        payload={"data": {"month": label, "after": ""}},
                        queue=service.QUEUE,
                    ),
                    now=self._clock.now(),
                )
                started.append(label)
            return {"started": started, "waiting": not_final_from(last, closed) is not None}

        result = await context.apply(effect)
        label = f"{last.start:%Y-%m}"
        if result.get("waiting"):
            if today.day > WAIT_QUIETLY_DAYS:
                logger.error(
                    "monthly_statements_waiting", month=label, error_code="ledger_not_closed"
                )
            else:
                logger.info("monthly_statements_waiting", month=label)
        started = result.get("started")
        for asked in started if isinstance(started, list) else []:
            logger.info("monthly_statements_started", month=asked)

    async def _ask_page(
        self, job: ClaimedJob, context: JobContext, month: str, after: object
    ) -> None:
        if not isinstance(after, str):
            raise PermanentJobError(code="monthly_run_bad_cursor")
        try:
            start = datetime.strptime(month, "%Y-%m").date()
        except ValueError:
            raise PermanentJobError(code="monthly_run_bad_month") from None
        period = Period(start, month_end(start))
        label = f"{period.start:%Y-%m}"

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            if not_final_from(period, await self._closed_through(session)) is not None:
                # Pages are queued only for a closed month, and a closed day never reopens.
                raise PermanentJobError(code="ledger_not_closed")
            now = self._clock.now()
            clients = await ledger.clients_with_accounts(session, after=after, limit=MONTHLY_PAGE)
            for client_id in clients:
                await service.ask(
                    session,
                    client_id=client_id,
                    kind=DocumentKind.MONTHLY_STATEMENT,
                    key=label,
                    period=period,
                    now=now,
                )
            if len(clients) == MONTHLY_PAGE:
                await enqueue(
                    session,
                    JobRequest(
                        kind=service.MONTHLY_KIND,
                        idempotency_key=f"documents.monthly:{label}:{clients[-1]}",
                        payload={"data": {"month": label, "after": clients[-1]}},
                        queue=service.QUEUE,
                    ),
                    now=now,
                )
            return {"month": label, "asked": len(clients)}

        result = await context.apply(effect)
        logger.info("monthly_statements_asked", month=label, count=result.get("asked"))
