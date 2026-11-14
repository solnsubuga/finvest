"""Automatic investing runs end to end against real PostgreSQL: asked for, paid, failed, missed.

The run asks the investments module for an investment and the payments module for a deposit,
the payments worker prompts a fake provider, and the settling sweep records the outcome.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.autoinvest import requests
from app.modules.autoinvest import runs as autoinvest_runs
from app.modules.autoinvest import service as autoinvest
from app.modules.identity import service as identity_service
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import InvestmentStatus
from app.modules.payments import service as payments
from app.modules.payments.domain.deposits import DepositStatus, Network, Purpose
from app.modules.payments.domain.outcomes import Accepted, Confirmed, Rejected, RejectionCode
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.investments import approved_client
from tests.support.ledger import key, ugx
from tests.support.payments import FakeCollections, deposit_worker, drain, phone

pytestmark = pytest.mark.integration

AMOUNT = ugx(200_000)
# The database takes an investment only at the real time, so a run is due today: its weekly
# plan was set up a week ago. The prompt hours are lifted for these tests (`daytime`), except
# where a test sets its own clock to check them.
DUE_DAY = business_date(datetime.now(UTC))
SET_UP = DUE_DAY - timedelta(days=7)


def now() -> FakeClock:
    return FakeClock(datetime.now(UTC))


def at(day: date, hour: int = 9) -> FakeClock:
    """`hour` in Kampala on `day`."""
    return FakeClock(datetime(day.year, day.month, day.day, hour - 3, tzinfo=UTC))


class Contacts:
    """Stands in for identity's `client_contact`: the number on each client's account."""

    def __init__(self) -> None:
        self.phones: dict[str, str] = {}

    async def __call__(
        self, session: AsyncSession, client_id: str
    ) -> identity_service.ClientContact | None:
        found = self.phones.get(client_id)
        return None if found is None else identity_service.ClientContact(found, None, True)


@pytest.fixture
def contacts(monkeypatch: pytest.MonkeyPatch) -> Iterator[Contacts]:
    fake = Contacts()
    monkeypatch.setattr(identity_service, "client_contact", fake)
    yield fake


@pytest.fixture
def daytime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prompts may go at any hour, so a test at night can still ask for a real-time run."""
    monkeypatch.setattr(requests, "may_prompt_at", lambda moment: True)
    monkeypatch.setattr(autoinvest_runs, "may_prompt_at", lambda moment: True)


async def plan_for(
    app_database: Database, contacts: Contacts, *, msisdn: str | None = None
) -> tuple[Actor, autoinvest.Plan, str]:
    """An approved client with a weekly plan due today, paid from their own number."""
    number = msisdn or phone()
    client = await approved_client(app_database, at(SET_UP))
    assert client.id is not None
    contacts.phones[client.id] = number
    async with app_database.actor_transaction(client) as session:
        plan = await autoinvest.create_plan(
            session,
            client,
            autoinvest.PlanTerms(
                product_code="TREA-91",
                amount=AMOUNT,
                schedule=autoinvest.Schedule(autoinvest.Frequency.WEEKLY, DUE_DAY.isoweekday()),
                network=Network.MTN,
                phone=number,
            ),
            idempotency_key=key(),
            account_phone=number,
            clock=at(SET_UP),
        )
    return client, plan, number


async def open_runs(worker_database: Database, clock: FakeClock) -> autoinvest.Opened:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await autoinvest.open_due_runs(session, clock)


async def run_of(worker_database: Database, plan: autoinvest.Plan) -> autoinvest.Run:
    async with worker_database.actor_transaction(SYSTEM) as session:
        [latest, *_] = await autoinvest.plan_runs(session, plan.id)
    return latest


async def ask(worker_database: Database, run_id: str, clock: FakeClock) -> str:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await requests.request_run(session, run_id, provider="fake", clock=clock)


async def settle(worker_database: Database, clock: FakeClock) -> requests.Settled:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await requests.settle_runs(session, clock)


