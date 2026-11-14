"""Statements against real PostgreSQL: built from the ledger, asked for once, fenced to their
client, finished once, and the monthly run."""

import asyncio
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import replace
from datetime import date, timedelta
from io import BytesIO
from types import EllipsisType
from urllib.parse import quote

import httpx
import pytest
from pypdf import PdfReader
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.email import EmailDeferred, EmailMessage, EmailRejected, SendResult
from app.infrastructure.jobs import JobRequest, Worker, WorkerConfig, enqueue
from app.infrastructure.sealing import Sealer
from app.modules.documents import building, pdf, service
from app.modules.documents.domain.emails import receipt_email
from app.modules.documents.domain.statements import (
    ActivityKind,
    Period,
    Statement,
    StatementKind,
    month_end,
    previous_month,
)
from app.modules.documents.events import RECEIPT_KIND
from app.modules.documents.jobs import DocumentJobs
from app.modules.documents.service import DocumentKind, DocumentStatus
from app.modules.identity import service as identity_service
from app.modules.identity.email_checks import EmailCheckService
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import ClientContact, IdentityService
from app.modules.investments import accrual
from app.modules.investments import service as investments
from app.modules.investments.holdings import AWAITING_START
from app.modules.kyc import service as kyc
from app.modules.kyc.providers import ManualProvider
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.notifications.domain.email_events import address_hash
from app.modules.payments import bank_transfers as bank
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Money
from app.shared.push_ids import push_id
from tests.integration import test_bank_transfers as bank_tests
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.investments import (
    PRESENT,
    approved_client,
    approved_destination,
    at,
    request,
    started,
    today,
)
from tests.support.kyc import SEALER, ready_draft
from tests.support.ledger import client as new_client
from tests.support.ledger import key, ugx
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

# The database refuses an investment that is not created at the current time, so these tests
# start from today and set every other date from it. Each is read when a test asks, not when
# this file is imported, so a run that crosses midnight in Kampala still agrees with the
# database about which day it is.


def asked_period() -> Period:
    """A statement a client asks for, around the investment's first days."""
    return Period(today() - timedelta(days=3), today() + timedelta(days=20))


def issued_on() -> date:
    return asked_period().end + timedelta(days=1)


def later_clock() -> FakeClock:
    return at(issued_on())


def paid_month() -> Period:
    """The calendar month the investment was paid in."""
    return Period(today().replace(day=1), month_end(today()))


def month_before() -> Period:
    return previous_month(today())


def paid_label() -> str:
    return f"{paid_month().start:%Y-%m}"


def before_label() -> str:
    return f"{month_before().start:%Y-%m}"


