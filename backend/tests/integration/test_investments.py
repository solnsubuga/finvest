"""Investments against real PostgreSQL: the request, the start on a confirmed payment, the
fences and the races."""

import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, WorkerConfig, enqueue
from app.modules.investments import service as investments
from app.modules.investments.domain.interest import ClientReturns
from app.modules.investments.domain.investments import AtMaturity, InvestmentStatus
from app.modules.investments.events import START_KIND
from app.modules.investments.holdings import AWAITING_START, HOLDING
from app.modules.investments.jobs import InvestmentJobs
from app.modules.ledger import service as ledger
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.investments import (
    PRESENT,
    account_balance,
    after,
    approved_client,
    at,
    pay,
    request,
    started,
    today,
)
from tests.support.ledger import client as new_client
from tests.support.ledger import key, ugx
from tests.support.ledger import staff as new_staff
from tests.support.payments import start_deposit

pytestmark = pytest.mark.integration

DAY = today()
NOW = PRESENT
ACTIVE, AWAITING, CANCELLED = (
    InvestmentStatus.ACTIVE,
    InvestmentStatus.AWAITING_PAYMENT,
    InvestmentStatus.CANCELLED,
)


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, list[tuple[str, str]]]:
    return error.value.code, [
        (str(problem.field_path[0]), problem.code) for problem in error.value.fields
    ]


async def start(
    worker_database: Database,
    investment_id: str,
    *,
    confirmed_on: date = DAY,
    clock: FakeClock = NOW,
) -> investments.Investment:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.start_investment(
            session, investment_id, confirmed_on=confirmed_on, clock=clock
        )


# Review and confirm -----------------------------------------------------------------------------


async def test_a_confirmed_review_saves_the_investment_with_todays_terms(
    app_database: Database,
) -> None:
    client = await approved_client(app_database, NOW)
    investment = await request(app_database, client, NOW)
    assert investment.status is AWAITING
    assert investment.amount == ugx(1_000_000)
    assert (investment.terms.annual_rate_bps, investment.terms.tax_bps) == (1250, 1500)
    assert investment.term_days == 91
    assert investment.at_maturity is AtMaturity.ROLL_OVER
    assert investment.pay_by == investment.created_at + timedelta(hours=72)
    assert abs(investment.created_at - NOW.now()) < timedelta(minutes=1)
    assert investment.start_date is None


