"""The daily interest run against real PostgreSQL: what it posts, what it never posts twice,
and who may write its record.

Every test shares one database, so a whole run also pays investments other tests left behind.
Tests therefore check their own investments, and totals only as "at least".
"""

import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, WorkerConfig, enqueue
from app.modules.investments import accrual
from app.modules.investments import service as investments
from app.modules.investments.domain.interest import Accrual, accrued
from app.modules.investments.jobs import ACCRUE_KIND, InvestmentJobs
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.errors import UnknownAccount
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.investments import PRESENT, approved_client, at, pay, request, started, today
from tests.support.ledger import staff as new_staff
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

DAY = today()
NOW = PRESENT


def day(offset: int) -> date:
    return DAY + timedelta(days=offset)


async def pay_interest(
    worker_database: Database, investment_id: str, run_date: date, clock: FakeClock | None = None
) -> Accrual | None:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.accrue_investment(
            session, investment_id, run_date, clock or at(run_date)
        )


async def whole_run(
    worker_database: Database, run_date: date, clock: FakeClock | None = None
) -> tuple[int, int]:
    """Every batch of a run, each in its own transaction. Returns (posted, failed)."""
    posted = failed = 0
    after = ""
    while True:
        async with worker_database.actor_transaction(SYSTEM) as session:
            batch = await accrual.accrue_batch(
                session, run_date, clock or at(run_date), after=after, limit=50
            )
        if batch.last_id is None:
            return posted, failed
        after = batch.last_id
        posted += batch.posted
        failed += batch.failed


async def reload(worker_database: Database, investment_id: str) -> investments.Investment:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.get_investment(session, investment_id)


async def system_balance(worker_database: Database, code: str) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        try:
            account_id = await ledger.system_account_id(session, code, Currency.UGX)
        except UnknownAccount:
            return 0
        return (await ledger.get_balance(session, account_id)).minor


async def interest_owed(worker_database: Database, investment: investments.Investment) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(
            session, accrual.interest_account(investment), FakeClock()
        )
        return (await ledger.get_balance(session, account.id)).minor


async def entries_for(worker_database: Database, investment_id: str) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        count = await session.execute(
            text(
                "SELECT count(*) FROM ledger_journal_entries WHERE source_type = 'investment' "
                "AND source_id = :id AND entry_type = 'investment.interest_accrued'"
            ),
            {"id": investment_id},
        )
        return int(count.scalar_one())


# One investment ---------------------------------------------------------------------------------