@pytest.fixture(autouse=True)
def own_queue(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    queue = "doc" + "".join(c for c in push_id().lower() if c.isalnum())
    monkeypatch.setattr(service, "QUEUE", queue)
    yield queue


async def _post(
    database: Database,
    *,
    entry_type: str,
    debit_spec: AccountSpec,
    credit_spec: AccountSpec,
    amount: Money,
    on: date,
    source_id: str | None = None,
) -> None:
    clock = FakeClock()
    async with database.actor_transaction(SYSTEM) as session:
        debit_account = await ledger.open_account(session, debit_spec, clock)
        credit_account = await ledger.open_account(session, credit_spec, clock)
        await ledger.post_entry(
            session,
            EntryDraft(
                entry_type=entry_type,
                effective_date=on,
                lines=(debit(debit_account.id, amount), credit(credit_account.id, amount)),
                idempotency_key=key(),
                source_type="payment" if source_id else None,
                source_id=source_id,
            ),
            clock,
        )


async def invested(
    app_database: Database,
    worker_database: Database,
    *,
    interest: tuple[int, ...] = (100, 100),
) -> tuple[Actor, investments.Investment]:
    """An approved client's running 91 day bill, paid today, with daily interest after."""
    started_on = today()
    client = await approved_client(app_database, at(started_on))
    investment = await request(app_database, client, PRESENT)
    amount = investment.amount
    awaiting = AccountSpec(
        AWAITING_START,
        AccountKind.LIABILITY,
        amount.currency,
        "Paid in, awaiting start",
        owner_client_id=client.id,
        reference=investment.id,
    )
    await _post(
        worker_database,
        entry_type="payment.deposit_confirmed",
        debit_spec=AccountSpec(
            "test.collections", AccountKind.ASSET, amount.currency, "Test collections"
        ),
        credit_spec=awaiting,
        amount=amount,
        on=started_on,
        source_id=push_id(),
    )
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(
            session, investment.id, confirmed_on=started_on, clock=at(started_on)
        )
    for day, earned in enumerate(interest, start=1):
        await _post(
            worker_database,
            entry_type="investment.interest_accrued",
            debit_spec=AccountSpec(
                "test.interest_expense", AccountKind.EXPENSE, amount.currency, "Test interest"
            ),
            credit_spec=AccountSpec(
                "client.interest_accrued",
                AccountKind.LIABILITY,
                amount.currency,
                "Interest earned",
                owner_client_id=client.id,
                reference=investment.id,
            ),
            amount=ugx(earned),
            on=started_on + timedelta(days=day),
        )
    return client, investment


# Closing a day is one fact for the whole shared test database and is never undone, so these
# tests tell the jobs which day the ledger is closed through instead of closing real days.
def all_closed() -> date:
    return issued_on() + timedelta(days=60)


def closed(day: date | None) -> Callable[[AsyncSession], Awaitable[date | None]]:
    async def closed_through(session: AsyncSession) -> date | None:
        return day

    return closed_through


class FakeEmail:
    """Resend, as a test sees it: keeps what it was sent, or fails as told."""

    name = "fake"

    def __init__(self, failure: Exception | None = None) -> None:
        self.sent: list[EmailMessage] = []
        self.failure = failure

    async def send(self, message: EmailMessage) -> SendResult:
        if self.failure is not None:
            raise self.failure
        self.sent.append(message)
        return SendResult(provider=self.name, provider_message_id=f"fake-{len(self.sent)}")


async def run_jobs(
    worker_database: Database,
    clock: FakeClock | None = None,
    through: date | EllipsisType | None = ...,
    email: FakeEmail | None = None,
    sealer: Sealer | None = SEALER,
) -> int:
    """Runs the documents jobs. The clock defaults to later_clock() and the ledger to closed
    through all_closed(); pass through=None for a ledger closed through no day."""
    clock = clock or later_clock()
    if through is ...:
        through = all_closed()
    jobs = DocumentJobs(
        worker_database,
        clock,
        email=email or FakeEmail(),
        closed_through=closed(through),
        sealer=sealer,
    )
    runner = Worker(
        worker_database,
        jobs.handlers(),
        WorkerConfig(queue=service.QUEUE),
        clock,
    )
    done = 0
    while await runner.run_once():
        done += 1
    return done


async def ask(
    app_database: Database,
    client: Actor,
    period: Period | None = None,
    idempotency_key: str = "",
) -> service.DocumentView:
    period = period or asked_period()
    async with app_database.actor_transaction(client) as session:
        return await service.request_statement(
            session,
            client,
            date_from=period.start,
            date_to=period.end,
            idempotency_key=idempotency_key or key(),
            clock=later_clock(),
        )


# Building from the ledger ------------------------------------------------------------------------


async def test_a_statement_is_read_from_the_clients_own_ledger_accounts(
    app_database: Database, worker_database: Database
) -> None:
    client, investment = await invested(app_database, worker_database)
    assert client.id is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        drawn = await building.statement(
            session,
            client_id=client.id,
            kind=StatementKind.REQUESTED,
            period=asked_period(),
            reference="ST-1",
            issued_on=issued_on(),
        )
    (summary,) = drawn.summaries
    assert (summary.opening, summary.paid_in, summary.earned, summary.paid_out) == (
        ugx(0),
        ugx(1_000_000),
        ugx(200),
        ugx(0),
    )
    assert summary.closing == ugx(1_000_200)
    # The start moved money between the client's own accounts, so it is not a line.
    paid_in, *earned = drawn.activity
    assert (paid_in.kind, paid_in.amount, paid_in.on) == (
        ActivityKind.PAID_IN,
        ugx(1_000_000),
        today(),
    )
    assert paid_in.description == "Payment received"
    # Interest is folded into one line a calendar month, so two if the days span a month end.
    assert sum((line.amount for line in earned), ugx(0)) == ugx(200)
    assert {(line.kind, line.description) for line in earned} == {
        (ActivityKind.EARNED, f"Interest, {investment.product_name}")
    }
    (holding,) = drawn.holdings
    assert (holding.reference, holding.status, holding.value_at_end, holding.earned_in_period) == (
        investment.id,
        "Running",
        ugx(1_000_200),
        ugx(200),
    )
    assert len(drawn.account_number) == 10


async def test_a_period_holding_an_early_withdrawal_adds_up_and_says_where_the_money_is(
    app_database: Database, worker_database: Database
) -> None:
    # The withdrawal moves principal and interest from the holding to the client's payout
    # account, which is still theirs and still under the investment, so nothing is paid out
    # until the payout is sent, and the statement still adds up (or it would not be drawn).
    day = today()
    investment = await started(app_database, worker_database, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await accrual.accrue_investment(
            session, investment.id, day + timedelta(days=1), at(day + timedelta(days=1))
        )
    client = Actor("client", investment.client_id)
    destination_id = await approved_destination(app_database, client, PRESENT)
    withdrawn_at = at(day + timedelta(days=2))
    async with app_database.actor_transaction(client) as session:
        await investments.request_withdrawal(
            session,
            client,
            investment.id,
            destination_id=destination_id,
            idempotency_key=key(),
            clock=withdrawn_at,
        )
    async with worker_database.actor_transaction(SYSTEM) as session:
        withdrawn = await investments.withdraw(session, investment.id, withdrawn_at)
    owed = withdrawn.amount + withdrawn.accrued_net

    async def drawn(period: Period) -> Statement:
        async with worker_database.actor_transaction(SYSTEM) as session:
            return await building.statement(
                session,
                client_id=investment.client_id,
                kind=StatementKind.REQUESTED,
                period=period,
                reference="ST-1",
                issued_on=day + timedelta(days=10),
            )

    after = await drawn(Period(day - timedelta(days=1), day + timedelta(days=5)))
    (summary,) = after.summaries
    assert summary.paid_out == ugx(0)
    assert summary.earned > ugx(0)
    assert summary.closing == owed
    (holding,) = after.holdings
    assert (holding.status, holding.value_at_end) == (
        "Withdrawn early, waiting to be paid out",
        owed,
    )
    # A period that ended before the withdrawal shows the investment still running.
    before = await drawn(Period(day - timedelta(days=1), day + timedelta(days=1)))
    (holding,) = before.holdings
    assert holding.status == "Running"


async def test_a_later_period_opens_with_what_the_last_one_closed_on(
    app_database: Database, worker_database: Database
) -> None:
    client, _ = await invested(app_database, worker_database, interest=(100, 100, 100))
    assert client.id is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        drawn = await building.statement(
            session,
            client_id=client.id,
            kind=StatementKind.REQUESTED,
            period=Period(today() + timedelta(days=2), today() + timedelta(days=3)),
            reference="ST-1",
            issued_on=issued_on(),
        )
    (summary,) = drawn.summaries
    assert (summary.opening, summary.earned, summary.closing) == (
        ugx(1_000_100),
        ugx(200),
        ugx(1_000_300),
    )


async def test_a_client_who_is_not_approved_has_no_statement(worker_database: Database) -> None:
    async with worker_database.actor_transaction(SYSTEM) as session:
        with pytest.raises(building.NotReady):
            await building.statement(
                session,
                client_id=push_id(),
                kind=StatementKind.MONTHLY,
                period=asked_period(),
                reference="ST-1",
                issued_on=issued_on(),
            )


# Asking for a statement -----------------------------------------------------------------------


async def test_a_requested_statement_is_built_stored_and_announced(
    app_database: Database, worker_database: Database
) -> None:
    client, _ = await invested(app_database, worker_database)
    asked = await ask(app_database, client)
    assert asked.status is DocumentStatus.PENDING
    # The build, then its email: this client has no address to send to.
    assert await run_jobs(worker_database) == 2
    async with app_database.actor_transaction(client) as session:
        ready = await service.client_document(session, client, asked.id)
        found = await service.client_file(session, client, asked.id)
        told = (
            await session.execute(
                text(
                    "SELECT title, link_kind, link_id FROM notifications "
                    "WHERE client_id = :client AND event = 'statement_ready'"
                ),
                {"client": client.id},
            )
        ).all()
    assert ready.status is DocumentStatus.READY
    assert ready.file_name == f"Finvest statement {asked_period().label}.pdf"
    assert found.content.startswith(b"%PDF-")
    assert ready.byte_size == len(found.content)
    assert ready.not_final_from is None
    assert [(row.title, row.link_kind, row.link_id) for row in told] == [
        (f"{asked_period().label} statement is ready", "statement", asked.id)
    ]
    # Run again: nothing more to do, and nothing changes.
    assert await run_jobs(worker_database) == 0


async def test_the_same_key_asks_once_and_a_different_period_under_it_is_refused(
    app_database: Database,
) -> None:
    client = await approved_client(app_database, later_clock())
    first = await ask(app_database, client, idempotency_key="statement-key-1")
    again = await ask(app_database, client, idempotency_key="statement-key-1")
    assert again.id == first.id
    with pytest.raises(ApplicationError) as reused:
        await ask(
            app_database,
            client,
            Period(date(2026, 10, 1), date(2026, 10, 31)),
            idempotency_key="statement-key-1",
        )
    assert reused.value.code == "idempotency_key_reused"


async def test_two_requests_racing_with_one_key_make_one_statement(
    app_database: Database,
) -> None:
    client = await approved_client(app_database, later_clock())
    views = await asyncio.gather(
        ask(app_database, client, idempotency_key="statement-race"),
        ask(app_database, client, idempotency_key="statement-race"),
    )
    assert views[0].id == views[1].id
    async with app_database.actor_transaction(client) as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM documents WHERE client_id = :client"),
                {"client": client.id},
            )
        ).scalar_one()
        jobs = (
            await session.execute(
                text("SELECT count(*) FROM jobs WHERE idempotency_key = :key"),
                {"key": f"document:{views[0].id}"},
            )
        ).scalar_one()
    assert (count, jobs) == (1, 1)


