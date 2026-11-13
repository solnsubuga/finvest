"""The worker fills the keyed hash of email rows saved before the key existed, once each."""

import asyncio
import secrets
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, RetryPolicy, Worker, WorkerConfig, enqueue
from app.modules.notifications import keyed_backfill, suppression_lifts
from app.modules.notifications.domain.email_events import address_hash, keyed_hash
from app.shared.push_ids import push_id
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api

pytestmark = pytest.mark.integration

# Made for this run. The real key comes only from the environment.
HASH_KEY = secrets.token_bytes(32)


@pytest.fixture(autouse=True)
def own_queue(monkeypatch: pytest.MonkeyPatch) -> str:
    name = f"keyed_backfill_{secrets.token_hex(8)}"
    monkeypatch.setattr(keyed_backfill, "QUEUE", name)
    return name


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


def a_worker(database: Database, api: Api, key: bytes | None = HASH_KEY) -> Worker:
    config = WorkerConfig(
        queue=keyed_backfill.QUEUE,
        worker_id="keyed-backfill-test",
        retry=RetryPolicy(base=timedelta(seconds=10), cap=timedelta(minutes=1)),
    )
    handlers = keyed_backfill.KeyedBackfillJobs(database, api.clock, key).handlers()
    return Worker(database, handlers, config, api.clock)


async def start(database: Database, api: Api) -> str:
    """Queue a run as the scheduler would."""
    key = f"test:{push_id()}"
    async with database.transaction() as session:
        await enqueue(
            session,
            JobRequest(
                kind=keyed_backfill.KEY_HASHES,
                idempotency_key=key,
                payload={},
                queue=keyed_backfill.QUEUE,
            ),
            now=api.clock.now(),
        )
    return key


async def drain(worker: Worker) -> int:
    runs = 0
    while await worker.run_once():
        runs += 1
    return runs


