"""The worker's side of documents: build each statement or receipt once, email it, ask for
a receipt when a payment is confirmed, and start the monthly run.

Building reads the ledger, draws the PDF and stores it in the single fenced transaction that
also completes the job, so a job run twice builds nothing the second time. That transaction
also queues the document's email. Only the email job calls a provider, Resend, and it does so
with no transaction open. Payloads and logs carry document IDs and codes only, never a name,
an address or a figure.
"""

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.email import (
    Attachment,
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
from app.modules.documents import building, pdf, service
from app.modules.documents.domain.emails import Rendered, receipt_email, statement_email
from app.modules.documents.domain.statements import (
    Period,
    StatementDoesNotAddUp,
    StatementKind,
    month_end,
    not_final_from,
    previous_month,
)
from app.modules.documents.events import RECEIPT_KIND
from app.modules.documents.service import DocumentFile, DocumentKind, DocumentStatus, DocumentView
from app.modules.identity import service as identity
from app.modules.ledger import closing
from app.modules.ledger import statements as ledger
from app.modules.notifications import contract as notifications
from app.modules.payments import service as payments
from app.shared.actors import SYSTEM
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


_QUEUE_EMAIL = text(
    "INSERT INTO document_emails (document_id, client_id, queued_at) "
    "VALUES (:id, :client_id, :now) ON CONFLICT (document_id) DO NOTHING"
)
_EMAIL_STATUS = text("SELECT status FROM document_emails WHERE document_id = :id")
_EMAIL_DONE = text(
    "UPDATE document_emails SET status = :status, failure_code = :failure_code, "
    "provider = :provider, provider_message_id = :message_id, finished_at = :now "
    "WHERE document_id = :id AND status = 'queued'"
)
# Resend drops a repeat of a key for 24 hours, so a send is retried only within that time.
EMAIL_MAX_AGE = timedelta(hours=20)


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
        self,
        database: Database,
        clock: Clock,
        *,
        email: EmailTransport | None = None,
        closed_through: ClosedThrough | None = None,
    ) -> None:
        self._database = database
        self._clock = clock
        self._email = email or DisabledTransport()
        # The last day the ledger is closed through. Tests pass their own, because closing a
        # day is one fact for the whole shared test database and is never undone.
        self._closed_through = closed_through or closing.closed_through

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            service.BUILD_KIND: self.build,
            service.MONTHLY_KIND: self.monthly_run,
            RECEIPT_KIND: self.receipt_from_deposit,
            # Sending reaches Resend. Its idempotency key makes a lost answer safe to repeat,
            # and a crash mid-send stops the job instead of sending blind.
            service.EMAIL_KIND: external_effect(self.send_email),
        }

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
            if status is DocumentStatus.READY:
                await _queue_email(session, view, self._clock.now())
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

    async def receipt_from_deposit(self, job: ClaimedJob, context: JobContext) -> None:
        """A payment was confirmed: ask for its receipt, which is built and emailed next."""
        data = job.payload.get("data")
        data = data if isinstance(data, Mapping) else {}
        deposit_id = data.get("deposit_id")
        if not isinstance(deposit_id, str):
            raise PermanentJobError(code="deposit_id_missing")

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            deposit = await payments.get_deposit(session, deposit_id)
            view = await service.ask(
                session,
                client_id=deposit.client_id,
                kind=DocumentKind.RECEIPT,
                key=deposit_id,
                source_id=deposit_id,
                now=self._clock.now(),
            )
            return {"document_id": view.id}

        result = await context.apply(effect)
        logger.info("receipt_asked", document_id=result.get("document_id"))

    async def send_email(self, job: ClaimedJob, context: JobContext) -> None:
        """Email a ready document to its client, with the PDF attached.

        The address is looked up now, from identity. A client with none, or who has closed
        their account, or has not claimed an account carried over from Finvest 1.0, is recorded
        as `skipped` with `no_email`, and an address on the
        notifications suppression list as `skipped` with `suppressed`.
        Nothing is sent while a transaction is open.
        """
        document_id = job.payload.get("document_id")
        if not isinstance(document_id, str):
            raise PermanentJobError(code="document_id_missing")
        async with self._database.actor_transaction(SYSTEM) as session:
            status = (await session.execute(_EMAIL_STATUS, {"id": document_id})).scalar()
            view = await service.get_document(session, document_id)
            file = await service.document_file(session, document_id)
            if status is None or view is None or file is None:
                raise PermanentJobError(code="document_email_missing")
            rendered, address, skip = None, None, "no_email"
            if status == "queued":
                contact = await identity.client_contact(session, view.client_id)
                # Nothing verifies an address yet and the PDF has no password, so an account
                # carried over from Finvest 1.0 gets no statement until its owner claims it.
                address = contact.email if contact is not None and contact.claimed else None
                if address is not None and await notifications.is_suppressed(session, address):
                    # The address bounced for good or reported our mail as spam. Sending a
                    # PDF every month would harm the sending domain, and a complaint means
                    # the person asked us to stop.
                    address, skip = None, "suppressed"
                rendered = await _render(session, view, file) if address else None

        if status != "queued":
            # Finished by an earlier attempt whose result was saved.
            await context.apply(_nothing)
            return
        if address is None or rendered is None:
            await context.apply(self._finish(document_id, "skipped", failure_code=skip))
            logger.info("document_email_skipped", document_id=document_id, error_code=skip)
            return
        try:
            message = EmailMessage(
                to=address,
                subject=rendered.subject,
                html=rendered.html,
                text=rendered.text,
                idempotency_key=f"document:{document_id}",
                tags={"template": view.kind.value},
                attachments=(Attachment(file.file_name, file.content),),
            )
            sent = await self._email.send(message)
        except EmailDeferred as error:
            raise RetryableJobError(error.safe_message, code=error.code) from None
        except (InvalidEmail, EmailRejected) as error:
            code = getattr(error, "code", "email_invalid")
            await context.apply(self._finish(document_id, "failed", failure_code=code))
            logger.warning("document_email_refused", document_id=document_id, error_code=code)
            return
        await context.apply(
            self._finish(
                document_id,
                "sent",
                provider=sent.provider,
                message_id=sent.provider_message_id,
            )
        )
        logger.info("document_emailed", document_id=document_id, kind=view.kind.value)

    def _finish(
        self,
        document_id: str,
        status: str,
        *,
        failure_code: str | None = None,
        provider: str | None = None,
        message_id: str | None = None,
    ) -> Callable[[AsyncSession], Awaitable[Mapping[str, object]]]:
        async def effect(session: AsyncSession) -> Mapping[str, object]:
            await session.execute(
                _EMAIL_DONE,
                {
                    "id": document_id,
                    "status": status,
                    "failure_code": failure_code,
                    "provider": provider,
                    "message_id": message_id,
                    "now": self._clock.now(),
                },
            )
            return {"status": status}

        return effect