@pytest.mark.parametrize(
    ("start", "end", "field", "code"),
    # Days from today. The usual period runs from -3 to 20, and the later clock is on day 21.
    [
        (20, -3, "date_from", "period_backwards"),
        (-3, 22, "date_to", "period_in_future"),
        (21 - 400, 21, "date_from", "period_too_long"),
    ],
)
async def test_a_period_that_cannot_be_a_statement_is_refused_on_its_field(
    app_database: Database, start: int, end: int, field: str, code: str
) -> None:
    client = await approved_client(app_database, later_clock())
    period = Period(today() + timedelta(days=start), today() + timedelta(days=end))
    with pytest.raises(ApplicationError) as refused:
        await ask(app_database, client, period)
    assert refused.value.code == "validation_failed"
    assert [(str(p.field_path[0]), p.code) for p in refused.value.fields] == [(field, code)]


async def test_a_client_may_ask_for_ten_statements_a_day(app_database: Database) -> None:
    client = await approved_client(app_database, later_clock())
    for _ in range(service.REQUESTS_PER_DAY):
        await ask(app_database, client)
    with pytest.raises(ApplicationError) as limited:
        await ask(app_database, client)
    assert limited.value.code == "rate_limited"


async def test_a_client_sees_and_downloads_only_their_own_documents(
    app_database: Database, worker_database: Database
) -> None:
    client, _ = await invested(app_database, worker_database)
    asked = await ask(app_database, client)
    await run_jobs(worker_database)
    other = new_client()
    async with app_database.actor_transaction(other) as session:
        assert await service.client_documents(session, other) == []
        for read in (service.client_document, service.client_file):
            with pytest.raises(ApplicationError) as hidden:
                await read(session, other, asked.id)
            assert hidden.value.code == "not_found"
    async with app_database.actor_transaction(client) as session:
        assert [view.id for view in await service.client_documents(session, client)] == [asked.id]


# What the database enforces --------------------------------------------------------------------


async def test_a_client_cannot_write_a_monthly_statement_or_a_receipt(
    app_database: Database,
) -> None:
    client = new_client()
    for kind, start, end, source in (
        ("monthly_statement", date(2026, 11, 1), date(2026, 11, 30), None),
        ("receipt", None, None, "some-deposit"),
    ):
        with pytest.raises(DBAPIError, match="row-level security"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(
                    text(
                        "INSERT INTO documents (id, client_id, kind, idempotency_key, "
                        "period_start, period_end, source_id, requested_at) VALUES "
                        "(:id, :client, :kind, 'k', :start, :end, :source, now())"
                    ),
                    {
                        "id": push_id(),
                        "client": client.id,
                        "kind": kind,
                        "start": start,
                        "end": end,
                        "source": source,
                    },
                )


async def test_a_finished_document_never_changes_and_none_is_deleted(
    app_database: Database, worker_database: Database
) -> None:
    client, _ = await invested(app_database, worker_database)
    asked = await ask(app_database, client)
    await run_jobs(worker_database)
    with pytest.raises(DBAPIError, match="never changes"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "UPDATE documents SET content = 'x', sha256 = sha256('x'), byte_size = 1 "
                    "WHERE id = :id"
                ),
                {"id": asked.id},
            )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(text("DELETE FROM documents WHERE id = :id"), {"id": asked.id})
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text("UPDATE documents SET status = 'failed' WHERE id = :id"), {"id": asked.id}
            )


async def test_a_ready_document_must_hold_the_file_it_describes(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, later_clock())
    asked = await ask(app_database, client)
    with pytest.raises(DBAPIError, match="ck_documents_file"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "UPDATE documents SET status = 'ready', finished_at = now(), "
                    "file_name = 'a.pdf', content = 'abc', sha256 = sha256('abd'), byte_size = 3 "
                    "WHERE id = :id"
                ),
                {"id": asked.id},
            )


# The monthly run -----------------------------------------------------------------------------