async def unkeyed_rows(migrator: Database, api: Api) -> tuple[str, str, str]:
    """An event, a suppression and a lift as they were saved before migration 0093."""
    hashed = address_hash(f"client.{secrets.token_hex(6)}@example.com")
    svix_id = f"msg_{secrets.token_hex(8)}"
    async with migrator.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO notification_email_events (svix_id, delivery, email_id, recipients, "
                "permanent, received_at, processed_at, outcome) VALUES (:svix, 'bounced', "
                ":email_id, ARRAY[:hash]::text[], true, :at, :at, 'applied')"
            ),
            {
                "svix": svix_id,
                "email_id": secrets.token_hex(8),
                "hash": hashed,
                "at": api.clock.now(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO notification_email_suppressions (address_hash, reason, svix_id, "
                "created_at) VALUES (:hash, 'bounced', :svix, :at)"
            ),
            {"hash": hashed, "svix": svix_id, "at": api.clock.now()},
        )
    staff = await api.staff("support")
    async with api.database.actor_transaction(staff) as session:
        lift = await suppression_lifts.propose(session, staff, hashed, "Fixed", api.clock, None)
    return svix_id, hashed, lift.id


async def keyed_values(
    migrator: Database, svix_id: str, hashed: str, lift_id: str
) -> tuple[object, ...]:
    async with migrator.transaction() as session:
        row = (
            await session.execute(
                text(
                    "SELECT (SELECT recipients_keyed FROM notification_email_events "
                    "WHERE svix_id = :svix), (SELECT address_key FROM "
                    "notification_email_suppressions WHERE address_hash = :hash), "
                    "(SELECT address_key FROM notification_email_suppression_lifts "
                    "WHERE id = :lift)"
                ),
                {"svix": svix_id, "hash": hashed, "lift": lift_id},
            )
        ).one()
    return tuple(row)


async def left(migrator: Database) -> int:
    async with migrator.transaction() as session:
        return int((await session.execute(keyed_backfill._LEFT)).scalar_one())


async def test_a_run_fills_every_empty_keyed_hash_a_page_at_a_time(
    api: Api,
    migrator_database: Database,
    worker_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [await unkeyed_rows(migrator_database, api) for _ in range(3)]
    monkeypatch.setattr(keyed_backfill, "PAGE", 1)
    await start(worker_database, api)

    runs = await drain(a_worker(worker_database, api))

    # One row of each table per page, so the run queued the pages after the first itself.
    assert runs >= 3
    assert await left(migrator_database) == 0
    for svix_id, hashed, lift_id in rows:
        keyed = keyed_hash(hashed, HASH_KEY)
        assert await keyed_values(migrator_database, svix_id, hashed, lift_id) == (
            [keyed],
            keyed,
            keyed,
        )


async def test_running_again_changes_nothing(
    api: Api, migrator_database: Database, worker_database: Database
) -> None:
    rows = await unkeyed_rows(migrator_database, api)
    await start(worker_database, api)
    await drain(a_worker(worker_database, api))
    before = await keyed_values(migrator_database, *rows)

    # A second run, even with another key, finds nothing empty and so changes nothing.
    await start(worker_database, api)
    assert await drain(a_worker(worker_database, api, secrets.token_bytes(32))) == 1
    assert await keyed_values(migrator_database, *rows) == before


async def test_two_pages_at_once_fill_each_row_once(
    api: Api, migrator_database: Database, worker_database: Database
) -> None:
    for _ in range(4):
        await unkeyed_rows(migrator_database, api)
    total = await left(migrator_database)
    gate = asyncio.Event()

    async def page() -> int:
        async with worker_database.transaction() as session:
            filled = await keyed_backfill.fill_page(session, HASH_KEY)
            await gate.wait()
            return filled

    first, second = asyncio.create_task(page()), asyncio.create_task(page())
    await asyncio.sleep(0.5)
    gate.set()
    # Each takes the rows the other has not locked, so between them every row is filled once.
    assert await first + await second == total
    assert await left(migrator_database) == 0


async def test_a_keyed_hash_once_set_is_never_changed(
    api: Api, migrator_database: Database, worker_database: Database
) -> None:
    svix_id, hashed, lift_id = await unkeyed_rows(migrator_database, api)
    await start(worker_database, api)
    await drain(a_worker(worker_database, api))
    other = keyed_hash(hashed, secrets.token_bytes(32))

    for sql in (
        "UPDATE notification_email_events SET recipients_keyed = ARRAY[:other]::text[] "
        "WHERE svix_id = :id",
        "UPDATE notification_email_suppressions SET address_key = :other WHERE address_hash = :id",
    ):
        with pytest.raises(DBAPIError, match="set once and never changed"):
            async with worker_database.transaction() as session:
                await session.execute(
                    text(sql), {"other": other, "id": svix_id if "events" in sql else hashed}
                )
    # A lift's keyed hash is out of the worker's reach once set.
    async with worker_database.transaction() as session:
        changed = await session.execute(
            text(
                "UPDATE notification_email_suppression_lifts SET address_key = :other "
                "WHERE id = :id"
            ),
            {"other": other, "id": lift_id},
        )
    assert changed.rowcount == 0  # type: ignore[attr-defined]
    keyed = keyed_hash(hashed, HASH_KEY)
    assert await keyed_values(migrator_database, svix_id, hashed, lift_id) == (
        [keyed],
        keyed,
        keyed,
    )


async def test_the_worker_fills_a_lift_but_changes_nothing_else_in_it(
    api: Api, migrator_database: Database, worker_database: Database
) -> None:
    _, hashed, lift_id = await unkeyed_rows(migrator_database, api)
    async with migrator_database.transaction() as session:
        await session.execute(
            text("GRANT UPDATE (reason) ON notification_email_suppression_lifts TO finvest_worker")
        )
    try:
        with pytest.raises(DBAPIError, match="only staff lift a suppression"):
            async with worker_database.transaction() as session:
                await session.execute(
                    text(
                        "UPDATE notification_email_suppression_lifts SET address_key = :keyed, "
                        "reason = 'Changed' WHERE id = :id"
                    ),
                    {"keyed": keyed_hash(hashed, HASH_KEY), "id": lift_id},
                )
    finally:
        async with migrator_database.transaction() as session:
            await session.execute(
                text(
                    "REVOKE UPDATE (reason) ON notification_email_suppression_lifts "
                    "FROM finvest_worker"
                )
            )


async def test_without_a_key_the_run_fails_and_fills_nothing(
    api: Api, migrator_database: Database, worker_database: Database
) -> None:
    rows = await unkeyed_rows(migrator_database, api)
    key = await start(worker_database, api)

    await drain(a_worker(worker_database, api, key=None))

    async with migrator_database.transaction() as session:
        job = (
            await session.execute(
                text(
                    "SELECT status, last_error_code, last_error_message FROM jobs "
                    "WHERE kind = :kind AND idempotency_key = :key"
                ),
                {"kind": keyed_backfill.KEY_HASHES, "key": key},
            )
        ).one()
    assert (job.status, job.last_error_code) == ("failed", "email_hash_key_missing")
    assert await keyed_values(migrator_database, *rows) == (None, None, None)