async def _queue_email(session: AsyncSession, view: DocumentView, now: datetime) -> None:
    await session.execute(_QUEUE_EMAIL, {"id": view.id, "client_id": view.client_id, "now": now})
    await enqueue(
        session,
        JobRequest(
            kind=service.EMAIL_KIND,
            idempotency_key=f"document-email:{view.id}",
            payload={"document_id": view.id},
            queue=service.QUEUE,
            max_age=EMAIL_MAX_AGE,
        ),
        now=now,
    )


async def _render(session: AsyncSession, view: DocumentView, file: DocumentFile) -> Rendered:
    holder = await building.holder(session, view.client_id)
    if view.kind is DocumentKind.RECEIPT:
        assert view.source_id is not None
        receipt, _ = await building.receipt(
            session, deposit_id=view.source_id, reference=receipt_reference(view.id)
        )
        return receipt_email(
            first_name=holder.first_name, receipt=receipt, file_name=file.file_name
        )
    assert view.period is not None
    monthly = view.kind is DocumentKind.MONTHLY_STATEMENT
    return statement_email(
        kind=StatementKind.MONTHLY if monthly else StatementKind.REQUESTED,
        first_name=holder.first_name,
        period=view.period,
        account_number=holder.account_number,
        file_name=file.file_name,
        requested_at=None if monthly else view.requested_at,
    )


async def _nothing(session: AsyncSession) -> Mapping[str, object]:
    return {}