async def deposit_of(worker_database: Database, run: autoinvest.Run) -> payments.Deposit:
    assert run.deposit_id is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await payments.get_deposit(session, run.deposit_id)


async def told(worker_database: Database, client: Actor) -> list[str]:
    async with worker_database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT event FROM notifications WHERE client_id = :id "
                "AND event IN ('investment_started', 'autoinvest_run_retrying', "
                "'autoinvest_run_failed', 'autoinvest_run_missed') ORDER BY id"
            ),
            {"id": client.id},
        )
    return [row.event for row in rows.all()]


async def prompt(
    worker_database: Database, provider: FakeCollections, clock: FakeClock, msisdn: str
) -> None:
    """Let the payments worker send the prompt and read its outcome."""
    worker = deposit_worker(worker_database, provider, clock)
    await drain(worker)
    clock.advance(seconds=10)
    await drain(worker)


# The whole path ---------------------------------------------------------------------------------


async def test_a_due_run_asks_for_its_investment_and_payment_and_is_paid_once(
    app_database: Database,
    worker_database: Database,
    contacts: Contacts,
    daytime: None,
) -> None:
    client, plan, msisdn = await plan_for(app_database, contacts)
    clock = now()
    opened = await open_runs(worker_database, clock)
    run = await run_of(worker_database, plan)
    assert run.id in opened.due and run.status is autoinvest.RunStatus.DUE

    assert await ask(worker_database, run.id, clock) == "requested"
    run = await run_of(worker_database, plan)
    assert run.status is autoinvest.RunStatus.REQUESTED
    assert run.investment_id is not None and run.deposit_id is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        investment = await investments.get_investment(session, run.investment_id)
    assert (investment.client_id, investment.amount) == (client.id, AMOUNT)
    assert investment.status is InvestmentStatus.AWAITING_PAYMENT
    deposit = await deposit_of(worker_database, run)
    assert (deposit.purpose, deposit.purpose_id) == (Purpose.INVESTMENT, investment.id)
    assert (deposit.amount, deposit.payer_msisdn, deposit.status) == (
        AMOUNT,
        msisdn,
        DepositStatus.CREATED,
    )

    # Asking again changes nothing: one investment, one prompt.
    assert await ask(worker_database, run.id, clock) == "not_due"
    assert (await run_of(worker_database, plan)).deposit_id == deposit.id

    provider = FakeCollections()
    provider.on_send(msisdn, Accepted("ref-" + deposit.id))
    provider.on_status(msisdn, Confirmed("ref-" + deposit.id, AMOUNT))
    await prompt(worker_database, provider, clock, msisdn)
    assert provider.sends_for(deposit) == 1
    assert (await deposit_of(worker_database, run)).status is DepositStatus.CONFIRMED

    # Confirmed but not started yet: the run waits for the investment.
    await settle(worker_database, clock)
    assert (await run_of(worker_database, plan)).status is autoinvest.RunStatus.REQUESTED
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(
            session, investment.id, confirmed_on=DUE_DAY, clock=clock
        )
    await settle(worker_database, clock)
    paid = await run_of(worker_database, plan)
    assert paid.status is autoinvest.RunStatus.PAID
    assert "investment_started" in await told(worker_database, client)
    assert not {requests.RUN_FAILED, requests.RUN_RETRYING} & set(
        await told(worker_database, client)
    )


