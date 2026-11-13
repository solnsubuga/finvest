"""Early withdrawal against real PostgreSQL: the request, the move to payouts, the payout once
the dealing desk has funded it, and the fences."""

import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, WorkerConfig, enqueue
from app.modules.audit import service as audit
from app.modules.investments import accrual
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import InvestmentStatus
from app.modules.investments.events import PAY_EARLY_WITHDRAWAL_KIND, WITHDRAW_KIND
from app.modules.investments.holdings import HOLDING
from app.modules.investments.jobs import InvestmentJobs
from app.modules.ledger import service as ledger
from app.modules.payouts import payouts
from app.modules.payouts.domain import posting as payout_posting
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.investments import (
    PRESENT,
    account_balance,
    approved_client,
    approved_destination,
    at,
    started,
    today,
)
from tests.support.ledger import key, ugx
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

DAY = today()
NOW = PRESENT
# A day after the destinations are approved, past their first day's safety wait.
LATER = at(today() + timedelta(days=2))


def day(offset: int) -> date:
    return DAY + timedelta(days=offset)


async def earn(worker_database: Database, investment_id: str, days: int) -> None:
    """Pay `days` of interest, as the nightly runs would have."""
    async with worker_database.actor_transaction(SYSTEM) as session:
        await accrual.accrue_investment(session, investment_id, day(days), at(day(days)))


async def ask(
    app_database: Database,
    investment: investments.Investment,
    destination_id: str,
    *,
    client: Actor | None = None,
    idempotency_key: str | None = None,
    clock: FakeClock = LATER,
) -> investments.Investment:
    client = client or Actor("client", investment.client_id)
    async with app_database.actor_transaction(client) as session:
        return await investments.request_withdrawal(
            session,
            client,
            investment.id,
            destination_id=destination_id,
            idempotency_key=idempotency_key or key(),
            clock=clock,
        )


async def withdraw(worker_database: Database, investment_id: str) -> investments.Investment:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.withdraw(session, investment_id, LATER)


async def fund(
    worker_database: Database, investment_id: str, amount: Money, funding_id: str | None = None
) -> investments.Investment:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.pay_funded(
            session, investments.Funded(funding_id or push_id(), investment_id, amount), LATER
        )


async def handed_over(worker_database: Database, investment: investments.Investment) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(
            session,
            payout_posting.funding_account(investment.client_id, investment.id, Currency.UGX),
            FakeClock(),
        )
        return (await ledger.get_balance(session, account.id)).minor


async def interest_owed(worker_database: Database, investment: investments.Investment) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(
            session, accrual.interest_account(investment), FakeClock()
        )
        return (await ledger.get_balance(session, account.id)).minor


async def running_with_interest(
    app_database: Database, worker_database: Database
) -> tuple[investments.Investment, str, int]:
    """A running investment with five days of interest, and an approved destination."""
    investment = await started(app_database, worker_database, NOW)
    await earn(worker_database, investment.id, 5)
    async with worker_database.actor_transaction(SYSTEM) as session:
        investment = await investments.get_investment(session, investment.id)
    destination_id = await approved_destination(
        app_database, Actor("client", investment.client_id), NOW
    )
    return investment, destination_id, investment.accrued_net.minor


async def test_an_early_withdrawal_pays_principal_and_interest_so_far_once_funded(
    app_database: Database, worker_database: Database
) -> None:
    investment, destination_id, earned = await running_with_interest(app_database, worker_database)
    assert earned > 0

    asked = await ask(app_database, investment, destination_id)
    assert asked.status is InvestmentStatus.ACTIVE
    assert asked.withdrawal_requested_at is not None
    client = Actor("client", investment.client_id)
    async with app_database.actor_transaction(client) as session:
        seen = await investments.client_investment(session, client, investment.id, LATER)
    assert (seen.can_withdraw, seen.can_change_maturity) == (False, False)
    assert seen.value == ugx(1_000_000 + earned)

    withdrawn = await withdraw(worker_database, investment.id)
    assert withdrawn.status is InvestmentStatus.WITHDRAWN
    assert await account_balance(worker_database, investment, HOLDING) == 0
    assert await interest_owed(worker_database, investment) == 0
    assert await handed_over(worker_database, investment) == 1_000_000 + earned
    assert await withdraw(worker_database, investment.id) == withdrawn

    funding_id = push_id()
    paid = await fund(worker_database, investment.id, ugx(1_000_000 + earned), funding_id)
    assert paid.withdrawal_payout_id is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        payout = await payouts.get_payout(session, paid.withdrawal_payout_id)
        assert await ledger.find_balance_drift(session) == []
    assert (payout.amount, payout.destination_id, payout.source_reference) == (
        ugx(1_000_000 + earned),
        destination_id,
        investment.id,
    )
    assert await handed_over(worker_database, investment) == 0
    # The desk reporting the same funding twice changes nothing.
    assert await fund(worker_database, investment.id, ugx(1_000_000 + earned), funding_id) == paid

    async with app_database.actor_transaction(new_staff()) as session:
        events = await audit.events_for_resource(session, "investment", investment.id)
    actions = [event.action for event in events]
    assert actions[:2] == ["investments.withdrawn_early", "investments.withdrawal_requested"]