async def test_a_day_of_interest_is_one_entry_split_between_the_client_and_tax(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    expense = await system_balance(worker_database, accrual.INTEREST_EXPENSE)
    tax = await system_balance(worker_database, accrual.TAX_PAYABLE)

    posted = await pay_interest(worker_database, investment.id, day(1))

    assert posted == Accrual(ugx(342), ugx(51))
    assert await interest_owed(worker_database, investment) == 291
    assert await system_balance(worker_database, accrual.INTEREST_EXPENSE) == expense + 342
    assert await system_balance(worker_database, accrual.TAX_PAYABLE) == tax + 51
    after = await reload(worker_database, investment.id)
    assert (after.accrued_through, after.accrued_gross, after.accrued_tax) == (
        day(1),
        ugx(342),
        ugx(51),
    )
    assert await entries_for(worker_database, investment.id) == 1


async def test_nothing_is_earned_on_the_day_an_investment_starts(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    assert await pay_interest(worker_database, investment.id, DAY) is None
    assert (await reload(worker_database, investment.id)).accrued_through is None
    assert await entries_for(worker_database, investment.id) == 0


async def test_a_day_is_never_paid_twice(app_database: Database, worker_database: Database) -> None:
    investment = await started(app_database, worker_database, NOW)
    await pay_interest(worker_database, investment.id, day(1))
    assert await pay_interest(worker_database, investment.id, day(1)) is None
    assert await interest_owed(worker_database, investment) == 291
    assert await entries_for(worker_database, investment.id) == 1


async def test_a_run_after_missed_days_pays_them_together(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    await pay_interest(worker_database, investment.id, day(1))
    await pay_interest(worker_database, investment.id, day(5))
    five_days = accrued(investment.terms, 5)
    after = await reload(worker_database, investment.id)
    assert (after.accrued_through, after.accrued_gross, after.accrued_tax) == (
        day(5),
        five_days.gross,
        five_days.tax,
    )
    assert await interest_owed(worker_database, investment) == five_days.net.minor
    assert await entries_for(worker_database, investment.id) == 2


async def test_an_older_run_arriving_late_posts_nothing(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    await pay_interest(worker_database, investment.id, day(3))
    assert await pay_interest(worker_database, investment.id, day(2), at(day(3))) is None
    assert (await reload(worker_database, investment.id)).accrued_through == day(3)


async def test_a_whole_term_of_daily_runs_pays_exactly_the_formula_and_stops_at_maturity(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    for offset in range(1, 96):
        await pay_interest(worker_database, investment.id, day(offset))
    after = await reload(worker_database, investment.id)
    assert after.accrued_through == after.maturity_date == day(91)
    assert after.accrued_net == ugx(26_490)
    assert await interest_owed(worker_database, investment) == 26_490
    assert await entries_for(worker_database, investment.id) == 91


async def test_interest_is_not_paid_for_a_day_that_has_not_come(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    with pytest.raises(ApplicationError) as error:
        await pay_interest(worker_database, investment.id, day(2), at(day(1)))
    assert error.value.code == "conflict"
    with pytest.raises(ApplicationError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await accrual.accrue_batch(session, day(2), at(day(1)))


async def test_an_unpaid_investment_earns_nothing(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    unpaid = await request(app_database, client, NOW)
    assert await pay_interest(worker_database, unpaid.id, day(1)) is None
    await whole_run(worker_database, day(1))
    assert (await reload(worker_database, unpaid.id)).accrued_through is None


# The whole run ----------------------------------------------------------------------------------


async def test_a_run_pays_every_running_investment_once(
    app_database: Database, worker_database: Database
) -> None:
    first = await started(app_database, worker_database, NOW)
    second = await started(app_database, worker_database, NOW, amount=ugx(250_000))

    posted, failed = await whole_run(worker_database, day(1))
    again, _ = await whole_run(worker_database, day(1))

    assert posted >= 2
    assert (failed, again) == (0, 0)
    assert (await reload(worker_database, first.id)).accrued_gross == ugx(342)
    assert (await reload(worker_database, second.id)).accrued_gross == ugx(85)
    assert await entries_for(worker_database, first.id) == 1
    assert await entries_for(worker_database, second.id) == 1


async def test_two_workers_on_the_same_run_pay_each_investment_once(
    app_database: Database, worker_database: Database
) -> None:
    held = [await started(app_database, worker_database, NOW) for _ in range(6)]
    barrier = AsyncBarrier(2)

    async def run() -> int:
        posted = 0
        after = ""
        first = True
        while True:
            async with worker_database.actor_transaction(SYSTEM) as session:
                if first:
                    await barrier.wait()
                    first = False
                batch = await accrual.accrue_batch(
                    session, day(1), at(day(1)), after=after, limit=2
                )
                # Give the other worker time to meet the rows this one holds.
                await asyncio.sleep(0.01)
            if batch.last_id is None:
                return posted
            after = batch.last_id
            posted += batch.posted

    await asyncio.gather(run(), run())
    # A worker skips what the other holds, so a final pass picks up anything both stepped over.
    await whole_run(worker_database, day(1))

    for investment in held:
        after = await reload(worker_database, investment.id)
        assert (after.accrued_through, after.accrued_gross) == (day(1), ugx(342))
        assert await interest_owed(worker_database, investment) == 291
        assert await entries_for(worker_database, investment.id) == 1
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await ledger.find_balance_drift(session) == []


async def test_one_investment_that_cannot_be_paid_does_not_stop_the_rest(
    app_database: Database, worker_database: Database
) -> None:
    broken = await started(app_database, worker_database, NOW)
    fine = await started(app_database, worker_database, NOW)
    async with worker_database.actor_transaction(SYSTEM) as session:
        # More interest on the record than the terms can have earned by the next day.
        await session.execute(
            text(
                "UPDATE investments SET accrued_through = :through, accrued_gross_minor = 90000 "
                "WHERE id = :id"
            ),
            {"through": day(1), "id": broken.id},
        )

    _, failed = await whole_run(worker_database, day(2))
    async with worker_database.actor_transaction(SYSTEM) as session:
        behind = await accrual.finish_run(session, day(2), at(day(2)))

    assert failed == 1
    assert behind == {"UGX": 1}
    assert (await reload(worker_database, fine.id)).accrued_through == day(2)
    stuck = await reload(worker_database, broken.id)
    assert (stuck.accrued_through, stuck.accrued_gross) == (day(1), ugx(90_000))
    assert await entries_for(worker_database, broken.id) == 0
    # Leave nothing behind for the tests that follow: later runs would count it again.
    async with worker_database.actor_transaction(SYSTEM) as session:
        await session.execute(
            text("UPDATE investments SET accrued_through = :through WHERE id = :id"),
            {"through": day(4000), "id": broken.id},
        )


# The scheduled job ------------------------------------------------------------------------------


async def run_job(worker_database: Database, scheduled_for: str, clock: FakeClock) -> str:
    queue = "inv" + "".join(c for c in push_id().lower() if c.isalnum())
    async with worker_database.actor_transaction(SYSTEM) as session:
        job_id = await enqueue(
            session,
            JobRequest(
                kind=ACCRUE_KIND,
                idempotency_key=f"investments.accrue_interest@{push_id()}",
                payload={"scheduled_for": scheduled_for, "data": {}},
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
    return f"{row.status}:{row.last_error_code}"


async def test_the_scheduled_job_pays_the_day_it_was_scheduled_for_and_records_the_run(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    # Fired at 00:30 in Kampala on the second day, which is 21:30 UTC the day before, and
    # run late.
    outcome = await run_job(worker_database, f"{day(1).isoformat()}T21:30:00+00:00", at(day(2), 12))

    assert outcome == "succeeded:None"
    after = await reload(worker_database, investment.id)
    assert (after.accrued_through, after.accrued_gross) == (day(2), ugx(684))
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        runs = await investments.interest_runs(session, staff, limit=200)
    (run,) = [r for r in runs if r.run_date == day(2) and r.currency is Currency.UGX]
    assert run.investments >= 1
    assert run.gross >= ugx(684)
    assert run.net == run.gross - run.tax
    assert run.behind == 0
    assert run.finished_at == at(day(2), 12).now()
    assert [r.currency for r in runs if r.run_date == day(2)] == [Currency.UGX, Currency.USD]


async def test_a_job_for_a_day_that_has_not_come_fails_for_good(
    worker_database: Database,
) -> None:
    outcome = await run_job(worker_database, f"{day(7).isoformat()}T21:30:00+00:00", NOW)
    assert outcome == "failed:interest_run_conflict"


# What people see and who may write --------------------------------------------------------------


async def test_the_client_sees_interest_after_tax(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    await pay_interest(worker_database, investment.id, day(1))
    await pay_interest(worker_database, investment.id, day(2))
    client = Actor("client", investment.client_id)
    async with app_database.actor_transaction(client) as session:
        today = await investments.client_investment(session, client, investment.id, at(day(2)))
        tomorrow = await investments.client_investment(session, client, investment.id, at(day(3)))
    assert (today.days_earned, today.interest_so_far, today.interest_today) == (
        2,
        ugx(582),
        ugx(291),
    )
    assert today.value == ugx(1_000_582)
    # Before the next day's run, nothing has been added yet that day.
    assert (tomorrow.interest_so_far, tomorrow.interest_today) == (ugx(582), ugx(0))


async def test_only_staff_read_the_record_of_runs(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, NOW)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await accrual.finish_run(session, day(1), at(day(1)))
    client = Actor("client", investment.client_id)
    async with app_database.actor_transaction(client) as session:
        with pytest.raises(ApplicationError) as error:
            await investments.interest_runs(session, client)
        rows = await session.execute(text("SELECT count(*) FROM investment_interest_runs"))
        assert rows.scalar_one() == 0
    assert error.value.code == "forbidden"


async def test_nobody_in_the_api_writes_the_record_of_runs(app_database: Database) -> None:
    staff = new_staff()
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "INSERT INTO investment_interest_runs (run_date, currency, started_at) "
                    "VALUES (DATE '2031-01-01', 'UGX', now())"
                )
            )


async def test_the_record_of_a_run_only_grows(worker_database: Database) -> None:
    async with worker_database.actor_transaction(SYSTEM) as session:
        await accrual.finish_run(session, day(1), at(day(1)))
    with pytest.raises(DBAPIError, match="the record of an interest run only grows"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "UPDATE investment_interest_runs SET investments = investments - 1, "
                    "gross_minor = gross_minor + 5 WHERE run_date = :day AND investments > 0"
                ),
                {"day": day(1)},
            )
            await session.execute(
                text("UPDATE investment_interest_runs SET gross_minor = -1 WHERE run_date = :day"),
                {"day": day(1)},
            )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(text("DELETE FROM investment_interest_runs"))


# Telling clients --------------------------------------------------------------------------------


async def earnings_told(app_database: Database, client: Actor) -> list[tuple[str, str]]:
    async with app_database.actor_transaction(client) as session:
        rows = await session.execute(
            text(
                "SELECT title, body FROM notifications "
                "WHERE client_id = :client AND event = 'interest_earned'"
            ),
            {"client": client.id},
        )
        return [(row.title, row.body) for row in rows.all()]


async def test_the_run_tells_each_client_once_what_the_day_earned_after_tax(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, NOW)
    assert client.id is not None
    # Just this client: every test shares the database.
    just_them = client.id[:-1]
    for _ in range(2):
        investment = await request(app_database, client, NOW)
        await pay(worker_database, investment, investment.amount, NOW)
        async with worker_database.actor_transaction(SYSTEM) as session:
            await investments.start_investment(session, investment.id, confirmed_on=DAY, clock=NOW)
    # Nothing is earned on the start date, so nobody is told then.
    async with worker_database.actor_transaction(SYSTEM) as session:
        await accrual.tell_earnings(session, DAY, at(DAY, 12), after=just_them, limit=1)
    assert await earnings_told(app_database, client) == []

    outcome = await run_job(worker_database, f"{DAY.isoformat()}T21:30:00+00:00", at(day(1), 12))

    assert outcome == "succeeded:None"
    # 291 a day each after tax, as the app shows it.
    assert await earnings_told(app_database, client) == [
        ("You earned UGX 582 today", "Interest across 2 investments, after tax."),
    ]
    # Asked again for the same day, nobody is told twice.
    async with worker_database.actor_transaction(SYSTEM) as session:
        again = await accrual.tell_earnings(
            session, day(1), at(day(1), 12), after=just_them, limit=1
        )
    assert again.told == 0
    assert len(await earnings_told(app_database, client)) == 1