async def test_a_retry_with_the_same_key_is_the_same_investment(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    idempotency_key = key()
    first = await request(app_database, client, NOW, idempotency_key=idempotency_key)
    again = await request(app_database, client, NOW, idempotency_key=idempotency_key)
    assert again.id == first.id
    with pytest.raises(ApplicationError) as error:
        await request(
            app_database, client, NOW, idempotency_key=idempotency_key, amount=ugx(2_000_000)
        )
    assert error.value.code == "idempotency_key_reused"


async def test_two_taps_at_once_invest_once(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    idempotency_key = key()
    barrier = AsyncBarrier(2)

    async def tap() -> str:
        async with app_database.actor_transaction(client) as session:
            await barrier.wait()
            investment = await investments.request_investment(
                session,
                client,
                idempotency_key=idempotency_key,
                product_code="TREA-91",
                amount=ugx(1_000_000),
                at_maturity=None,
                clock=NOW,
            )
            return investment.id

    first, second = await asyncio.gather(tap(), tap())
    assert first == second
    async with app_database.actor_transaction(client) as session:
        assert len(await investments.client_investments(session, client, NOW)) == 1


async def test_nobody_invests_before_their_identity_is_checked(app_database: Database) -> None:
    with pytest.raises(ApplicationError) as error:
        await request(app_database, new_client(), NOW)
    assert error.value.code == "forbidden"


@pytest.mark.parametrize(
    ("code", "amount", "problem"),
    [
        ("TREA-91", ugx(99_999), "below_minimum"),
        ("TREA-182", ugx(100_000), "below_minimum"),
        ("TREA-91", Money(100_000_00, Currency.USD), "currency_not_supported"),
        ("USD-BOND", Money(1_000_00, Currency.USD), "product_not_open"),
    ],
)
async def test_an_amount_the_product_would_not_take_is_refused(
    app_database: Database, code: str, amount: Money, problem: str
) -> None:
    client = await approved_client(app_database, NOW)
    with pytest.raises(ApplicationError) as error:
        await request(app_database, client, NOW, product_code=code, amount=amount)
    assert refused(error) == ("validation_failed", [("amount", problem)])


async def test_a_client_holds_at_most_five_unpaid_investments(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    for _ in range(5):
        await request(app_database, client, NOW)
    with pytest.raises(ApplicationError) as error:
        await request(app_database, client, NOW)
    assert error.value.code == "conflict"
    # Once they are cancelled for not being paid, the client can ask again.
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.cancel_unpaid(session, after(hours=74))
    await request(app_database, client, NOW)


async def test_the_cap_holds_when_requests_race(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    for _ in range(4):
        await request(app_database, client, NOW)
    barrier = AsyncBarrier(2)

    async def ask() -> str:
        try:
            async with app_database.actor_transaction(client) as session:
                await barrier.wait()
                await investments.request_investment(
                    session,
                    client,
                    idempotency_key=key(),
                    product_code="TREA-91",
                    amount=ugx(1_000_000),
                    at_maturity=None,
                    clock=NOW,
                )
        except ApplicationError as error:
            return error.code
        return "saved"

    assert sorted(await asyncio.gather(ask(), ask())) == ["conflict", "saved"]


# What the database enforces ---------------------------------------------------------------------

_INSERT = text(
    "INSERT INTO investments (id, client_id, product_id, currency, principal_minor, "
    "annual_rate_bps, tax_bps, term_days, at_maturity, created_at, pay_by, updated_at) VALUES "
    "(:id, :client_id, 'FvProduct1TREA91_001', :currency, :principal, :rate, :tax, :term, "
    "'roll_over', now(), now() + interval '72 hours', now())"
)
_GOOD = {"currency": "UGX", "principal": 1_000_000, "rate": 1250, "tax": 1500, "term": 91}


@pytest.mark.parametrize(
    ("created_at", "pay_by", "message"),
    [
        ("now() - interval '10 minutes'", "now() + interval '71 hours 50 minutes'", "current"),
        ("now() + interval '10 minutes'", "now() + interval '72 hours 10 minutes'", "current"),
        ("now()", "now() + interval '30 days'", "72 hours"),
    ],
)
async def test_the_database_refuses_an_investment_not_created_now_or_payable_for_longer(
    app_database: Database, created_at: str, pay_by: str, message: str
) -> None:
    client = new_client()
    statement = text(
        "INSERT INTO investments (id, client_id, product_id, currency, principal_minor, "  # noqa: S608
        "annual_rate_bps, tax_bps, term_days, at_maturity, created_at, pay_by, updated_at) "
        "VALUES (:id, :client_id, 'FvProduct1TREA91_001', 'UGX', 1000000, 1250, 1500, 91, "
        f"'roll_over', {created_at}, {pay_by}, now())"
    )
    with pytest.raises(DBAPIError, match=message):
        async with app_database.actor_transaction(client) as session:
            await session.execute(statement, {"id": push_id(), "client_id": client.id})


@pytest.mark.parametrize(
    "change",
    [
        {"rate": 5000},  # a rate the catalogue does not offer
        {"tax": 0},
        {"principal": 99_999},
        {"term": 30},
        {"currency": "USD"},
    ],
)
async def test_the_database_refuses_terms_that_are_not_the_products(
    app_database: Database, change: dict[str, object]
) -> None:
    client = new_client()
    with pytest.raises(DBAPIError, match="product's own terms"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                _INSERT, {"id": push_id(), "client_id": client.id, **_GOOD, **change}
            )


async def test_a_client_cannot_create_an_investment_for_someone_else(
    app_database: Database,
) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(new_client()) as session:
            await session.execute(_INSERT, {"id": push_id(), "client_id": new_client().id, **_GOOD})


async def test_a_client_sees_only_their_own_investments(app_database: Database) -> None:
    owner = await approved_client(app_database, NOW)
    investment = await request(app_database, owner, NOW)
    stranger = new_client()
    async with app_database.actor_transaction(stranger) as session:
        assert await investments.client_investments(session, stranger, NOW) == []
        with pytest.raises(ApplicationError) as error:
            await investments.client_investment(session, stranger, investment.id, NOW)
        assert error.value.code == "not_found"
        count = await session.execute(text("SELECT count(*) FROM investments"))
        assert count.scalar_one() == 0


async def test_nobody_in_the_api_can_start_cancel_or_reprice_an_investment(
    app_database: Database,
) -> None:
    owner = await approved_client(app_database, NOW)
    investment = await request(app_database, owner, NOW)
    for actor in (owner, new_staff()):
        for statement in (
            "UPDATE investments SET status = 'active', start_date = CURRENT_DATE, "
            "started_at = now(), start_entry_id = id WHERE id = :id",
            "UPDATE investments SET status = 'cancelled', cancelled_at = now() WHERE id = :id",
            "UPDATE investments SET annual_rate_bps = 5000 WHERE id = :id",
            "UPDATE investments SET principal_minor = 9000000 WHERE id = :id",
            "UPDATE investments SET accrued_gross_minor = 500000, "
            "accrued_through = CURRENT_DATE + 1 WHERE id = :id",
            "DELETE FROM investments WHERE id = :id",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                async with app_database.actor_transaction(actor) as session:
                    await session.execute(text(statement), {"id": investment.id})


async def test_the_worker_cannot_change_terms_or_rewrite_a_start(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    for statement, message in (
        (
            "UPDATE investments SET start_date = start_date - 12, maturity_date = "
            "maturity_date - 12 WHERE id = :id",
            "start of an investment never changes",
        ),
        (
            "UPDATE investments SET status = 'awaiting_payment', start_date = NULL, "
            "maturity_date = NULL, started_at = NULL, start_entry_id = NULL WHERE id = :id",
            "cannot go from active",
        ),
        (
            "UPDATE investments SET accrued_gross_minor = 10, accrued_through = start_date + 1 "
            "WHERE id = :id; UPDATE investments SET accrued_gross_minor = 5, "
            "accrued_through = start_date + 2 WHERE id = :id",
            "forwards only",
        ),
    ):
        with pytest.raises(DBAPIError, match=message):
            async with worker_database.actor_transaction(SYSTEM) as session:
                for part in statement.split("; "):
                    await session.execute(text(part), {"id": investment.id})


# Starting on a confirmed payment ----------------------------------------------------------------


async def test_the_confirmed_payment_moves_into_the_holding_and_the_investment_starts(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    investment = await request(app_database, client, NOW)
    await pay(worker_database, investment, ugx(1_000_000), NOW)

    running = await start(worker_database, investment.id)

    assert running.status is ACTIVE
    assert running.start_date == DAY
    assert running.maturity_date == DAY + timedelta(days=91)
    assert await account_balance(worker_database, investment, AWAITING_START) == 0
    assert await account_balance(worker_database, investment, HOLDING) == 1_000_000
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await ledger.find_balance_drift(session) == []


async def test_starting_twice_changes_nothing(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    again = await start(worker_database, investment.id, clock=at(DAY + timedelta(days=1)))
    assert again == investment
    assert await account_balance(worker_database, investment, HOLDING) == 1_000_000


async def test_the_start_date_is_the_day_the_payment_was_confirmed_not_the_day_it_was_processed(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    investment = await request(app_database, client, NOW)
    await pay(worker_database, investment, ugx(1_000_000), NOW)
    two_days_later = at(DAY + timedelta(days=2))
    running = await start(worker_database, investment.id, clock=two_days_later)
    assert running.start_date == DAY
    assert running.maturity_date == DAY + timedelta(days=91)


@pytest.mark.parametrize("paid", [0, 999_999, 1_000_001])
async def test_anything_but_the_exact_amount_starts_nothing_and_moves_nothing(
    app_database: Database, worker_database: Database, paid: int
) -> None:
    client = await approved_client(app_database, NOW)
    investment = await request(app_database, client, NOW)
    if paid:
        await pay(worker_database, investment, ugx(paid), NOW)
    with pytest.raises(ApplicationError) as error:
        await start(worker_database, investment.id)
    assert error.value.code == "conflict"
    assert await account_balance(worker_database, investment, AWAITING_START) == paid
    assert await account_balance(worker_database, investment, HOLDING) == 0
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert (await investments.get_investment(session, investment.id)).status is AWAITING


@pytest.mark.parametrize("confirmed_on", [DAY - timedelta(days=1), DAY + timedelta(days=1)])
async def test_an_impossible_confirmation_date_starts_nothing(
    app_database: Database, worker_database: Database, confirmed_on: date
) -> None:
    client = await approved_client(app_database, NOW)
    investment = await request(app_database, client, NOW)
    await pay(worker_database, investment, ugx(1_000_000), NOW)
    with pytest.raises(ApplicationError):
        await start(worker_database, investment.id, confirmed_on=confirmed_on)
    assert await account_balance(worker_database, investment, AWAITING_START) == 1_000_000


async def test_two_workers_starting_one_investment_post_one_entry(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    investment = await request(app_database, client, NOW)
    await pay(worker_database, investment, ugx(1_000_000), NOW)
    barrier = AsyncBarrier(2)

    async def run() -> InvestmentStatus:
        async with worker_database.actor_transaction(SYSTEM) as session:
            await barrier.wait()
            result = await investments.start_investment(
                session, investment.id, confirmed_on=DAY, clock=NOW
            )
            return result.status

    assert list(await asyncio.gather(run(), run())) == [ACTIVE, ACTIVE]
    assert await account_balance(worker_database, investment, HOLDING) == 1_000_000
    assert await account_balance(worker_database, investment, AWAITING_START) == 0
    async with worker_database.actor_transaction(SYSTEM) as session:
        entries = await session.execute(
            text(
                "SELECT count(*) FROM ledger_journal_entries "
                "WHERE source_type = 'investment' AND source_id = :id"
            ),
            {"id": investment.id},
        )
        assert entries.scalar_one() == 1


# The job that payments' event starts ------------------------------------------------------------


async def run_start_job(
    worker_database: Database,
    data: dict[str, object],
    clock: FakeClock = NOW,
) -> tuple[str, str | None]:
    """Queue the consumer job as the outbox would, run it, and return how it ended."""
    queue = "inv" + "".join(c for c in push_id().lower() if c.isalnum())
    idempotency_key = f"payments.deposit_confirmed:{push_id()}"
    async with worker_database.actor_transaction(SYSTEM) as session:
        job_id = await enqueue(
            session,
            JobRequest(
                kind=START_KIND,
                idempotency_key=idempotency_key,
                payload={
                    "event_id": push_id(),
                    "topic": "payments.deposit_confirmed",
                    "data": data,
                },
                queue=queue,
            ),
            now=clock.now(),
        )
    runner = Worker(
        worker_database,
        InvestmentJobs(worker_database, clock).handlers(),
        WorkerConfig(queue=queue),
        clock,
    )
    assert await runner.run_once() == 1
    async with worker_database.actor_transaction(SYSTEM) as session:
        row = (
            await session.execute(
                text("SELECT status, last_error_code FROM jobs WHERE id = :id"), {"id": job_id}
            )
        ).one()
    return row.status, row.last_error_code


async def test_a_confirmed_deposit_starts_its_investment(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    investment = await request(app_database, client, NOW)
    await pay(worker_database, investment, ugx(1_000_000), NOW)
    processed_late = at(DAY + timedelta(days=1))

    outcome = await run_start_job(
        worker_database,
        {
            "deposit_id": push_id(),
            "purpose_type": "investment",
            "purpose_id": investment.id,
            "confirmed_on": DAY.isoformat(),
        },
        processed_late,
    )

    assert outcome == ("succeeded", None)
    async with worker_database.actor_transaction(SYSTEM) as session:
        running = await investments.get_investment(session, investment.id)
    assert (running.status, running.start_date) == (ACTIVE, DAY)


async def test_a_deposit_for_a_goal_is_left_alone(worker_database: Database) -> None:
    outcome = await run_start_job(
        worker_database,
        {"deposit_id": push_id(), "purpose_type": "goal", "purpose_id": push_id()},
    )
    assert outcome == ("succeeded", None)


async def test_a_deposit_that_does_not_match_fails_the_job_for_staff(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    investment = await request(app_database, client, NOW)
    await pay(worker_database, investment, ugx(400_000), NOW)
    status, code = await run_start_job(
        worker_database,
        {"deposit_id": push_id(), "purpose_type": "investment", "purpose_id": investment.id},
    )
    assert (status, code) == ("failed", "investment_conflict")
    assert await account_balance(worker_database, investment, AWAITING_START) == 400_000


# Paying, and not paying -------------------------------------------------------------------------


async def test_payments_is_told_who_pays_and_exactly_how_much(app_database: Database) -> None:
    client = await approved_client(app_database, NOW)
    investment = await request(app_database, client, NOW, amount=ugx(2_500_000))
    async with app_database.actor_transaction(client) as session:
        payable = await investments.payable_investment(
            session, client_id=client.id or "", investment_id=investment.id, clock=NOW
        )
    assert payable == investments.PayableInvestment(investment.id, client.id or "", ugx(2_500_000))


async def test_an_investment_cannot_be_paid_by_someone_else_or_after_its_window(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    investment = await request(app_database, client, NOW)

    async def payable(client_id: str, clock: FakeClock = NOW) -> str:
        try:
            async with worker_database.actor_transaction(SYSTEM) as session:
                await investments.payable_investment(
                    session, client_id=client_id, investment_id=investment.id, clock=clock
                )
        except ApplicationError as error:
            return error.code
        return "payable"

    assert await payable(client.id or "") == "payable"
    assert await payable(new_client().id or "") == "not_found"
    assert await payable(client.id or "", after(hours=72, minutes=1)) == "conflict"
    await pay(worker_database, investment, ugx(1_000_000), NOW)
    await start(worker_database, investment.id)
    assert await payable(client.id or "") == "conflict"


async def test_an_unpaid_investment_is_cancelled_after_its_window(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    unpaid = await request(app_database, client, NOW)
    part_paid = await request(app_database, client, NOW)
    await pay(worker_database, part_paid, ugx(300_000), NOW)

    async def sweep(clock: FakeClock) -> None:
        async with worker_database.actor_transaction(SYSTEM) as session:
            await investments.cancel_unpaid(session, clock)

    async def status(investment_id: str) -> InvestmentStatus:
        async with worker_database.actor_transaction(SYSTEM) as session:
            return (await investments.get_investment(session, investment_id)).status

    await sweep(after(hours=72, minutes=30))  # the window has closed, the grace has not
    assert await status(unpaid.id) is AWAITING
    await sweep(after(hours=73, minutes=1))
    assert await status(unpaid.id) is CANCELLED
    # Money arrived for this one, so it is never cancelled under it.
    assert await status(part_paid.id) is AWAITING
    # A cancelled investment is final, even for the worker.
    with pytest.raises(DBAPIError, match="never changes"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE investments SET updated_at = now() WHERE id = :id"), {"id": unpaid.id}
            )
    with pytest.raises(ApplicationError):
        await start(worker_database, unpaid.id)


async def test_an_investment_with_a_deposit_still_in_progress_is_not_cancelled(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    investment = await request(app_database, client, NOW)
    # The prompt went out and the client has not answered by the end of the window.
    await start_deposit(app_database, NOW, actor=client, amount=1_000_000, purpose_id=investment.id)

    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.cancel_unpaid(session, after(hours=74))
        still = await investments.get_investment(session, investment.id)
    assert still.status is AWAITING


# What the client sees ---------------------------------------------------------------------------


async def test_the_portfolio_shows_figures_after_tax_only(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    client = Actor("client", investment.client_id)
    async with app_database.actor_transaction(client) as session:
        (view,) = await investments.client_investments(session, client, NOW)
        same = await investments.client_investment(session, client, investment.id, NOW)
    assert view == same
    assert view.status is ACTIVE
    assert view.rate_percent == "10.63"
    assert view.amount == view.value == ugx(1_000_000)
    assert view.interest_so_far == view.interest_today == ugx(0)
    assert view.at_maturity_returns == ClientReturns(ugx(26_490), ugx(1_026_490))
    assert (view.start_date, view.maturity_date) == (DAY, DAY + timedelta(days=91))
    assert not [name for name in investments.ClientInvestment.__slots__ if "tax" in name]
    assert not [name for name in investments.ClientInvestment.__slots__ if "gross" in name]


async def test_staff_list_investments_and_clients_cannot(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        running = await investments.list_investments(session, staff, status=ACTIVE, limit=200)
    assert investment.id in [item.id for item in running]
    client = Actor("client", investment.client_id)
    async with app_database.actor_transaction(client) as session:
        with pytest.raises(ApplicationError) as error:
            await investments.list_investments(session, client)
    assert error.value.code == "forbidden"


# Rate approval, from the review of migration 0060 -----------------------------------------------


async def test_the_database_refuses_an_approval_on_or_after_the_effective_date(
    app_database: Database,
) -> None:
    daniel, grace = new_staff(), new_staff()
    async with app_database.actor_transaction(daniel) as session:
        proposal = await investments.propose_rate(
            session,
            daniel,
            product_code="USD-BOND",
            annual_rate_bps=700,
            tax_bps=500,
            effective_from=date(2031, 3, 1),
            reason="Coupon reset",
            clock=NOW,
        )
    try:
        with pytest.raises(DBAPIError, match="ck_investment_rate_proposals_in_time"):
            async with app_database.actor_transaction(grace) as session:
                await session.execute(
                    text(
                        "UPDATE investment_rate_proposals SET status = 'approved', decided_by = "
                        ":staff, decided_at = TIMESTAMPTZ '2031-02-28 21:00+00' WHERE id = :id"
                    ),
                    {"staff": grace.id, "id": proposal.id},
                )
        # Approved in time, but with no rate written: refused when the transaction commits.
        with pytest.raises(DBAPIError, match="must have its rate"):
            async with app_database.actor_transaction(grace) as session:
                await session.execute(
                    text(
                        "UPDATE investment_rate_proposals SET status = 'approved', decided_by = "
                        ":staff, decided_at = TIMESTAMPTZ '2031-02-28 20:59+00' WHERE id = :id"
                    ),
                    {"staff": grace.id, "id": proposal.id},
                )
    finally:
        async with app_database.actor_transaction(daniel) as session:
            await investments.withdraw_rate(session, daniel, proposal.id, NOW)