async def test_interest_stops_when_the_client_asks(
    app_database: Database, worker_database: Database
) -> None:
    investment, destination_id, earned = await running_with_interest(app_database, worker_database)
    await ask(app_database, investment, destination_id)

    await earn(worker_database, investment.id, 6)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await accrual.accrue_batch(session, day(7), at(day(7)))
        again = await investments.get_investment(session, investment.id)
    assert again.accrued_net.minor == earned
    assert again.accrued_through == day(5)


async def test_a_destination_approved_today_is_refused(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    fresh = await approved_destination(app_database, Actor("client", investment.client_id), NOW)
    with pytest.raises(ApplicationError) as error:
        await ask(app_database, investment, fresh, clock=NOW)
    assert error.value.fields[0].code == "destination_in_safety_wait"


async def test_a_withdrawal_is_asked_once_by_its_client_for_an_approved_destination(
    app_database: Database, worker_database: Database
) -> None:
    investment, destination_id, _ = await running_with_interest(app_database, worker_database)
    stranger = await approved_client(app_database, NOW)
    someone_elses = await approved_destination(app_database, stranger, NOW)

    with pytest.raises(ApplicationError) as error:
        await ask(app_database, investment, someone_elses)
    assert (error.value.code, error.value.fields[0].code) == ("validation_failed", "not_usable")
    with pytest.raises(ApplicationError) as error:
        await ask(app_database, investment, someone_elses, client=stranger)
    assert error.value.code == "not_found"

    idempotency_key = key()
    first = await ask(app_database, investment, destination_id, idempotency_key=idempotency_key)
    again = await ask(app_database, investment, destination_id, idempotency_key=idempotency_key)
    assert again.withdrawal_requested_at == first.withdrawal_requested_at
    with pytest.raises(ApplicationError) as error:
        await ask(app_database, investment, destination_id)
    assert error.value.code == "conflict"


async def test_a_fixed_term_cannot_be_withdrawn_on_the_day_it_matures(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    destination_id = await approved_destination(
        app_database, Actor("client", investment.client_id), NOW
    )
    with pytest.raises(ApplicationError) as error:
        await ask(app_database, investment, destination_id, clock=at(day(91)))
    assert error.value.code == "conflict"


async def test_two_requests_at_once_withdraw_once(
    app_database: Database, worker_database: Database
) -> None:
    investment, destination_id, _ = await running_with_interest(app_database, worker_database)
    client = Actor("client", investment.client_id)
    barrier = AsyncBarrier(2)

    async def one() -> str:
        async with app_database.actor_transaction(client) as session:
            await barrier.wait()
            try:
                await investments.request_withdrawal(
                    session,
                    client,
                    investment.id,
                    destination_id=destination_id,
                    idempotency_key=key(),
                    clock=LATER,
                )
            except ApplicationError as error:
                return error.code
            return "asked"

    assert sorted(await asyncio.gather(one(), one())) == ["asked", "conflict"]


async def test_the_payout_waits_for_exactly_the_amount_withdrawn(
    app_database: Database, worker_database: Database
) -> None:
    investment, destination_id, earned = await running_with_interest(app_database, worker_database)
    with pytest.raises(ApplicationError) as error:
        await fund(worker_database, investment.id, ugx(1_000_000 + earned))
    assert error.value.code == "conflict"  # not withdrawn yet
    await ask(app_database, investment, destination_id)
    await withdraw(worker_database, investment.id)
    with pytest.raises(ApplicationError) as error:
        await fund(worker_database, investment.id, ugx(1_000_000))
    assert error.value.code == "conflict"
    assert await handed_over(worker_database, investment) == 1_000_000 + earned


async def test_the_database_fences_early_withdrawal(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    destination_id = await approved_destination(
        app_database, Actor("client", investment.client_id), NOW
    )
    # The worker cannot withdraw what nobody asked for, nor ask on the client's behalf.
    with pytest.raises(DBAPIError, match="cannot go from active to withdrawn"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "UPDATE investments SET status = 'withdrawn', withdrawn_at = now(), "
                    "withdrawal_entry_id = :entry WHERE id = :id"
                ),
                {"id": investment.id, "entry": push_id()},
            )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "UPDATE investments SET withdrawal_requested_at = now(), "
                    "withdrawal_destination_id = :destination WHERE id = :id"
                ),
                {"id": investment.id, "destination": destination_id},
            )
    # A client cannot backdate a request, nor mark the investment withdrawn themselves.
    client = Actor("client", investment.client_id)
    with pytest.raises(DBAPIError, match="only the client asks"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text(
                    "UPDATE investments SET withdrawal_requested_at = now() - interval '1 day', "
                    "withdrawal_destination_id = :destination WHERE id = :id"
                ),
                {"id": investment.id, "destination": destination_id},
            )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text("UPDATE investments SET withdrawn_at = now() WHERE id = :id"),
                {"id": investment.id},
            )

    # Once asked, interest and the choice for maturity stop, and a withdrawn one is closed.
    await ask(app_database, investment, destination_id)
    with pytest.raises(DBAPIError, match="interest is added by the daily run"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "UPDATE investments SET accrued_through = :through, "
                    "accrued_gross_minor = 1 WHERE id = :id"
                ),
                {"id": investment.id, "through": day(1)},
            )
    await withdraw(worker_database, investment.id)
    with pytest.raises(DBAPIError, match="a withdrawn investment never changes"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "UPDATE investments SET withdrawal_payout_id = NULL, updated_at = now() "
                    "WHERE id = :id"
                ),
                {"id": investment.id},
            )


