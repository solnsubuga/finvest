"""The job queue, outbox and scheduler against real PostgreSQL, as the real roles.

Races use separate connections and a barrier. Time is a fake clock, so leases expire when a test
says so.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import timedelta

import pytest
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import (
    ClaimedJob,
    Consumer,
    Handler,
    JobContext,
    JobHandler,
    JobRequest,
    LeaderLease,
    LeadershipLostError,
    PermanentJobError,
    RetryableJobError,
    RetryPolicy,
    Schedule,
    ScheduleDispatcher,
    SchedulerService,
    StaleLeaseError,
    UncertainOutcomeError,
    Worker,
    WorkerConfig,
    enqueue,
    external_effect,
    publish,
)
from app.infrastructure.jobs import queue as job_queue
from app.infrastructure.jobs.scheduler import instance_key
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

LEASE = timedelta(seconds=30)
SECRET_TEXT = "0772123456 owes 5,000,000"


@dataclass(frozen=True)
class Names:
    """Names unique to one test, so tests share a database without seeing each other's jobs."""

    suffix: str

    @property
    def queue(self) -> str:
        return f"q_{self.suffix}"

    def kind(self, name: str) -> str:
        return f"test.{name}_{self.suffix}"


@pytest.fixture
def names() -> Names:
    return Names("".join(c for c in push_id().lower() if c.isalnum())[-12:])


@pytest.fixture
async def pools(test_database: TestDatabase) -> AsyncIterator[list[Database]]:
    """Four more worker connections pools, for races between independent transactions."""
    databases = [Database(test_database.settings("finvest_worker")) for _ in range(4)]
    yield databases
    for database in databases:
        await database.close()


async def scalar(database: Database, sql: str, **parameters: object) -> object:
    async with database.transaction() as session:
        return await session.scalar(text(sql), parameters)


async def job_row(database: Database, job_id: str) -> Mapping[str, object]:
    async with database.transaction() as session:
        row = await session.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id})
        return dict(row.one()._mapping)


async def attempts(database: Database, job_id: str) -> list[tuple[int, str | None, str | None]]:
    async with database.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT attempt, outcome, error_code FROM job_attempts "
                "WHERE job_id = :id ORDER BY attempt"
            ),
            {"id": job_id},
        )
        return [(row.attempt, row.outcome, row.error_code) for row in rows]


async def add_job(database: Database, clock: FakeClock, request: JobRequest) -> str:
    async with database.transaction() as session:
        job_id = await enqueue(session, request, now=clock.now())
    assert job_id is not None
    return job_id


Effect = Callable[[AsyncSession], Awaitable[Mapping[str, object]]]


def effect(names: Names, key: str, clock: FakeClock) -> Effect:
    """A database result to apply: one outbox event, so tests can count how often it happened."""

    async def apply(session: AsyncSession) -> Mapping[str, object]:
        event_id = await publish(
            session,
            topic=names.kind("effect"),
            idempotency_key=key,
            payload={},
            consumers=[],
            now=clock.now(),
        )
        return {"event_id": event_id}

    return apply


async def effects(database: Database, names: Names) -> object:
    return await scalar(
        database, "SELECT count(*) FROM outbox_events WHERE topic = :t", t=names.kind("effect")
    )


def worker(
    database: Database,
    names: Names,
    handlers: Mapping[str, JobHandler | Handler],
    clock: FakeClock,
    *,
    worker_id: str = "a",
) -> Worker:
    config = WorkerConfig(
        queue=names.queue,
        worker_id=worker_id,
        lease=LEASE,
        retry=RetryPolicy(base=timedelta(seconds=10), cap=timedelta(minutes=1)),
    )
    return Worker(database, handlers, config, clock)


# Enqueueing and the outbox