async def test_a_failed_payment_is_tried_once_more_the_next_day_on_the_same_investment(
    app_database: Database,
    worker_database: Database,
    contacts: Contacts,
    daytime: None,
) -> None:
    client, plan, msisdn = await plan_for(app_database, contacts)
    clock = now()
    await open_runs(worker_database, clock)
    first = await run_of(worker_database, plan)
    await ask(worker_database, first.id, clock)
    first = await run_of(worker_database, plan)
    provider = FakeCollections()
    provider.on_send(msisdn, Rejected(RejectionCode.INSUFFICIENT_FUNDS))
    await prompt(worker_database, provider, clock, msisdn)

    await settle(worker_database, clock)
    failed = await run_of(worker_database, plan)
    assert (failed.status, failed.code) == (autoinvest.RunStatus.FAILED, "insufficient_funds")
    assert failed.retry_on == DUE_DAY + timedelta(days=1)
    assert await told(worker_database, client) == [requests.RUN_RETRYING]

    # The next day the same run asks again, for the same investment, with a new prompt.
    tomorrow = FakeClock(clock.now() + timedelta(days=1))
    opened = await open_runs(worker_database, tomorrow)
    assert first.id in opened.due
    assert await ask(worker_database, first.id, tomorrow) == "requested"
    second = await run_of(worker_database, plan)
    assert second.attempt == 2
    assert second.investment_id == first.investment_id
    assert second.deposit_id not in (None, first.deposit_id)

    provider.on_send(msisdn, Rejected(RejectionCode.DECLINED))
    await prompt(worker_database, provider, tomorrow, msisdn)
    await settle(worker_database, tomorrow)
    final = await run_of(worker_database, plan)
    assert (final.status, final.code, final.retry_on) == (
        autoinvest.RunStatus.FAILED,
        "declined",
        None,
    )
    assert await told(worker_database, client) == [requests.RUN_RETRYING, requests.RUN_FAILED]


# What stops a run being asked for -------------------------------------------------------------


async def test_a_run_for_a_number_no_longer_on_the_account_fails_without_a_prompt(
    app_database: Database,
    worker_database: Database,
    contacts: Contacts,
    daytime: None,
) -> None:
    client, plan, _ = await plan_for(app_database, contacts)
    assert client.id is not None
    contacts.phones[client.id] = phone()
    clock = now()
    await open_runs(worker_database, clock)
    run = await run_of(worker_database, plan)
    assert await ask(worker_database, run.id, clock) == "failed"
    failed = await run_of(worker_database, plan)
    assert (failed.status, failed.code, failed.retry_on) == (
        autoinvest.RunStatus.FAILED,
        "phone_changed",
        None,
    )
    assert failed.investment_id is None and failed.deposit_id is None
    assert await told(worker_database, client) == [requests.RUN_FAILED]


async def test_a_run_for_a_locked_or_closed_account_fails_without_a_prompt(
    app_database: Database,
    worker_database: Database,
    contacts: Contacts,
    daytime: None,
) -> None:
    client, plan, _ = await plan_for(app_database, contacts)
    assert client.id is not None
    del contacts.phones[client.id]
    clock = now()
    await open_runs(worker_database, clock)
    run = await run_of(worker_database, plan)
    assert await ask(worker_database, run.id, clock) == "failed"
    assert (await run_of(worker_database, plan)).code == "client_not_active"


async def test_an_investment_the_product_refuses_fails_the_run_for_a_retry(
    app_database: Database,
    worker_database: Database,
    contacts: Contacts,
    daytime: None,
) -> None:
    client, plan, _ = await plan_for(app_database, contacts)
    clock = now()
    await open_runs(worker_database, clock)
    run = await run_of(worker_database, plan)
    # Five investments already wait for payment, the most a client may have.
    for _ in range(5):
        async with app_database.actor_transaction(client) as session:
            await investments.request_investment(
                session,
                client,
                idempotency_key=key(),
                product_code="TREA-91",
                amount=AMOUNT,
                at_maturity=None,
                clock=clock,
            )
    assert await ask(worker_database, run.id, clock) == "failed"
    failed = await run_of(worker_database, plan)
    assert (failed.code, failed.retry_on) == ("investment_conflict", DUE_DAY + timedelta(days=1))
    assert failed.investment_id is None


async def test_nothing_is_asked_for_outside_the_prompt_hours(
    app_database: Database, worker_database: Database, contacts: Contacts
) -> None:
    _, plan, _ = await plan_for(app_database, contacts)
    await open_runs(worker_database, at(DUE_DAY))
    run = await run_of(worker_database, plan)
    assert await ask(worker_database, run.id, at(DUE_DAY, 21)) == "outside_hours"
    assert (await run_of(worker_database, plan)).status is autoinvest.RunStatus.DUE