async def run_job(
    worker_database: Database, kind: str, payload: dict[str, object]
) -> tuple[str, str | None]:
    """Queue one job as the request or the outbox would, run it, and return how it ended."""
    queue = "wd" + "".join(c for c in push_id().lower() if c.isalnum())
    async with worker_database.actor_transaction(SYSTEM) as session:
        job_id = await enqueue(
            session,
            JobRequest(kind=kind, idempotency_key=push_id(), payload=payload, queue=queue),
            now=LATER.now(),
        )
    runner = Worker(
        worker_database,
        InvestmentJobs(worker_database, LATER).handlers(),
        WorkerConfig(queue=queue),
        LATER,
    )
    assert await runner.run_once() == 1
    async with worker_database.actor_transaction(SYSTEM) as session:
        row = (
            await session.execute(
                text("SELECT status, last_error_code FROM jobs WHERE id = :id"), {"id": job_id}
            )
        ).one()
    return row.status, row.last_error_code


async def test_the_worker_withdraws_then_pays_when_the_desk_reports_funding(
    app_database: Database, worker_database: Database
) -> None:
    investment, destination_id, earned = await running_with_interest(app_database, worker_database)
    await ask(app_database, investment, destination_id)
    # The request queued the withdrawal itself.
    async with worker_database.actor_transaction(SYSTEM) as session:
        queued = (
            await session.execute(
                text("SELECT count(*) FROM jobs WHERE kind = :kind AND idempotency_key = :id"),
                {"kind": WITHDRAW_KIND, "id": investment.id},
            )
        ).scalar_one()
    assert queued == 1

    done = await run_job(worker_database, WITHDRAW_KIND, {"investment_id": investment.id})
    assert done == ("succeeded", None)
    funded = {
        "funding_id": push_id(),
        "investment_id": investment.id,
        "source": "buffer",
        "amount_minor": 1_000_000 + earned,
        "currency": "UGX",
    }
    paid = await run_job(
        worker_database,
        PAY_EARLY_WITHDRAWAL_KIND,
        {"topic": "dealing.withdrawal_funded", "data": funded},
    )
    assert paid == ("succeeded", None)
    async with worker_database.actor_transaction(SYSTEM) as session:
        after = await investments.get_investment(session, investment.id)
    assert after.withdrawal_payout_id is not None

    wrong = {**funded, "funding_id": push_id(), "investment_id": push_id()}
    refused = await run_job(
        worker_database,
        PAY_EARLY_WITHDRAWAL_KIND,
        {"topic": "dealing.withdrawal_funded", "data": wrong},
    )
    assert refused[1] == "investment_not_found"