async def test_the_monthly_run_asks_once_for_each_client_and_skips_an_empty_month(
    app_database: Database, worker_database: Database, own_queue: str
) -> None:
    client, _ = await invested(app_database, worker_database)
    first = at(paid_month().end + timedelta(days=1), hour=3)
    scheduled = first.now().isoformat()
    runs: list[tuple[str, dict[str, object]]] = [
        ("documents.monthly_statements@" + scheduled, {"scheduled_for": scheduled, "data": {}}),
        # The same fire time again is the same job.
        ("documents.monthly_statements@" + scheduled, {"scheduled_for": scheduled, "data": {}}),
        # Each month's first page, as the morning run queues them. Other tests share the
        # database and may have started these months already, so they are queued here too.
        (f"documents.monthly:{paid_label()}:start", {"data": {"month": paid_label(), "after": ""}}),
        # A month in which this client held nothing.
        (
            f"documents.monthly:{before_label()}:start",
            {"data": {"month": before_label(), "after": ""}},
        ),
    ]
    async with worker_database.actor_transaction(SYSTEM) as session:
        for job_key, payload in runs:
            await enqueue(
                session,
                JobRequest(
                    kind=service.MONTHLY_KIND,
                    idempotency_key=job_key,
                    payload=payload,
                    queue=own_queue,
                ),
                now=first.now(),
            )
    await run_jobs(worker_database, first)
    async with app_database.actor_transaction(client) as session:
        mine = await service.client_documents(session, client)
    monthly = [view for view in mine if view.kind is DocumentKind.MONTHLY_STATEMENT]
    assert [(view.period, view.status, view.idempotency_key) for view in monthly] == [
        (paid_month(), DocumentStatus.READY, paid_label())
    ]
    # The month before had nothing in it, so it is recorded as empty and never shown or sent.
    assert await monthly_of(worker_database, client) == [
        (before_label(), "empty"),
        (paid_label(), "ready"),
    ]


async def schedule_run(worker_database: Database, queue: str, clock: FakeClock) -> None:
    """The scheduler's morning run, fired at `clock`'s time."""
    scheduled = clock.now().isoformat()
    async with worker_database.actor_transaction(SYSTEM) as session:
        await enqueue(
            session,
            JobRequest(
                kind=service.MONTHLY_KIND,
                idempotency_key="documents.monthly_statements@" + scheduled,
                payload={"scheduled_for": scheduled, "data": {}},
                queue=queue,
            ),
            now=clock.now(),
        )


async def monthly_of(worker_database: Database, client: Actor) -> list[tuple[str, str]]:
    async with worker_database.actor_transaction(SYSTEM) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT idempotency_key, status FROM documents WHERE client_id = :client "
                    "AND kind = 'monthly_statement' ORDER BY idempotency_key"
                ),
                {"client": client.id},
            )
        ).all()
    return [tuple(row) for row in rows]


def drawn_statements(monkeypatch: pytest.MonkeyPatch) -> dict[str, Statement]:
    """Every statement the build job draws, by reference."""
    drawn: dict[str, Statement] = {}
    real = pdf.statement_pdf

    def keep(statement: Statement) -> bytes:
        drawn[statement.reference] = statement
        return real(statement)

    monkeypatch.setattr(pdf, "statement_pdf", keep)
    return drawn