async def test_a_run_whose_request_was_lost_is_missed_a_day_later_and_the_client_told(
    app_database: Database, worker_database: Database, contacts: Contacts
) -> None:
    client, plan, _ = await plan_for(app_database, contacts)
    await open_runs(worker_database, at(DUE_DAY))
    run = await run_of(worker_database, plan)
    await settle(worker_database, at(DUE_DAY, 19))
    assert (await run_of(worker_database, plan)).status is autoinvest.RunStatus.DUE
    later = FakeClock(at(DUE_DAY).now() + requests.STRANDED_AFTER + timedelta(minutes=1))
    await settle(worker_database, later)
    missed = await run_of(worker_database, plan)
    assert (missed.id, missed.status, missed.code) == (run.id, autoinvest.RunStatus.MISSED, "late")
    assert await told(worker_database, client) == [requests.RUN_MISSED]
    # A request that turns up after all does nothing.
    assert await ask(worker_database, run.id, later) == "not_due"


async def test_a_due_day_long_past_is_missed_and_the_client_told_once(
    app_database: Database, worker_database: Database, contacts: Contacts
) -> None:
    client, plan, _ = await plan_for(app_database, contacts)
    # The system was down from the due day for five days.
    clock = at(DUE_DAY + timedelta(days=5))
    await open_runs(worker_database, clock)
    await open_runs(worker_database, clock)
    missed = await run_of(worker_database, plan)
    assert (missed.due_on, missed.status, missed.code) == (
        DUE_DAY,
        autoinvest.RunStatus.MISSED,
        "late",
    )
    assert await told(worker_database, client) == [requests.RUN_MISSED]


# Races ------------------------------------------------------------------------------------------


async def test_two_workers_asking_for_one_run_make_one_investment_and_one_prompt(
    app_database: Database,
    worker_database: Database,
    contacts: Contacts,
    daytime: None,
) -> None:
    _, plan, _ = await plan_for(app_database, contacts)
    clock = now()
    await open_runs(worker_database, clock)
    run = await run_of(worker_database, plan)
    outcomes = await asyncio.gather(
        ask(worker_database, run.id, clock), ask(worker_database, run.id, clock)
    )
    assert sorted(outcomes) == ["not_due", "requested"]
    asked = await run_of(worker_database, plan)
    async with worker_database.actor_transaction(SYSTEM) as session:
        count = await session.scalar(
            text(
                "SELECT count(*) FROM payment_deposits WHERE purpose_type = 'investment' "
                "AND purpose_id = :id"
            ),
            {"id": asked.investment_id},
        )
        sourced = await session.scalar(
            text(
                "SELECT count(*) FROM investments WHERE source_type = 'autoinvest_run' "
                "AND source_id = :id"
            ),
            {"id": run.id},
        )
    assert (count, sourced) == (1, 1)


# The fences -------------------------------------------------------------------------------------


async def test_the_worker_saves_only_the_start_of_a_mobile_money_deposit_for_an_investment(
    worker_database: Database,
) -> None:
    insert = text(
        "INSERT INTO payment_deposits (id, client_id, purpose_type, purpose_id, method, "
        "provider, currency, amount_minor, payer_msisdn, status, provider_reference, "
        "created_at, updated_at) VALUES (:id, :client, :purpose_type, :purpose, 'mtn', 'fake', "
        "'UGX', 1000, '+256772000000', 'created', :reference, now(), now())"
    )
    async with worker_database.actor_transaction(SYSTEM) as session:
        for purpose_type, reference in (("goal", None), ("investment", "already-sent")):
            with pytest.raises(DBAPIError, match="row-level security"):
                async with session.begin_nested():
                    await session.execute(
                        insert,
                        {
                            "id": push_id(),
                            "client": push_id(),
                            "purpose_type": purpose_type,
                            "purpose": push_id(),
                            "reference": reference,
                        },
                    )