async def test_a_job_commits_or_rolls_back_with_the_business_transaction(
    app_database: Database, worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    request = JobRequest(
        kind=names.kind("send"), idempotency_key="k", payload={}, queue=names.queue
    )
    with pytest.raises(RuntimeError, match="business change failed"):
        async with app_database.transaction() as session:
            assert await enqueue(session, request, now=fake_clock.now()) is not None
            raise RuntimeError("business change failed")
    count = "SELECT count(*) FROM jobs WHERE queue = :q"
    assert await scalar(worker_database, count, q=names.queue) == 0

    async with app_database.transaction() as session:
        job_id = await enqueue(session, request, now=fake_clock.now())
    assert (await job_row(worker_database, job_id or ""))["status"] == "pending"


async def test_an_equal_active_job_is_not_added_twice(
    app_database: Database, worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    request = JobRequest(
        kind=names.kind("send"), idempotency_key="k", payload={}, queue=names.queue
    )
    await add_job(app_database, fake_clock, request)
    async with app_database.transaction() as session:
        assert await enqueue(session, request, now=fake_clock.now()) is None
        other = replace(request, idempotency_key="other")
        assert await enqueue(session, other, now=fake_clock.now()) is not None
    assert (
        await scalar(worker_database, "SELECT count(*) FROM jobs WHERE queue = :q", q=names.queue)
        == 2
    )


async def test_concurrent_enqueues_of_one_key_create_one_job(
    worker_database: Database, pools: list[Database], names: Names, fake_clock: FakeClock
) -> None:
    request = JobRequest(
        kind=names.kind("send"), idempotency_key="k", payload={}, queue=names.queue
    )
    barrier = AsyncBarrier(len(pools))

    async def add(database: Database) -> str | None:
        async with database.transaction() as session:
            await barrier.wait()
            return await enqueue(session, request, now=fake_clock.now())

    results = await asyncio.gather(*(add(database) for database in pools))
    assert len([job_id for job_id in results if job_id is not None]) == 1
    assert (
        await scalar(worker_database, "SELECT count(*) FROM jobs WHERE queue = :q", q=names.queue)
        == 1
    )


async def test_an_event_reaches_each_consumer_once_however_often_it_is_published(
    app_database: Database, worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    consumers = [
        Consumer(names.kind("start_investment"), queue=names.queue),
        Consumer(names.kind("notify"), queue=names.queue, max_attempts=3),
    ]

    async def confirm() -> str | None:
        async with app_database.transaction() as session:
            return await publish(
                session,
                topic=names.kind("payment.confirmed"),
                idempotency_key="payment-1",
                payload={"payment_id": "payment-1"},
                consumers=consumers,
                now=fake_clock.now(),
            )

    event_id = await confirm()
    assert event_id is not None
    assert await confirm() is None
    async with worker_database.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT kind, event_id, idempotency_key, max_attempts, payload->>'event_id' AS e "
                "FROM jobs WHERE queue = :q ORDER BY kind"
            ),
            {"q": names.queue},
        )
        jobs = rows.all()
    assert [job.kind for job in jobs] == sorted(consumer.kind for consumer in consumers)
    assert {job.event_id for job in jobs} == {event_id}
    assert {job.e for job in jobs} == {event_id}
    assert {job.idempotency_key for job in jobs} == {f"{names.kind('payment.confirmed')}:payment-1"}


async def test_the_api_role_can_add_work_and_nothing_else(
    app_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    job_id = await add_job(
        app_database,
        fake_clock,
        JobRequest(kind=names.kind("send"), idempotency_key="k", payload={}, queue=names.queue),
    )
    for statement in (
        "SELECT payload FROM jobs",
        "SELECT last_error_message, lease_owner FROM jobs",
        "UPDATE jobs SET status = 'succeeded' WHERE id = :id",
        "DELETE FROM jobs WHERE id = :id",
        "SELECT payload FROM outbox_events",
        "SELECT count(*) FROM consumer_receipts",
        "INSERT INTO scheduler_leadership VALUES ('x', 'x', 1, now(), now(), now())",
        "SELECT * FROM finvest_claim_jobs('default', ARRAY['x'], ARRAY[]::text[], 'w', now(), "
        "interval '30s', 1)",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_database.transaction() as session:
                await session.execute(text(statement), {"id": job_id})


@pytest.mark.parametrize(
    "table", ["jobs", "job_attempts", "outbox_events", "consumer_receipts", "schedule_instances"]
)
async def test_nobody_can_delete_job_history(worker_database: Database, table: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.transaction() as session:
            await session.execute(text(f"DELETE FROM {table}"))  # noqa: S608


# Claims, leases and fencing


async def test_concurrent_claims_lease_each_job_exactly_once(
    worker_database: Database, pools: list[Database], names: Names, fake_clock: FakeClock
) -> None:
    kind = names.kind("race")
    for index in range(24):
        await add_job(
            worker_database,
            fake_clock,
            JobRequest(kind=kind, idempotency_key=f"race-{index}", payload={}, queue=names.queue),
        )
    barrier = AsyncBarrier(len(pools))

    async def claim_all(database: Database, name: str) -> list[ClaimedJob]:
        claimed: list[ClaimedJob] = []
        await barrier.wait()
        while batch := await job_queue.claim(
            database,
            queue=names.queue,
            kinds=[kind],
            worker=name,
            now=fake_clock.now(),
            lease=LEASE,
            limit=3,
        ):
            claimed += batch
        return claimed

    results = await asyncio.gather(
        *(claim_all(database, f"claimant-{index}") for index, database in enumerate(pools))
    )
    ids = [job.id for batch in results for job in batch]
    assert len(ids) == 24
    assert len(set(ids)) == 24
    assert {job.fencing_token for batch in results for job in batch} == {1}


async def test_a_worker_applies_its_result_once_with_the_completion(
    worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    kind = names.kind("apply")
    results: list[Mapping[str, object]] = []

    async def handler(job: ClaimedJob, context: JobContext) -> None:
        results.append(await context.apply(effect(names, job.idempotency_key, fake_clock)))

    job_id = await add_job(
        worker_database,
        fake_clock,
        JobRequest(kind=kind, idempotency_key="once", payload={}, queue=names.queue),
    )
    assert await worker(worker_database, names, {kind: handler}, fake_clock).run_once() == 1

    row = await job_row(worker_database, job_id)
    assert (row["status"], row["attempts"], row["lease_owner"]) == ("succeeded", 1, None)
    assert await attempts(worker_database, job_id) == [(1, "succeeded", None)]
    assert await effects(worker_database, names) == 1
    assert await worker(worker_database, names, {kind: handler}, fake_clock).run_once() == 0


async def test_a_stalled_workers_late_result_is_fenced_out(
    worker_database: Database, pools: list[Database], names: Names, fake_clock: FakeClock
) -> None:
    kind = names.kind("stall")
    stalled, resume = asyncio.Event(), asyncio.Event()
    late: list[BaseException] = []

    async def slow(job: ClaimedJob, context: JobContext) -> None:
        stalled.set()
        await resume.wait()
        try:
            await context.apply(effect(names, "stalled", fake_clock))
        except StaleLeaseError as error:
            late.append(error)
            raise

    async def fast(job: ClaimedJob, context: JobContext) -> None:
        await context.apply(effect(names, "recovered", fake_clock))

    job_id = await add_job(
        worker_database,
        fake_clock,
        JobRequest(kind=kind, idempotency_key="stall", payload={}, queue=names.queue),
    )
    first = asyncio.create_task(worker(pools[0], names, {kind: slow}, fake_clock).run_once())
    await asyncio.wait_for(stalled.wait(), 5)
    fake_clock.advance(seconds=LEASE.total_seconds() + 1)
    second = worker(worker_database, names, {kind: fast}, fake_clock, worker_id="b")
    assert await second.run_once() == 1
    resume.set()
    assert await first == 1

    assert len(late) == 1
    row = await job_row(worker_database, job_id)
    assert (row["status"], row["fencing_token"]) == ("succeeded", 2)
    assert await attempts(worker_database, job_id) == [
        (1, "lease_expired", "lease_expired"),
        (2, "succeeded", None),
    ]
    assert await effects(worker_database, names) == 1
    stale = "SELECT count(*) FROM outbox_events WHERE topic = :t AND idempotency_key = 'stalled'"
    assert await scalar(worker_database, stale, t=names.kind("effect")) == 0


async def test_a_crash_inside_the_result_transaction_rolls_back_and_retries(
    worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    kind = names.kind("rollback")
    calls = 0

    async def handler(job: ClaimedJob, context: JobContext) -> None:
        nonlocal calls
        calls += 1

        async def apply(session: AsyncSession) -> Mapping[str, object]:
            result = await effect(names, "result", fake_clock)(session)
            if calls == 1:
                raise RuntimeError(SECRET_TEXT)
            return result

        await context.apply(apply)

    job_id = await add_job(
        worker_database,
        fake_clock,
        JobRequest(kind=kind, idempotency_key="rollback", payload={}, queue=names.queue),
    )
    runner = worker(worker_database, names, {kind: handler}, fake_clock)
    assert await runner.run_once() == 1
    assert await effects(worker_database, names) == 0
    row = await job_row(worker_database, job_id)
    assert (row["status"], row["last_error_code"]) == ("pending", "unhandled_error")
    # Arbitrary exception text never reaches the database: only the type is kept.
    assert row["last_error_message"] == "RuntimeError"

    fake_clock.advance(minutes=2)
    assert await runner.run_once() == 1
    assert await effects(worker_database, names) == 1
    assert await attempts(worker_database, job_id) == [
        (1, "retry", "unhandled_error"),
        (2, "succeeded", None),
    ]


async def test_a_second_job_for_the_same_delivery_replays_the_receipt(
    worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    kind = names.kind("replay")
    results: list[Mapping[str, object]] = []
    applied = 0

    async def handler(job: ClaimedJob, context: JobContext) -> None:
        async def apply(session: AsyncSession) -> Mapping[str, object]:
            nonlocal applied
            applied += 1
            return await effect(names, "delivery", fake_clock)(session)

        results.append(await context.apply(apply))

    request = JobRequest(kind=kind, idempotency_key="delivery", payload={}, queue=names.queue)
    runner = worker(worker_database, names, {kind: handler}, fake_clock)
    await add_job(worker_database, fake_clock, request)
    assert await runner.run_once() == 1
    await add_job(worker_database, fake_clock, request)  # the first has finished, so this is new
    assert await runner.run_once() == 1

    assert applied == 1
    assert results[0] == results[1]
    assert await effects(worker_database, names) == 1


async def test_external_work_runs_with_no_transaction_open(
    worker_database: Database, test_database: TestDatabase, names: Names, fake_clock: FakeClock
) -> None:
    # One connection only: if the worker held a transaction during the handler, the handler's
    # own database call would wait for a connection that never comes back.
    single = Database(test_database.settings("finvest_worker", pool_size=1))
    kind = names.kind("external")

    async def handler(job: ClaimedJob, context: JobContext) -> None:
        async with asyncio.timeout(2):
            assert await single.ready()

    try:
        job_id = await add_job(
            single,
            fake_clock,
            JobRequest(kind=kind, idempotency_key="external", payload={}, queue=names.queue),
        )
        assert await worker(single, names, {kind: handler}, fake_clock).run_once() == 1
    finally:
        await single.close()
    assert (await job_row(worker_database, job_id))["status"] == "succeeded"


async def test_a_heartbeat_extends_the_lease_and_fails_once_lost(
    worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    kind = names.kind("beat")
    outcomes: list[str] = []

    async def handler(job: ClaimedJob, context: JobContext) -> None:
        fake_clock.advance(seconds=20)
        await context.heartbeat()
        fake_clock.advance(seconds=20)  # 40s after the claim, inside the extended lease
        await context.heartbeat()
        outcomes.append("extended")
        fake_clock.advance(seconds=LEASE.total_seconds() + 1)
        try:
            await context.heartbeat()
        except StaleLeaseError:
            outcomes.append("lost")
            raise

    job_id = await add_job(
        worker_database,
        fake_clock,
        JobRequest(kind=kind, idempotency_key="beat", payload={}, queue=names.queue),
    )
    assert await worker(worker_database, names, {kind: handler}, fake_clock).run_once() == 1
    assert outcomes == ["extended", "lost"]
    # The worker that lost the lease records nothing. The expired lease is there to be claimed.
    assert (await job_row(worker_database, job_id))["status"] == "running"


# Failure handling


async def test_retries_back_off_then_fail_and_an_operator_can_requeue(
    worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    kind = names.kind("flaky")
    healthy = False

    async def handler(job: ClaimedJob, context: JobContext) -> None:
        if not healthy:
            raise RetryableJobError("provider unavailable", code="provider_unavailable")

    job_id = await add_job(
        worker_database,
        fake_clock,
        JobRequest(kind=kind, idempotency_key="f", payload={}, queue=names.queue, max_attempts=3),
    )
    runner = worker(worker_database, names, {kind: handler}, fake_clock)
    assert await runner.run_once() == 1
    row = await job_row(worker_database, job_id)
    assert (row["status"], row["last_error_message"]) == ("pending", "provider unavailable")
    wait = row["run_at"] - fake_clock.now()  # type: ignore[operator]
    assert timedelta(seconds=5) <= wait <= timedelta(seconds=10)
    assert await runner.run_once() == 0  # not due yet

    for _ in range(2):
        fake_clock.advance(minutes=2)
        assert await runner.run_once() == 1
    row = await job_row(worker_database, job_id)
    assert (row["status"], row["attempts"]) == ("failed", 3)
    assert await attempts(worker_database, job_id) == [
        (1, "retry", "provider_unavailable"),
        (2, "retry", "provider_unavailable"),
        (3, "failed", "provider_unavailable"),
    ]

    async with worker_database.transaction() as session:
        assert next(job.id for job in await job_queue.failed_jobs(session)) == job_id
        assert await job_queue.requeue_failed(session, job_id, now=fake_clock.now())
        assert not await job_queue.requeue_failed(session, job_id, now=fake_clock.now())
    healthy = True
    assert await runner.run_once() == 1
    row = await job_row(worker_database, job_id)
    assert (row["status"], row["attempts"], row["max_attempts"]) == ("succeeded", 4, 6)


async def test_a_rejection_fails_at_once(
    worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    kind = names.kind("rejected")

    async def handler(job: ClaimedJob, context: JobContext) -> None:
        raise PermanentJobError("the provider refused the request", code="provider_rejected")

    job_id = await add_job(
        worker_database,
        fake_clock,
        JobRequest(kind=kind, idempotency_key="r", payload={}, queue=names.queue),
    )
    assert await worker(worker_database, names, {kind: handler}, fake_clock).run_once() == 1
    row = await job_row(worker_database, job_id)
    assert (row["status"], row["attempts"], row["last_error_code"]) == (
        "failed",
        1,
        "provider_rejected",
    )


async def test_an_uncertain_outcome_is_never_retried_or_requeued(
    worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    kind = names.kind("payout")
    sent = 0

    async def handler(job: ClaimedJob, context: JobContext) -> None:
        nonlocal sent
        sent += 1
        raise UncertainOutcomeError("the connection dropped after the request was sent")

    job_id = await add_job(
        worker_database,
        fake_clock,
        JobRequest(kind=kind, idempotency_key="payout-1", payload={}, queue=names.queue),
    )
    runner = worker(worker_database, names, {kind: handler}, fake_clock)
    assert await runner.run_once() == 1
    row = await job_row(worker_database, job_id)
    assert (row["status"], row["attempts"], row["last_error_code"]) == (
        "failed",
        1,
        "uncertain_outcome",
    )
    # Not by the worker after any wait, and not by an operator: only reconciliation settles it.
    fake_clock.advance(hours=1)
    assert await runner.run_once() == 0
    async with worker_database.transaction() as session:
        assert not await job_queue.requeue_failed(session, job_id, now=fake_clock.now())
    assert await runner.run_once() == 0
    assert sent == 1


async def test_jobs_past_their_age_or_attempts_fail_when_claimed(
    worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    kind = names.kind("old")
    ran = 0

    async def handler(job: ClaimedJob, context: JobContext) -> None:
        nonlocal ran
        ran += 1

    expired = await add_job(
        worker_database,
        fake_clock,
        JobRequest(
            kind=kind,
            idempotency_key="expired",
            payload={},
            queue=names.queue,
            max_age=timedelta(minutes=5),
        ),
    )
    crashed = await add_job(
        worker_database,
        fake_clock,
        JobRequest(
            kind=kind, idempotency_key="crashed", payload={}, queue=names.queue, max_attempts=1
        ),
    )
    # The single allowed attempt is claimed by a worker that then dies.
    claimed = await job_queue.claim(
        worker_database,
        queue=names.queue,
        kinds=[kind],
        worker="dead",
        now=fake_clock.now(),
        lease=LEASE,
        limit=10,
    )
    assert {job.id for job in claimed} == {expired, crashed}
    fake_clock.advance(minutes=6)

    assert await worker(worker_database, names, {kind: handler}, fake_clock).run_once() == 0
    assert ran == 0
    assert (await job_row(worker_database, expired))["last_error_code"] == "max_age_exceeded"
    assert (await job_row(worker_database, crashed))["last_error_code"] == "attempts_exhausted"
    assert await attempts(worker_database, crashed) == [(1, "lease_expired", "lease_expired")]


async def test_the_worker_loop_runs_jobs_until_told_to_stop(
    worker_database: Database, names: Names
) -> None:
    kind = names.kind("loop")
    done = asyncio.Event()
    seen: list[str] = []

    async def handler(job: ClaimedJob, context: JobContext) -> None:
        seen.append(job.idempotency_key)
        if len(seen) == 6:
            done.set()

    runner = Worker(
        worker_database,
        {kind: handler},
        WorkerConfig(queue=names.queue, concurrency=2, poll_interval=timedelta(milliseconds=20)),
    )
    stop = asyncio.Event()
    running = asyncio.create_task(runner.run(stop))
    for index in range(6):
        async with worker_database.transaction() as session:
            await enqueue(
                session,
                JobRequest(kind=kind, idempotency_key=f"j{index}", payload={}, queue=names.queue),
                now=runner.clock.now(),
            )
    await asyncio.wait_for(done.wait(), 10)
    stop.set()
    await asyncio.wait_for(running, 10)
    assert sorted(seen) == [f"j{index}" for index in range(6)]
    pending = "SELECT count(*) FROM jobs WHERE queue = :q AND status <> 'succeeded'"
    assert await scalar(worker_database, pending, q=names.queue) == 0


# Handlers with an external effect


async def test_an_unsorted_exception_from_an_external_effect_is_uncertain_and_never_rerun(
    worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    kind = names.kind("provider")
    calls = 0

    async def handler(job: ClaimedJob, context: JobContext) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError(SECRET_TEXT)  # the call may or may not have reached the provider

    job_id = await add_job(
        worker_database,
        fake_clock,
        JobRequest(kind=kind, idempotency_key="send", payload={}, queue=names.queue),
    )
    runner = worker(worker_database, names, {kind: external_effect(handler)}, fake_clock)
    assert await runner.run_once() == 1

    row = await job_row(worker_database, job_id)
    assert (row["status"], row["last_error_code"]) == ("failed", "uncertain_outcome")
    assert row["last_error_message"] == "RuntimeError"
    assert await attempts(worker_database, job_id) == [(1, "failed", "uncertain_outcome")]
    fake_clock.advance(hours=1)
    assert await runner.run_once() == 0
    assert calls == 1


async def test_an_external_effect_still_retries_what_its_handler_says_did_not_happen(
    worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    kind = names.kind("provider")
    calls = 0

    async def handler(job: ClaimedJob, context: JobContext) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableJobError("provider refused the connection", code="provider_down")

    job_id = await add_job(
        worker_database,
        fake_clock,
        JobRequest(kind=kind, idempotency_key="send", payload={}, queue=names.queue),
    )
    runner = worker(worker_database, names, {kind: external_effect(handler)}, fake_clock)
    assert await runner.run_once() == 1
    fake_clock.advance(minutes=2)
    assert await runner.run_once() == 1
    assert await attempts(worker_database, job_id) == [
        (1, "retry", "provider_down"),
        (2, "succeeded", None),
    ]


async def test_an_expired_lease_on_an_external_effect_is_uncertain_and_never_rerun(
    worker_database: Database, pools: list[Database], names: Names, fake_clock: FakeClock
) -> None:
    external, internal = names.kind("provider"), names.kind("internal")
    stalled, resume = asyncio.Event(), asyncio.Event()
    calls: list[str] = []
    late: list[BaseException] = []

    async def slow(job: ClaimedJob, context: JobContext) -> None:
        calls.append(job.kind)
        if job.kind == internal:
            return
        stalled.set()
        await resume.wait()
        try:
            await context.apply(effect(names, "late", fake_clock))
        except StaleLeaseError as error:
            late.append(error)
            raise

    job_id = await add_job(
        worker_database,
        fake_clock,
        JobRequest(kind=external, idempotency_key="send", payload={}, queue=names.queue),
    )
    handlers: Mapping[str, JobHandler | Handler] = {
        external: external_effect(slow),
        internal: slow,
    }
    first = asyncio.create_task(worker(pools[0], names, handlers, fake_clock).run_once())
    await asyncio.wait_for(stalled.wait(), 5)
    # A job without an external effect whose worker died is simply run again.
    other_id = await add_job(
        worker_database,
        fake_clock,
        JobRequest(kind=internal, idempotency_key="tidy", payload={}, queue=names.queue),
    )
    dead = await job_queue.claim(
        worker_database,
        queue=names.queue,
        kinds=[internal],
        worker="dead",
        now=fake_clock.now(),
        lease=LEASE,
        limit=10,
    )
    assert [job.id for job in dead] == [other_id]
    fake_clock.advance(seconds=LEASE.total_seconds() + 1)

    second = worker(worker_database, names, handlers, fake_clock, worker_id="b")
    assert await second.run_once() == 1  # only the internal job is leased again
    row = await job_row(worker_database, job_id)
    assert (row["status"], row["last_error_code"]) == ("failed", "uncertain_outcome")
    assert (row["lease_owner"], row["attempts"]) == (None, 1)
    assert await attempts(worker_database, job_id) == [(1, "lease_expired", "lease_expired")]
    assert await attempts(worker_database, other_id) == [
        (1, "lease_expired", "lease_expired"),
        (2, "succeeded", None),
    ]

    # The stalled worker wakes up. Its result is refused: reconciliation owns the outcome now.
    resume.set()
    assert await first == 1
    assert len(late) == 1
    assert await effects(worker_database, names) == 0
    assert (await job_row(worker_database, job_id))["status"] == "failed"
    fake_clock.advance(hours=1)
    assert await second.run_once() == 0
    assert calls == [external, internal]


async def test_an_expired_lease_on_an_external_effect_is_uncertain_before_it_is_out_of_attempts(
    worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    kind = names.kind("provider")
    job_id = await add_job(
        worker_database,
        fake_clock,
        JobRequest(
            kind=kind,
            idempotency_key="send",
            payload={},
            queue=names.queue,
            max_attempts=1,
            max_age=timedelta(minutes=5),
        ),
    )

    async def handler(job: ClaimedJob, context: JobContext) -> None:
        raise AssertionError("the job must not run again")

    claimed = await job_queue.claim(
        worker_database,
        queue=names.queue,
        kinds=[kind],
        external_kinds=[kind],
        worker="dead",
        now=fake_clock.now(),
        lease=LEASE,
        limit=10,
    )
    assert len(claimed) == 1
    fake_clock.advance(minutes=6)
    runner = worker(worker_database, names, {kind: external_effect(handler)}, fake_clock)
    assert await runner.run_once() == 0
    # Out of attempts and past its age as well, but uncertain is what reconciliation must see.
    assert (await job_row(worker_database, job_id))["last_error_code"] == "uncertain_outcome"


# The scheduler


def interval_schedule(names: Names, clock: FakeClock, **options: object) -> Schedule:
    # The fake clock is in the real past, so APScheduler's own timer never fires this trigger
    # during a test. Dispatches happen only when a test calls them.
    return Schedule(
        name=f"test.tick_{names.suffix}",
        trigger=IntervalTrigger(
            minutes=1,
            start_date=clock.now(),
            end_date=clock.now() + timedelta(days=1),
            timezone="UTC",
        ),
        kind=names.kind("tick"),
        queue=names.queue,
        **options,  # type: ignore[arg-type]
    )


async def instances(database: Database, schedule: Schedule) -> list[tuple[str, int]]:
    async with database.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT i.leader_token, j.idempotency_key FROM schedule_instances i "
                "JOIN jobs j ON j.id = i.job_id WHERE i.schedule_name = :name "
                "ORDER BY i.scheduled_for"
            ),
            {"name": schedule.name},
        )
        return [(row.idempotency_key, row.leader_token) for row in rows]


async def test_one_leader_at_a_time_with_fenced_failover(
    worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    name = f"leader_{names.suffix}"
    first = LeaderLease(worker_database, holder="a", name=name, ttl=LEASE, clock=fake_clock)
    second = LeaderLease(worker_database, holder="b", name=name, ttl=LEASE, clock=fake_clock)
    assert await first.acquire() == 1
    assert await second.acquire() is None
    fake_clock.advance(seconds=20)
    assert await first.renew()
    fake_clock.advance(seconds=20)
    assert await second.acquire() is None  # the renewed lease is still valid
    fake_clock.advance(seconds=15)
    assert await second.acquire() == 2
    assert not await first.renew()
    assert first.token is None
    await second.release()
    assert await first.acquire() == 3


async def test_concurrent_candidates_elect_exactly_one_leader(
    pools: list[Database], names: Names, fake_clock: FakeClock
) -> None:
    name = f"leader_{names.suffix}"
    barrier = AsyncBarrier(len(pools))

    async def stand(index: int, database: Database) -> int | None:
        lease = LeaderLease(database, holder=f"h{index}", name=name, ttl=LEASE, clock=fake_clock)
        await barrier.wait()
        return await lease.acquire()

    tokens = await asyncio.gather(*(stand(index, db) for index, db in enumerate(pools)))
    assert sorted(token for token in tokens if token is not None) == [1]


async def test_missed_runs_are_caught_up_by_policy_and_never_repeated(
    worker_database: Database, names: Names, fake_clock: FakeClock
) -> None:
    lease = LeaderLease(
        worker_database, holder="a", name=f"leader_{names.suffix}", ttl=LEASE, clock=fake_clock
    )
    await lease.acquire()
    dispatcher = ScheduleDispatcher(worker_database, lease, fake_clock)
    start = fake_clock.now()
    latest = interval_schedule(names, fake_clock)
    every = replace(latest, name=f"test.every_{names.suffix}", catch_up="all", max_catch_up=3)

    for schedule in (latest, every):
        first = await dispatcher.dispatch_due(schedule)
        assert (first.instances, first.next_fire_at) == ((), start)

    # Downtime: five fire times pass before the scheduler dispatches again.
    fake_clock.advance(minutes=4, seconds=30)
    assert await lease.acquire() == 2  # the expired lease is retaken by the same holder
    coalesced = await dispatcher.dispatch_due(latest)
    assert coalesced.instances == (start + timedelta(minutes=4),)
    assert coalesced.skipped == 4
    assert coalesced.next_fire_at == start + timedelta(minutes=5)
    backfilled = await dispatcher.dispatch_due(every)
    assert backfilled.instances == tuple(start + timedelta(minutes=m) for m in (2, 3, 4))
    assert backfilled.skipped == 2
    # A repeated dispatch in the same minute creates nothing.
    assert (await dispatcher.dispatch_due(every)).instances == ()

    assert await instances(worker_database, latest) == [
        (instance_key(latest.name, start + timedelta(minutes=4)), 2)
    ]
    assert [key for key, _ in await instances(worker_database, every)] == [
        instance_key(every.name, start + timedelta(minutes=m)) for m in (2, 3, 4)
    ]
    skipped = "SELECT skipped_count FROM schedule_progress WHERE name = :n"
    assert await scalar(worker_database, skipped, n=every.name) == 2


async def test_concurrent_and_deposed_dispatches_never_duplicate_a_run(
    worker_database: Database, pools: list[Database], names: Names, fake_clock: FakeClock
) -> None:
    name = f"leader_{names.suffix}"
    schedule = interval_schedule(names, fake_clock, catch_up="all", max_catch_up=10)
    old = LeaderLease(worker_database, holder="old", name=name, ttl=LEASE, clock=fake_clock)
    await old.acquire()
    await ScheduleDispatcher(worker_database, old, fake_clock).dispatch_due(schedule)
    stale_token = old.token

    # The old leader stalls past its lease and a standby takes over.
    fake_clock.advance(minutes=3)
    new = LeaderLease(worker_database, holder="new", name=name, ttl=LEASE, clock=fake_clock)
    assert await new.acquire() == 2
    # The same leader dispatching twice at once, plus the deposed leader still holding token 1.
    deposed = LeaderLease(pools[2], holder="old", name=name, ttl=LEASE, clock=fake_clock)
    deposed.token = stale_token
    barrier = AsyncBarrier(3)

    async def dispatch(dispatcher: ScheduleDispatcher) -> object:
        await barrier.wait()
        try:
            return await dispatcher.dispatch_due(schedule)
        except LeadershipLostError as error:
            return error

    results = await asyncio.gather(
        dispatch(ScheduleDispatcher(pools[0], new, fake_clock)),
        dispatch(ScheduleDispatcher(pools[1], new, fake_clock)),
        dispatch(ScheduleDispatcher(pools[2], deposed, fake_clock)),
    )

    assert isinstance(results[2], LeadershipLostError)
    created = await instances(worker_database, schedule)
    start = fake_clock.now() - timedelta(minutes=3)
    assert [key for key, _ in created] == [
        instance_key(schedule.name, start + timedelta(minutes=m)) for m in range(4)
    ]
    assert {token for _, token in created} == {2}


async def test_the_scheduler_dispatches_as_leader_and_hands_over_on_shutdown(
    worker_database: Database, pools: list[Database], names: Names, fake_clock: FakeClock
) -> None:
    name = f"leader_{names.suffix}"
    schedule = interval_schedule(names, fake_clock)
    # A lease longer than the minute the test moves the clock by, so leadership is kept.
    ttl = timedelta(minutes=5)
    lease = LeaderLease(worker_database, holder="a", name=name, ttl=ttl, clock=fake_clock)
    service = SchedulerService(
        worker_database,
        [schedule],
        lease,
        clock=fake_clock,
        renew_interval=timedelta(milliseconds=20),
    )
    standby = LeaderLease(pools[0], holder="b", name=name, ttl=ttl, clock=fake_clock)
    stop = asyncio.Event()
    running = asyncio.create_task(service.run(stop))
    try:
        async with asyncio.timeout(10):
            while not service.leading:  # noqa: ASYNC110 - the service exposes no event for this
                await asyncio.sleep(0.01)
        assert await standby.acquire() is None
        fake_clock.advance(minutes=1, seconds=1)
        await service.dispatch(schedule)
        assert len(await instances(worker_database, schedule)) == 1
    finally:
        stop.set()
        await asyncio.wait_for(running, 15)
    assert not service.leading
    # Released, not merely left to expire: the standby takes over without waiting for the TTL.
    assert await standby.acquire() == 2