async def test_the_monthly_run_waits_for_the_ledger_to_close_the_month_and_includes_late_entries(
    app_database: Database,
    worker_database: Database,
    own_queue: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A month no other test uses: the run asks for a month once for every client, and the
    # tests share one database.
    march = Period(date(2031, 3, 1), date(2031, 3, 31))
    client = await approved_client(app_database, later_clock())
    reference = push_id()

    def client_account(code: str, name: str) -> AccountSpec:
        return AccountSpec(
            code,
            AccountKind.LIABILITY,
            ugx(0).currency,
            name,
            owner_client_id=client.id,
            reference=reference,
        )

    await _post(
        worker_database,
        entry_type="payment.deposit_confirmed",
        debit_spec=AccountSpec("test.collections", AccountKind.ASSET, ugx(0).currency, "Test"),
        credit_spec=client_account(AWAITING_START, "Paid in, awaiting start"),
        amount=ugx(5_000),
        on=date(2031, 3, 10),
        source_id=push_id(),
    )
    drawn = drawn_statements(monkeypatch)
    first = at(date(2031, 4, 1), hour=3)
    await schedule_run(worker_database, own_queue, first)
    # March's last day is not closed: nothing is asked for.
    await run_jobs(worker_database, first, through=date(2031, 3, 30))
    assert ("2031-03", "pending") not in await monthly_of(worker_database, client)
    assert [key for key, _ in await monthly_of(worker_database, client)] == ["2031-02"]

    # Interest for the 31st lands after the morning run, dated in March.
    await _post(
        worker_database,
        entry_type="investment.interest_accrued",
        debit_spec=AccountSpec(
            "test.interest_expense", AccountKind.EXPENSE, ugx(0).currency, "Test interest"
        ),
        credit_spec=client_account("client.interest_accrued", "Interest earned"),
        amount=ugx(77),
        on=march.end,
    )

    # The ledger is closed through the 31st, and the next morning's run asks.
    second = at(date(2031, 4, 2), hour=3)
    await schedule_run(worker_database, own_queue, second)
    await run_jobs(worker_database, second, through=march.end)
    assert await monthly_of(worker_database, client) == [
        ("2031-02", "empty"),
        ("2031-03", "ready"),
    ]
    async with app_database.actor_transaction(client) as session:
        mine = await service.client_documents(session, client)
    statement = drawn[f"ST-{mine[0].id}"]
    assert statement.period == march
    assert statement.not_final_from is None
    assert [(s.paid_in, s.earned, s.closing) for s in statement.summaries] == [
        (ugx(5_000), ugx(77), ugx(5_077))
    ]

    # A third morning asks for nothing more.
    third = at(date(2031, 4, 3), hour=3)
    await schedule_run(worker_database, own_queue, third)
    await run_jobs(worker_database, third, through=march.end)
    assert await monthly_of(worker_database, client) == [
        ("2031-02", "empty"),
        ("2031-03", "ready"),
    ]


async def test_a_monthly_statement_is_never_built_over_a_day_that_is_not_closed(
    app_database: Database, worker_database: Database
) -> None:
    client, _ = await invested(app_database, worker_database)
    assert client.id is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        await service.ask(
            session,
            client_id=client.id,
            kind=DocumentKind.MONTHLY_STATEMENT,
            key=paid_label(),
            period=paid_month(),
            now=later_clock().now(),
        )
    await run_jobs(worker_database, through=paid_month().end - timedelta(days=1))
    assert await monthly_of(worker_database, client) == [(paid_label(), "pending")]
    # It comes back later, and builds once the month is closed.
    later = FakeClock(later_clock().now() + timedelta(minutes=5))
    await run_jobs(worker_database, later, through=paid_month().end)
    assert await monthly_of(worker_database, client) == [(paid_label(), "ready")]


async def test_a_requested_statement_over_open_days_says_from_which_day_it_is_not_final(
    app_database: Database, worker_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = await invested(app_database, worker_database)
    drawn = drawn_statements(monkeypatch)
    asked = await ask(app_database, client)
    await run_jobs(worker_database, through=today() + timedelta(days=1))
    async with app_database.actor_transaction(client) as session:
        ready = await service.client_document(session, client, asked.id)
    assert ready.status is DocumentStatus.READY
    assert ready.not_final_from == today() + timedelta(days=2)
    assert drawn[f"ST-{asked.id}"].not_final_from == today() + timedelta(days=2)
    # The database holds the same rule: a monthly statement is never marked not final.
    with pytest.raises(DBAPIError, match="ck_documents_not_final"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "INSERT INTO documents (id, client_id, kind, idempotency_key, "
                    "period_start, period_end, requested_at, not_final_from) VALUES "
                    "(:id, :client, 'monthly_statement', '2030-01', DATE '2030-01-01', "
                    "DATE '2030-01-31', now(), DATE '2030-01-15')"
                ),
                {"id": push_id(), "client": client.id},
            )


# Receipts ----------------------------------------------------------------------------------------


async def confirmed_bank_transfer(
    app_database: Database, worker_database: Database, client: Actor
) -> tuple[str, int]:
    """A bank transfer the client made, matched, approved by a second person and confirmed.

    Gives the deposit's ID and its amount in shillings."""
    clock = FakeClock(bank_tests.NOW)
    minor = bank_tests.amount()
    transfer = await bank_tests.submit(app_database, clock, minor, actor=client)
    pot = await bank_tests.statement(app_database, clock, ((date(2026, 11, 18), minor, 0),))
    reviewer = bank_tests.staff()
    async with app_database.actor_transaction(reviewer) as session:
        candidates = await bank.candidate_lines(session, reviewer, transfer.deposit.id)
    line = next(found for found in candidates if found.account_reference == pot)
    await bank_tests.review(app_database, clock, transfer, bank.Matched(line.id), reviewer=reviewer)
    await bank_tests.approve(app_database, clock, transfer)
    payments_worker = Worker(
        worker_database, bank.BankTransferJobs(worker_database, clock).handlers(), clock=clock
    )
    for _ in range(2):
        while await payments_worker.run_once():
            pass

    return transfer.deposit.id, minor


async def test_a_bank_transfer_receipt_names_the_method_and_shows_no_payer_number(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, later_clock())
    deposit_id, minor = await confirmed_bank_transfer(app_database, worker_database, client)
    async with worker_database.actor_transaction(SYSTEM) as session:
        receipt, holder = await building.receipt(session, deposit_id=deposit_id, reference="RC-1")
    assert (receipt.method, receipt.paid_from, receipt.amount) == (
        "Bank transfer",
        None,
        ugx(minor),
    )
    # The investment it names is not one this module can find, so it says so plainly.
    assert receipt.paid_for == "your investment"
    assert pdf.receipt_pdf(receipt).startswith(b"%PDF-")
    email = receipt_email(first_name=holder.first_name, receipt=receipt, file_name="r.pdf")
    assert "Bank transfer" in email.text
    assert "Paid from" not in email.text


# Routes ---------------------------------------------------------------------------------------

PASSWORD = "copper-harbour-violin"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)


