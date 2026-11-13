"""Sending payouts: sent at most once, read until they end, and what the database refuses."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, enqueue
from app.modules.payments.domain.outcomes import (
    Accepted,
    Confirmed,
    Deferred,
    NotFound,
    Pending,
    Rejected,
    RejectionCode,
    Uncertain,
    UncertainReason,
)
from app.modules.payouts import jobs, payouts
from app.modules.payouts.domain.payouts import PayoutEvent, PayoutStatus
from app.modules.payouts.domain.sending import (
    READ_WINDOW,
    RESEND_AFTER,
    SEND_GRACE,
    AttemptOutcome,
)
from app.modules.payouts.payouts import PayoutView
from app.shared.actors import SYSTEM
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.ledger import staff, ugx
from tests.support.payouts import FakeDisbursements, World, payout_worker

pytestmark = pytest.mark.integration

S = PayoutStatus
RETRY = timedelta(seconds=5)


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime.now(UTC))


@pytest.fixture
def provider() -> FakeDisbursements:
    return FakeDisbursements()


@pytest.fixture
def worker(worker_database: Database, provider: FakeDisbursements, fake_clock: FakeClock) -> Worker:
    return payout_worker(worker_database, provider, fake_clock)


@pytest.fixture
async def pools(test_database: TestDatabase) -> AsyncIterator[dict[str, list[Database]]]:
    """Two more worker pools, for races between independent transactions."""
    made = {"finvest_worker": [Database(test_database.settings("finvest_worker")) for _ in "ab"]}
    yield made
    for database in made["finvest_worker"]:
        await database.close()


@pytest.fixture
async def world(app_database: Database, worker_database: Database, fake_clock: FakeClock) -> World:
    return await World(app_database, worker_database, fake_clock).build(8_000_000)


async def drain(worker: Worker) -> None:
    """Run every job that is due now, including ones those jobs queue for now."""
    while await worker.run_once():
        pass


async def sweep(worker: Worker, database: Database, clock: FakeClock) -> None:
    async with database.actor_transaction(SYSTEM) as session:
        await enqueue(
            session,
            JobRequest(kind=payouts.SWEEP_KIND, idempotency_key=f"sweep-{push_id()}", payload={}),
            now=clock.now(),
        )
    await drain(worker)


async def reload(world: World, payout: PayoutView) -> PayoutView:
    async with world.worker.actor_transaction(SYSTEM) as session:
        return await payouts.get_payout(session, payout.id)


async def attempts(world: World, payout: PayoutView) -> list[str | None]:
    async with world.worker.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text("SELECT outcome FROM payout_send_attempts WHERE payout_id = :id ORDER BY attempt"),
            {"id": payout.id},
        )
        return list(rows.scalars())


async def told(world: World, payout: PayoutView) -> list[str]:
    async with world.worker.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT event FROM notifications WHERE client_id = :client "
                "AND link_id = :id ORDER BY created_at, event"
            ),
            {"client": world.client_id, "id": payout.id},
        )
        return list(rows.scalars())


async def later(clock: FakeClock, worker: Worker, delta: timedelta) -> None:
    clock.advance(seconds=delta.total_seconds())
    await drain(worker)


# The whole path


async def test_a_payout_is_sent_once_read_until_paid_and_settled(
    world: World, provider: FakeDisbursements, worker: Worker
) -> None:
    payout = await world.request(1_000_000)
    provider.on_send(payout.id, Accepted("IOT-1"))
    provider.on_status(payout.id, Pending("IOT-1"), Confirmed("IOT-1", ugx(1_000_000)))
    # Nothing is sent by the request itself: the provider is called only by the worker.
    assert provider.requests == []

    await drain(worker)
    sent = await reload(world, payout)
    assert (sent.status, sent.provider_reference) == (S.SENT, "IOT-1")
    request = provider.requests[-1]
    assert (request.request_id, request.amount, request.network.value) == (
        payout.id,
        ugx(1_000_000),
        "mtn",
    )
    assert request.msisdn.startswith("25677")

    await later(world.clock, worker, timedelta(seconds=10))
    assert (await reload(world, payout)).status is S.SENT  # still with the provider
    await later(world.clock, worker, timedelta(seconds=30))
    paid = await reload(world, payout)
    assert paid.status is S.PAID
    assert await world.balance(payout.in_flight_account_id) == 0
    assert await world.pot_change() == 7_000_000
    assert provider.sends_for(payout.id) == 1
    assert await attempts(world, payout) == ["accepted"]
    assert await told(world, payout) == ["payout_processing", "payout_sent"]
    await world.no_drift()

    # Nothing more is read once it has ended.
    reads = provider.reads.count(payout.id)
    await later(world.clock, worker, timedelta(hours=2))
    await sweep(worker, world.worker, world.clock)
    assert provider.reads.count(payout.id) == reads


async def test_a_refused_send_fails_and_parks_the_money_for_staff(
    world: World, provider: FakeDisbursements, worker: Worker
) -> None:
    payout = await world.request(1_000_000)
    provider.on_send(payout.id, Rejected(RejectionCode.ACCOUNT_NOT_FOUND))
    await drain(worker)
    failed = await reload(world, payout)
    assert (failed.status, failed.failure_code) == (S.FAILED, "account_not_found")
    assert await world.pending() == 1_000_000
    assert await world.owed(payout) == 8_000_000
    assert await attempts(world, payout) == ["rejected"]
    assert await told(world, payout) == ["payout_failed"]


async def test_a_payout_the_provider_fails_after_accepting_is_parked(
    world: World, provider: FakeDisbursements, worker: Worker
) -> None:
    payout = await world.request(1_000_000)
    provider.on_send(payout.id, Accepted("IOT-2"))
    provider.on_status(payout.id, Rejected(RejectionCode.OTHER, "IOT-2"))
    await drain(worker)
    await later(world.clock, worker, timedelta(seconds=10))
    failed = await reload(world, payout)
    assert (failed.status, failed.failure_code) == (S.FAILED, "other")
    assert await world.pending() == 1_000_000


# Deferred: the provider took nothing, so sending again is safe


async def test_a_deferred_send_is_sent_again_later(
    world: World, provider: FakeDisbursements, worker: Worker
) -> None:
    payout = await world.request(1_000_000)
    provider.on_send(payout.id, Deferred(), Accepted("IOT-3"))
    await drain(worker)
    assert (await reload(world, payout)).status is S.QUEUED
    assert await attempts(world, payout) == ["deferred"]
    await later(world.clock, worker, RETRY)
    assert (await reload(world, payout)).status is S.SENT
    assert await attempts(world, payout) == ["deferred", "accepted"]
    assert provider.sends_for(payout.id) == 2


async def test_a_payout_deferred_every_time_stays_queued_and_the_sweep_tries_again(
    world: World, provider: FakeDisbursements, worker: Worker
) -> None:
    payout = await world.request(1_000_000)
    provider.on_send(payout.id, Deferred())
    await drain(worker)
    for _ in range(6):
        await later(world.clock, worker, RETRY)
    queued = await reload(world, payout)
    assert queued.status is S.QUEUED
    assert await world.balance(payout.in_flight_account_id) == 1_000_000
    tries = provider.sends_for(payout.id)
    assert tries == 5
    assert set(await attempts(world, payout)) == {"deferred"}

    provider.on_send(payout.id, Accepted("IOT-4"))
    world.clock.advance(seconds=RESEND_AFTER.total_seconds())
    await sweep(worker, world.worker, world.clock)
    assert (await reload(world, payout)).status is S.SENT
    assert provider.sends_for(payout.id) == tries + 1
    # Sweeping again sends nothing more.
    await sweep(worker, world.worker, world.clock)
    assert provider.sends_for(payout.id) == tries + 1


# Uncertain: it may have gone, so it is only ever read


async def test_a_send_that_times_out_is_never_sent_again_and_a_read_settles_it(
    world: World, provider: FakeDisbursements, worker: Worker
) -> None:
    payout = await world.request(1_000_000)
    provider.on_send(payout.id, Uncertain(UncertainReason.TIMED_OUT))
    provider.on_status(payout.id, NotFound())
    await drain(worker)
    assert (await reload(world, payout)).status is S.UNCERTAIN
    for _ in range(5):
        await later(world.clock, worker, timedelta(minutes=5))
    world.clock.advance(seconds=RESEND_AFTER.total_seconds())
    await sweep(worker, world.worker, world.clock)
    assert (await reload(world, payout)).status is S.UNCERTAIN
    assert provider.sends_for(payout.id) == 1
    assert provider.reads.count(payout.id) >= 3
    assert await world.balance(payout.in_flight_account_id) == 1_000_000
    assert await told(world, payout) == ["payout_processing"]

    # The provider had it after all.
    provider.on_status(payout.id, Confirmed("IOT-5", ugx(1_000_000)))
    await later(world.clock, worker, timedelta(hours=1))
    paid = await reload(world, payout)
    assert (paid.status, paid.provider_reference) == (S.PAID, "IOT-5")
    assert provider.sends_for(payout.id) == 1


async def test_an_adapter_that_raises_is_treated_as_uncertain_and_not_sent_again(
    world: World, provider: FakeDisbursements, worker: Worker
) -> None:
    payout = await world.request(1_000_000)
    provider.on_send(payout.id, ConnectionResetError("reset"))
    await drain(worker)
    assert (await reload(world, payout)).status is S.UNCERTAIN
    await later(world.clock, worker, RETRY)
    assert provider.sends_for(payout.id) == 1
    assert await attempts(world, payout) == ["uncertain"]


async def test_a_send_interrupted_before_its_answer_was_saved_becomes_uncertain(
    world: World, provider: FakeDisbursements, worker: Worker
) -> None:
    payout = await world.request(1_000_000)
    # The worker saved its attempt, called the provider and stopped before saving the answer.
    async with world.worker.actor_transaction(SYSTEM) as session:
        await jobs.start_attempt(session, payout.id, provider.name, world.clock.now())
    await drain(worker)  # the payout's own send job finds the open attempt and sends nothing
    assert provider.sends_for(payout.id) == 0
    assert (await reload(world, payout)).status is S.QUEUED

    world.clock.advance(seconds=SEND_GRACE.total_seconds())
    await sweep(worker, world.worker, world.clock)
    assert (await reload(world, payout)).status is S.UNCERTAIN
    assert await attempts(world, payout) == ["interrupted"]
    world.clock.advance(seconds=RESEND_AFTER.total_seconds())
    await sweep(worker, world.worker, world.clock)
    assert provider.sends_for(payout.id) == 0
    assert provider.reads.count(payout.id) >= 1


async def test_a_confirmation_for_another_amount_is_left_for_staff_and_not_read_again(
    world: World, provider: FakeDisbursements, worker: Worker
) -> None:
    payout = await world.request(1_000_000)
    provider.on_send(payout.id, Accepted("IOT-6"))
    provider.on_status(payout.id, Confirmed("IOT-6", ugx(999_999)))
    await drain(worker)
    await later(world.clock, worker, timedelta(seconds=10))
    reads = provider.reads.count(payout.id)
    await later(world.clock, worker, timedelta(minutes=30))
    assert (await reload(world, payout)).status is S.SENT
    assert provider.reads.count(payout.id) == reads
    assert await world.balance(payout.in_flight_account_id) == 1_000_000


async def test_reads_of_an_unanswered_payout_stop_after_the_read_window(
    world: World, provider: FakeDisbursements, worker: Worker
) -> None:
    payout = await world.request(1_000_000)
    provider.on_send(payout.id, Accepted("IOT-7"))  # and every read is unavailable
    await drain(worker)
    for _ in range(80):
        await later(world.clock, worker, timedelta(hours=1))
    reads = provider.reads.count(payout.id)
    await later(world.clock, worker, timedelta(hours=2))
    await sweep(worker, world.worker, world.clock)
    assert provider.reads.count(payout.id) == reads
    assert world.clock.now() - payout.created_at > READ_WINDOW
    assert (await reload(world, payout)).status is S.SENT


# What gets a send job


async def test_a_bank_payout_is_not_sent_through_the_provider(
    app_database: Database,
    worker_database: Database,
    fake_clock: FakeClock,
    provider: FakeDisbursements,
    worker: Worker,
) -> None:
    world = await World(app_database, worker_database, fake_clock).build(1_000_000, mobile=False)
    payout = await world.request(100_000)
    await drain(worker)
    world.clock.advance(seconds=RESEND_AFTER.total_seconds())
    await sweep(worker, world.worker, world.clock)
    assert provider.sends_for(payout.id) == 0
    assert (await reload(world, payout)).status is S.QUEUED


async def test_two_send_jobs_at_once_send_one_payout_once(
    world: World,
    provider: FakeDisbursements,
    pools: dict[str, list[Database]],
    fake_clock: FakeClock,
) -> None:
    payout = await world.request(1_000_000)
    provider.on_send(payout.id, Accepted("IOT-9"))
    async with world.worker.actor_transaction(SYSTEM) as session:
        await payouts.queue_send(
            session, payout.id, payout.kind, world.clock.now(), key=f"{payout.id}:again"
        )
    first, second = (payout_worker(db, provider, fake_clock) for db in pools["finvest_worker"])
    await asyncio.gather(first.run_once(), second.run_once())
    await asyncio.gather(first.run_once(), second.run_once())
    assert provider.sends_for(payout.id) == 1
    assert (await reload(world, payout)).status is S.SENT
    assert await attempts(world, payout) == ["accepted"]


# What the database refuses


async def test_the_database_allows_one_attempt_that_may_have_gone(world: World) -> None:
    payout = await world.request(1_000_000)
    now = world.clock.now()
    async with world.worker.actor_transaction(SYSTEM) as session:
        first = await jobs.start_attempt(session, payout.id, "fake", now)
    with pytest.raises(DBAPIError):
        async with world.worker.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "INSERT INTO payout_send_attempts (payout_id, attempt, provider, started_at) "
                    "VALUES (:id, 2, 'fake', now())"
                ),
                {"id": payout.id},
            )
    async with world.worker.actor_transaction(SYSTEM) as session:
        assert await jobs.finish_attempt(session, payout.id, first, AttemptOutcome.UNCERTAIN, now)
        # Its end is written once.
        assert not await jobs.finish_attempt(
            session, payout.id, first, AttemptOutcome.DEFERRED, now
        )
    for change in (
        "UPDATE payout_send_attempts SET outcome = 'deferred' WHERE payout_id = :id",
        "UPDATE payout_send_attempts SET provider = 'other' WHERE payout_id = :id",
        "DELETE FROM payout_send_attempts WHERE payout_id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with world.worker.actor_transaction(SYSTEM) as session:
                await session.execute(text(change), {"id": payout.id})
    # An uncertain attempt is not followed by another, even after a deferral would allow one.
    with pytest.raises(DBAPIError):
        async with world.worker.actor_transaction(SYSTEM) as session:
            await jobs.start_attempt(session, payout.id, "fake", now)


async def test_the_database_lets_a_payout_leave_the_queue_only_through_an_attempt(
    world: World,
) -> None:
    payout = await world.request(1_000_000)
    for status in ("sent", "uncertain"):
        with pytest.raises(DBAPIError, match="only through a send attempt"):
            async with world.worker.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text("UPDATE payouts SET status = :status WHERE id = :id"),
                    {"status": status, "id": payout.id},
                )
    # A deferred attempt says nothing went, so it does not let the payout out either.
    async with world.worker.actor_transaction(SYSTEM) as session:
        attempt = await jobs.start_attempt(session, payout.id, "fake", world.clock.now())
        await jobs.finish_attempt(
            session, payout.id, attempt, AttemptOutcome.DEFERRED, world.clock.now()
        )
    with pytest.raises(DBAPIError, match="only through a send attempt"):
        async with world.worker.actor_transaction(SYSTEM) as session:
            await payouts.apply_event(
                session, SYSTEM, payout.id, PayoutEvent.SEND_ACCEPTED, world.clock
            )


async def test_only_the_worker_records_attempts_and_only_for_a_queued_payout(
    world: World,
) -> None:
    payout = await world.request(1_000_000)
    insert = text(
        "INSERT INTO payout_send_attempts (payout_id, attempt, provider, started_at) "
        "VALUES (:id, 1, 'fake', now())"
    )
    for actor in (world.client, staff()):
        with pytest.raises(DBAPIError):
            async with world.app.actor_transaction(actor) as session:
                await session.execute(insert, {"id": payout.id})
    # A finished payout takes no attempt.
    await world.worker_event(payout.id, PayoutEvent.SEND_REJECTED, failure_code="other")
    with pytest.raises(DBAPIError, match="only a queued payout is sent"):
        async with world.worker.actor_transaction(SYSTEM) as session:
            await jobs.start_attempt(session, payout.id, "fake", world.clock.now())
    # Staff can read attempts for the console; a client cannot.
    read = text("SELECT count(*) FROM payout_send_attempts WHERE payout_id = :id")
    async with world.app.actor_transaction(staff()) as session:
        assert await session.scalar(read, {"id": payout.id}) == 1
    async with world.app.actor_transaction(world.client) as session:
        assert await session.scalar(read, {"id": payout.id}) == 0
