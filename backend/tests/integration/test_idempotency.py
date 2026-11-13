"""Idempotent commands against real PostgreSQL, as finvest_app, with separate transactions."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.idempotency import Outcome, Receipt, run_once
from app.infrastructure.jobs import JobRequest, enqueue
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.database import TestDatabase

pytestmark = pytest.mark.integration

OPERATION = "investments.invest"
REQUEST: dict[str, object] = {"product": "tbill-91", "amount": 50_000}


@pytest.fixture
def client() -> Actor:
    return Actor("client", push_id())


@pytest.fixture
def key() -> str:
    return f"key-{push_id()}"


@pytest.fixture
async def pools(test_database: TestDatabase) -> AsyncIterator[list[Database]]:
    databases = [Database(test_database.settings("finvest_app")) for _ in range(4)]
    yield databases
    for database in databases:
        await database.close()


class Command:
    """Stands in for a money command: counts its runs and leaves a job, as a real one would."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.runs = 0

    def bind(self, session: AsyncSession, key: str) -> Callable[[], Awaitable[Receipt]]:
        async def run() -> Receipt:
            self.runs += 1
            job_id = await enqueue(
                session,
                JobRequest(kind="test.idempotent_effect", idempotency_key=key, payload={}),
                now=self.clock.now(),
            )
            return Receipt(201, {"investment_id": job_id, "run": self.runs})

        return run


async def invest(
    database: Database,
    actor: Actor,
    key: str,
    command: Command,
    request: dict[str, object] = REQUEST,
    before: Callable[[], Awaitable[object]] | None = None,
) -> Outcome:
    async with database.actor_transaction(actor) as session:
        if before is not None:
            await before()
        return await run_once(
            session,
            actor=actor,
            operation=OPERATION,
            key=key,
            request=request,
            now=command.clock.now(),
            command=command.bind(session, key),
        )


async def effects(database: Database, key: str) -> object:
    async with database.transaction() as session:
        return await session.scalar(
            text(
                "SELECT count(*) FROM jobs WHERE kind = 'test.idempotent_effect' AND "
                "idempotency_key = :key"
            ),
            {"key": key},
        )


async def test_a_retry_returns_the_saved_outcome_and_does_not_run_again(
    app_database: Database,
    worker_database: Database,
    client: Actor,
    key: str,
    fake_clock: FakeClock,
) -> None:
    command = Command(fake_clock)
    first = await invest(app_database, client, key, command)
    second = await invest(app_database, client, key, command)
    assert (first.replayed, second.replayed) == (False, True)
    assert first.receipt == second.receipt
    assert first.receipt.status_code == 201
    assert command.runs == 1
    assert await effects(worker_database, key) == 1


async def test_a_key_reused_for_a_different_request_is_refused(
    app_database: Database, client: Actor, key: str, fake_clock: FakeClock
) -> None:
    command = Command(fake_clock)
    await invest(app_database, client, key, command)
    with pytest.raises(ApplicationError) as raised:
        await invest(app_database, client, key, command, {**REQUEST, "amount": 60_000})
    assert raised.value.code == "idempotency_key_reused"
    assert command.runs == 1


async def test_a_failed_command_saves_nothing_and_can_be_retried(
    app_database: Database,
    worker_database: Database,
    client: Actor,
    key: str,
    fake_clock: FakeClock,
) -> None:
    command = Command(fake_clock)

    async def failing(session: AsyncSession) -> Receipt:
        await command.bind(session, key)()
        raise ApplicationError("conflict")

    with pytest.raises(ApplicationError, match="conflict"):
        async with app_database.actor_transaction(client) as session:
            await run_once(
                session,
                actor=client,
                operation=OPERATION,
                key=key,
                request=REQUEST,
                now=fake_clock.now(),
                command=lambda: failing(session),
            )
    assert await effects(worker_database, key) == 0

    retried = await invest(app_database, client, key, command)
    assert not retried.replayed
    assert await effects(worker_database, key) == 1


async def test_concurrent_requests_with_one_key_run_the_command_once(
    pools: list[Database],
    worker_database: Database,
    client: Actor,
    key: str,
    fake_clock: FakeClock,
) -> None:
    command = Command(fake_clock)
    barrier = AsyncBarrier(len(pools))
    outcomes = await asyncio.gather(
        *(invest(database, client, key, command, before=barrier.wait) for database in pools)
    )
    assert command.runs == 1
    assert sorted(outcome.replayed for outcome in outcomes) == [False, True, True, True]
    assert all(outcome.receipt == outcomes[0].receipt for outcome in outcomes)
    assert await effects(worker_database, key) == 1


async def test_concurrent_requests_with_different_keys_do_not_wait_on_each_other(
    pools: list[Database], client: Actor, fake_clock: FakeClock
) -> None:
    command = Command(fake_clock)
    barrier = AsyncBarrier(len(pools))
    async with asyncio.timeout(10):
        outcomes = await asyncio.gather(
            *(
                invest(database, client, f"key-{push_id()}", command, before=barrier.wait)
                for database in pools
            )
        )
    assert command.runs == len(pools)
    assert not any(outcome.replayed for outcome in outcomes)


async def test_two_people_may_use_the_same_key_without_seeing_each_other(
    app_database: Database, client: Actor, key: str, fake_clock: FakeClock
) -> None:
    other = Actor("client", push_id())
    command = Command(fake_clock)
    mine = await invest(app_database, client, key, command)
    theirs = await invest(app_database, other, key, command, {**REQUEST, "amount": 1})
    assert (mine.replayed, theirs.replayed) == (False, False)
    assert mine.receipt != theirs.receipt

    count = text("SELECT count(*) FROM operation_receipts WHERE idempotency_key = :key")
    async with app_database.actor_transaction(client) as session:
        assert await session.scalar(count, {"key": key}) == 1
    async with app_database.actor_transaction(Actor("staff", client.id)) as session:
        assert await session.scalar(count, {"key": key}) == 0  # same ID, different kind
    async with app_database.transaction() as session:
        assert await session.scalar(count, {"key": key}) == 0  # no actor, no rows


async def test_a_receipt_cannot_be_saved_for_someone_else(
    app_database: Database, client: Actor, key: str, fake_clock: FakeClock
) -> None:
    insert = text(
        "INSERT INTO operation_receipts VALUES ('client', :actor, 'x.y', :key, :digest, 200, "
        "'{}', now())"
    )
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(insert, {"actor": push_id(), "key": key, "digest": "0" * 64})


async def test_receipts_are_never_changed_or_removed_and_the_worker_has_no_access(
    app_database: Database,
    worker_database: Database,
    client: Actor,
    key: str,
    fake_clock: FakeClock,
) -> None:
    await invest(app_database, client, key, Command(fake_clock))
    for statement in (
        "UPDATE operation_receipts SET status_code = 200",
        "DELETE FROM operation_receipts",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(text(statement))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(text("SELECT count(*) FROM operation_receipts"))


async def test_the_system_has_no_idempotency_keys(
    app_database: Database, key: str, fake_clock: FakeClock
) -> None:
    with pytest.raises(ValueError, match="belong to a client or a staff member"):
        await invest(app_database, SYSTEM, key, Command(fake_clock))