@pytest.fixture
async def http(
    test_database: TestDatabase, app_database: Database
) -> AsyncIterator[tuple[httpx.AsyncClient, IdentityService]]:
    app = create_app(test_database.settings("finvest_app"), app_database, later_clock(), HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as client:
        yield client, app.state.identity


async def test_a_client_asks_for_and_downloads_a_statement_over_http(
    http: tuple[httpx.AsyncClient, IdentityService],
    app_database: Database,
    worker_database: Database,
) -> None:
    api, identity = http
    assert (await api.get("/client/documents")).status_code == 401
    client_id = await identity.create_client(
        phone=f"+2567{abs(hash(push_id())) % 10**8:08d}", email=None, password=PASSWORD
    )
    client = Actor("client", client_id)
    issued = await identity.issue_session(client, second_step="passkey", transport="token")
    api.headers["Authorization"] = f"Bearer {issued.token}"
    async with app_database.actor_transaction(client) as session:
        await ready_draft(session, client, later_clock())
        check = await kyc.submit(session, SEALER, client, ManualProvider(), later_clock())
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        await kyc.approve(session, SEALER, staff, check.id, "Documents match", later_clock())

    body = {
        "date_from": asked_period().start.isoformat(),
        "date_to": asked_period().end.isoformat(),
    }
    assert (await api.post("/client/statements", json=body)).status_code == 428
    asked = await api.post(
        "/client/statements", json=body, headers={"Idempotency-Key": "statement-http-1"}
    )
    assert asked.status_code == 202
    item = asked.json()
    assert (item["status"], item["kind"], item["date_from"], item["file_name"]) == (
        "pending",
        "requested_statement",
        asked_period().start.isoformat(),
        None,
    )
    assert (item["reference"], item["note"]) == (f"ST-{item['id']}", None)
    assert (await api.get(f"/client/documents/{item['id']}/file")).status_code == 404
    refused = await api.post(
        "/client/statements",
        json={
            "date_from": asked_period().end.isoformat(),
            "date_to": asked_period().start.isoformat(),
        },
        headers={"Idempotency-Key": "statement-http-2"},
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["fields"] == [
        {"field_path": ["date_from"], "code": "period_backwards"}
    ]

    await run_jobs(worker_database)
    listed = (await api.get("/client/documents")).json()["items"]
    assert [(row["id"], row["status"]) for row in listed] == [(item["id"], "ready")]
    download = await api.get(f"/client/documents/{item['id']}/file")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["cache-control"] == "no-store"
    assert download.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''" + quote(f"Finvest statement {asked_period().label}.pdf")
    )
    assert download.content.startswith(b"%PDF-")
    assert len(download.content) == listed[0]["byte_size"]


# Emailing ----------------------------------------------------------------------------------------


async def client_with_email(
    identity: IdentityService,
    app_database: Database,
    worker_database: Database,
    *,
    proved: bool = True,
) -> Actor:
    """An approved client who can be reached at an email address, proved with a link unless not."""
    address = f"amara.{push_id().lower().replace('_', '').replace('-', '')}@example.com"
    client_id = await identity.create_client(
        phone=f"+2567{abs(hash(push_id())) % 10**8:08d}", email=address, password=PASSWORD
    )
    if proved:
        checks = EmailCheckService(app_database, FakeClock(), "https://app.finvestug.com")
        assert await checks.send_link(client_id)
        async with worker_database.transaction() as session:
            link = await session.scalar(
                text(
                    "SELECT variables->>'link' FROM notification_messages "
                    "WHERE recipient = :r AND template = 'email_verification'"
                ),
                {"r": address},
            )
        await checks.confirm(link.split("#", 1)[1])
    client = Actor("client", client_id)
    async with app_database.actor_transaction(client) as session:
        await ready_draft(session, client, later_clock())
        check = await kyc.submit(session, SEALER, client, ManualProvider(), later_clock())
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        await kyc.approve(session, SEALER, staff, check.id, "Documents match", later_clock())
    return client


async def emails_of(worker_database: Database, document_id: str) -> list[tuple[str, str | None]]:
    async with worker_database.actor_transaction(SYSTEM) as session:
        rows = (
            await session.execute(
                text("SELECT status, failure_code FROM document_emails WHERE document_id = :id"),
                {"id": document_id},
            )
        ).all()
    return [(row.status, row.failure_code) for row in rows]


async def test_a_ready_statement_is_emailed_once_with_its_pdf_attached(
    http: tuple[httpx.AsyncClient, IdentityService],
    app_database: Database,
    worker_database: Database,
) -> None:
    _, identity = http
    client = await client_with_email(identity, app_database, worker_database)
    asked = await ask(app_database, client)
    email = FakeEmail()
    await run_jobs(worker_database, email=email)
    (message,) = email.sent
    async with app_database.actor_transaction(client) as session:
        stored = await service.client_file(session, client, asked.id)
    assert message.to.startswith("amara.") and message.to.endswith("@example.com")
    assert message.subject == f"Your Finvest statement, {asked_period().label}"
    assert message.idempotency_key == f"document:{asked.id}"
    (attached,) = message.attachments
    assert attached.file_name == stored.file_name
    # The emailed copy is locked with the client's date of birth, 22 June 1994. Opened, it is
    # the stored file. The copy in the app is not locked.
    assert not PdfReader(BytesIO(stored.content)).is_encrypted
    locked = PdfReader(BytesIO(attached.content))
    assert locked.is_encrypted
    assert not locked.decrypt("wrong")
    assert locked.decrypt("22061994")
    assert [page.extract_text() for page in locked.pages] == [
        page.extract_text() for page in PdfReader(BytesIO(stored.content)).pages
    ]
    assert "05031990" in message.text and "date of birth" in message.html
    # The email carries no figures: they are in the PDF.
    assert "UGX" not in message.text and "UGX" not in message.html
    assert await emails_of(worker_database, asked.id) == [("sent", None)]
    # Run again: nothing more is sent.
    assert await run_jobs(worker_database, email=email) == 0
    assert len(email.sent) == 1


async def test_an_email_waits_for_a_worker_with_the_identity_key_and_needs_a_date_of_birth(
    http: tuple[httpx.AsyncClient, IdentityService],
    app_database: Database,
    worker_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, identity = http
    client = await client_with_email(identity, app_database, worker_database)
    asked = await ask(app_database, client)
    email = FakeEmail()
    # Without the key there is no date of birth to lock it with, and it is never sent unlocked.
    await run_jobs(worker_database, email=email, sealer=None)
    assert email.sent == []
    assert await emails_of(worker_database, asked.id) == [("queued", None)]
    async with worker_database.actor_transaction(SYSTEM) as session:
        code = await session.scalar(
            text(
                "SELECT last_error_code FROM jobs WHERE idempotency_key = :key AND queue = :queue"
            ),
            {"key": f"document-email:{asked.id}", "queue": service.QUEUE},
        )
    assert code == "identity_key_missing"

    # A client with no identity check on record has no password to type: nothing is sent.
    async def no_check(session: AsyncSession, sealer: Sealer, client_id: str) -> None:
        return None

    monkeypatch.setattr(kyc, "screening_subject", no_check)
    await run_jobs(
        worker_database, FakeClock(later_clock().now() + timedelta(minutes=5)), email=email
    )
    assert email.sent == []
    assert await emails_of(worker_database, asked.id) == [("skipped", "no_date_of_birth")]


async def test_a_client_with_no_email_address_is_recorded_as_skipped(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, later_clock())
    asked = await ask(app_database, client)
    email = FakeEmail()
    await run_jobs(worker_database, email=email)
    assert email.sent == []
    assert await emails_of(worker_database, asked.id) == [("skipped", "no_email")]


async def test_an_unclaimed_account_is_not_sent_a_document(
    http: tuple[httpx.AsyncClient, IdentityService],
    app_database: Database,
    worker_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Identity answers for an account carried over from Finvest 1.0 so it can be reminded to
    # claim it, and says it is unclaimed. Identity's own tests cover when that is so.
    _, identity = http
    client = await client_with_email(identity, app_database, worker_database)
    real = identity_service.client_contact

    async def unclaimed(session: AsyncSession, client_id: str) -> ClientContact | None:
        found = await real(session, client_id)
        return replace(found, claimed=False) if found is not None else None

    monkeypatch.setattr(identity_service, "client_contact", unclaimed)
    asked = await ask(app_database, client)
    email = FakeEmail()
    await run_jobs(worker_database, email=email)
    assert email.sent == []
    assert await emails_of(worker_database, asked.id) == [("skipped", "no_email")]


async def test_an_address_the_client_has_not_proved_is_not_sent_a_document(
    http: tuple[httpx.AsyncClient, IdentityService],
    app_database: Database,
    worker_database: Database,
) -> None:
    # The PDF carries the account number and figures and has no password, so a mistyped
    # address must not receive it.
    _, identity = http
    client = await client_with_email(identity, app_database, worker_database, proved=False)
    asked = await ask(app_database, client)
    email = FakeEmail()
    await run_jobs(worker_database, email=email)
    assert email.sent == []
    assert await emails_of(worker_database, asked.id) == [("skipped", "email_not_verified")]


async def suppress(app_database: Database, worker_database: Database, address: str) -> None:
    """Record the address as bounced for good, as Resend's webhook would, through the real roles."""
    svix_id = f"msg_{secrets.token_hex(8)}"
    async with app_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO notification_email_events "
                "(svix_id, delivery, email_id, recipients, permanent, received_at) "
                "VALUES (:svix_id, 'bounced', 'e-1', ARRAY[:hash], true, now())"
            ),
            {"svix_id": svix_id, "hash": address_hash(address)},
        )
    async with worker_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO notification_email_suppressions "
                "(address_hash, reason, svix_id, created_at) "
                "VALUES (:hash, 'bounced', :svix_id, now())"
            ),
            {"hash": address_hash(address), "svix_id": svix_id},
        )


async def test_a_suppressed_address_is_not_sent_a_document(
    http: tuple[httpx.AsyncClient, IdentityService],
    app_database: Database,
    worker_database: Database,
) -> None:
    _, identity = http
    client = await client_with_email(identity, app_database, worker_database)
    async with worker_database.actor_transaction(SYSTEM) as session:
        contact = await identity_service.client_contact(session, str(client.id))
    assert contact is not None and contact.email is not None
    # Suppression matches the address however it is written.
    await suppress(app_database, worker_database, contact.email.upper())
    asked = await ask(app_database, client)
    email = FakeEmail()
    await run_jobs(worker_database, email=email)
    assert email.sent == []
    assert await emails_of(worker_database, asked.id) == [("skipped", "suppressed")]


async def test_a_deferred_email_waits_and_a_refused_one_is_recorded(
    http: tuple[httpx.AsyncClient, IdentityService],
    app_database: Database,
    worker_database: Database,
) -> None:
    _, identity = http
    client = await client_with_email(identity, app_database, worker_database)
    asked = await ask(app_database, client)
    await run_jobs(worker_database, email=FakeEmail(EmailDeferred("Resend is unreachable")))
    assert await emails_of(worker_database, asked.id) == [("queued", None)]
    # The retry comes later, and Resend refuses it this time.
    later = FakeClock(later_clock().now() + timedelta(minutes=5))
    await run_jobs(worker_database, later, email=FakeEmail(EmailRejected("Resend rejected it")))
    assert await emails_of(worker_database, asked.id) == [("failed", "email_rejected")]


async def expire_emails(worker_database: Database, clock: FakeClock) -> None:
    """Run the daily sweep alone, as the scheduler starts it."""
    async with worker_database.actor_transaction(SYSTEM) as session:
        await enqueue(
            session,
            JobRequest(
                kind=service.EXPIRE_EMAILS_KIND,
                idempotency_key=f"documents.expire_emails:{clock.now():%Y-%m-%d}",
                payload={},
                queue=service.QUEUE,
                max_age=timedelta(hours=20),
            ),
            now=clock.now(),
        )
    jobs = DocumentJobs(worker_database, clock, email=FakeEmail(), closed_through=closed(None))
    handlers = {service.EXPIRE_EMAILS_KIND: jobs.handlers()[service.EXPIRE_EMAILS_KIND]}
    runner = Worker(worker_database, handlers, WorkerConfig(queue=service.QUEUE), clock)
    assert await runner.run_once()


async def test_an_email_whose_job_ended_unsent_is_closed_as_expired_after_a_week(
    http: tuple[httpx.AsyncClient, IdentityService],
    app_database: Database,
    worker_database: Database,
) -> None:
    _, identity = http
    client = await client_with_email(identity, app_database, worker_database)
    resend_down = FakeEmail(EmailDeferred("Resend is unreachable"))
    ended = await ask(app_database, client)
    await run_jobs(worker_database, email=resend_down)
    # Two days on, the retry is past its age: the worker fails the job without sending.
    await run_jobs(
        worker_database, FakeClock(later_clock().now() + timedelta(days=2)), email=resend_down
    )
    assert await emails_of(worker_database, ended.id) == [("queued", None)]
    # Another email whose job is still waiting to be retried.
    waiting = await ask(app_database, client)
    await run_jobs(worker_database, email=resend_down)
    assert await emails_of(worker_database, waiting.id) == [("queued", None)]

    # Within the week, nothing is closed.
    await expire_emails(worker_database, FakeClock(later_clock().now() + timedelta(days=6)))
    assert await emails_of(worker_database, ended.id) == [("queued", None)]
    await expire_emails(worker_database, FakeClock(later_clock().now() + timedelta(days=8)))
    assert await emails_of(worker_database, ended.id) == [("failed", "expired")]
    # A job that is still waiting is left to finish, however old the email.
    assert await emails_of(worker_database, waiting.id) == [("queued", None)]
    # The document itself stays in the app.
    async with app_database.actor_transaction(client) as session:
        listed = await service.client_documents(session, client)
    assert ended.id in {view.id for view in listed}


async def test_only_a_ready_document_is_queued_for_email_and_a_sent_one_never_changes(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, later_clock())
    pending = await ask(app_database, client)
    with pytest.raises(DBAPIError, match="only a ready document"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "INSERT INTO document_emails (document_id, client_id, queued_at) "
                    "VALUES (:id, :client, now())"
                ),
                {"id": pending.id, "client": client.id},
            )
    await run_jobs(worker_database)
    assert await emails_of(worker_database, pending.id) == [("skipped", "no_email")]
    with pytest.raises(DBAPIError, match="never changes"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE document_emails SET status = 'queued' WHERE document_id = :id"),
                {"id": pending.id},
            )
    for database in (app_database, worker_database):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text("DELETE FROM document_emails WHERE document_id = :id"),
                    {"id": pending.id},
                )


async def test_a_confirmed_payment_gets_a_receipt_that_is_built_and_emailed(
    http: tuple[httpx.AsyncClient, IdentityService],
    app_database: Database,
    worker_database: Database,
) -> None:
    _, identity = http
    client = await client_with_email(identity, app_database, worker_database)
    deposit_id, _ = await confirmed_bank_transfer(app_database, worker_database, client)
    async with worker_database.actor_transaction(SYSTEM) as session:
        # As the outbox delivers `payments.deposit_confirmed` to this consumer. Twice.
        for _ in range(2):
            await enqueue(
                session,
                JobRequest(
                    kind=RECEIPT_KIND,
                    idempotency_key=f"receipt-test:{deposit_id}",
                    payload={"data": {"deposit_id": deposit_id}},
                    queue=service.QUEUE,
                ),
                now=later_clock().now(),
            )
    email = FakeEmail()
    await run_jobs(worker_database, email=email)
    async with app_database.actor_transaction(client) as session:
        (receipt,) = await service.client_documents(session, client)
    assert (receipt.kind, receipt.status, receipt.source_id) == (
        DocumentKind.RECEIPT,
        DocumentStatus.READY,
        deposit_id,
    )
    (message,) = email.sent
    assert message.subject.startswith("Receipt: we received UGX ")
    assert [file.file_name for file in message.attachments] == [receipt.file_name]


# Checking a statement on the website -------------------------------------------------------------


async def check_code_of(worker_database: Database, document_id: str) -> str:
    async with worker_database.actor_transaction(SYSTEM) as session:
        code: str = (
            await session.execute(
                text("SELECT code FROM document_checks WHERE document_id = :id"),
                {"id": document_id},
            )
        ).scalar_one()
    return code


async def test_a_statement_carries_a_code_that_the_website_checks(
    http: tuple[httpx.AsyncClient, IdentityService],
    app_database: Database,
    worker_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, _ = http
    drawn = drawn_statements(monkeypatch)
    client, _ = await invested(app_database, worker_database)
    asked = await ask(app_database, client)
    await run_jobs(worker_database)
    statement = drawn[asked.reference]
    code = await check_code_of(worker_database, asked.id)
    assert statement.check_code == code
    async with app_database.actor_transaction(client) as session:
        found = await service.client_file(session, client, asked.id)
    printed = " ".join(page.extract_text() for page in PdfReader(BytesIO(found.content)).pages)
    shown = f"{code[:4]}-{code[4:8]}-{code[8:]}"
    assert "finvestug.com/check" in printed
    assert shown in printed

    # Typed as a person might: lower case, spaces in place of dashes.
    checked = await api.get(f"/public/statement-checks/{shown.lower().replace('-', ' ')}")
    assert checked.status_code == 200
    assert checked.headers["access-control-allow-origin"] == "*"
    assert checked.headers["cache-control"] == "no-store"
    body = checked.json()
    assert body["result"] == "genuine"
    assert body["statement"]["kind"] == "requested"
    assert (body["statement"]["period_start"], body["statement"]["period_end"]) == (
        asked_period().start.isoformat(),
        asked_period().end.isoformat(),
    )
    assert body["statement"]["account_ending"] == statement.account_number[-4:]
    assert body["statement"]["closing"] == [
        {"minor": summary.closing.minor, "currency": summary.currency.value}
        for summary in statement.summaries
    ]
    # Nothing about the client beyond what the statement already shows the bank.
    assert set(body["statement"]) == {
        "kind",
        "period_start",
        "period_end",
        "issued_at",
        "account_ending",
        "closing",
    }


@pytest.mark.parametrize("typed", ["0000-0000-0000", "not a code", "K7Q2-9XMA"])
async def test_a_code_no_statement_has_is_not_found(
    http: tuple[httpx.AsyncClient, IdentityService], typed: str
) -> None:
    api, _ = http
    answer = await api.get(f"/public/statement-checks/{quote(typed)}")
    assert answer.status_code == 200
    assert answer.json() == {"result": "not_found", "statement": None, "retry_after_seconds": None}


async def test_one_caller_may_try_only_a_few_codes_in_a_while(
    test_database: TestDatabase, app_database: Database
) -> None:
    app = create_app(test_database.settings("finvest_app"), app_database, later_clock(), HASHER)
    address = f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250)}"
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=(address, 1))
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as api:
        answers = [
            (await api.get("/public/statement-checks/0000-0000-0000")).json()["result"]
            for _ in range(21)
        ]
    assert answers[:20] == ["not_found"] * 20
    assert answers[20] == "too_many_tries"


async def test_a_check_is_written_once_by_the_worker_and_the_api_cannot_list_codes(
    app_database: Database, worker_database: Database
) -> None:
    client, _ = await invested(app_database, worker_database)
    asked = await ask(app_database, client)
    await run_jobs(worker_database)
    code = await check_code_of(worker_database, asked.id)
    for statement in (
        "UPDATE document_checks SET account_ending = '0000' WHERE document_id = :id",
        "DELETE FROM document_checks WHERE document_id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(text(statement), {"id": asked.id})
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(text("SELECT code FROM document_checks"))
    # The function answers one exact code, and only for a finished statement.
    async with app_database.transaction() as session:
        rows = (
            await session.execute(text("SELECT kind FROM documents_check(:code)"), {"code": code})
        ).all()
        assert [row.kind for row in rows] == ["requested_statement"]
        assert (
            await session.execute(text("SELECT count(*) FROM documents_check('%')"))
        ).scalar_one() == 0
