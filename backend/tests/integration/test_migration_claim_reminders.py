"""The weekly claim reminder: a fresh link for each migrated client who has not claimed yet."""

import asyncio
from datetime import UTC, datetime
from itertools import count

import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, enqueue
from app.modules.identity.service import create_unclaimed_client
from app.modules.migration import claims, reminders
from app.modules.migration.jobs import CLAIM_REMINDERS_KIND, MigrationJobs
from app.shared.actors import SYSTEM, Actor
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.jobs import run_due
from tests.support.kyc import SEALER

pytestmark = pytest.mark.integration

START = datetime(2027, 3, 1, 9, tzinfo=UTC)
STAFF = Actor("staff", "-Nabc0000000000000S1")
CLIENT_APP = "https://app.finvest.test"
_numbers = count(772_610_000)


async def moved_client(
    app: Database, worker: Database, clock: FakeClock, *, email: str | None
) -> tuple[str, str]:
    """A client carried over from Finvest 1.0 and sent a first link. Returns them, their phone."""
    phone = f"+256{next(_numbers)}"
    async with app.actor_transaction(STAFF) as session:
        client_id = await create_unclaimed_client(session, clock, STAFF, phone=phone, email=email)
    async with worker.actor_transaction(SYSTEM) as session:
        await claims.create_claim(
            session,
            SYSTEM,
            clock,
            client_id=client_id,
            legacy_client_ref=f"CL-{client_id[-8:]}",
            needs_id_check=False,
        )
        await claims.send_link(session, SYSTEM, clock, client_id)
    return client_id, phone


async def run_reminders(worker: Database, clock: FakeClock) -> None:
    """Queue the day's run as the scheduler would, and let the worker finish it. Other tests'
    claims share the database, so how many it sends is not this test's to assert."""
    async with worker.transaction() as session:
        await enqueue(
            session,
            JobRequest(kind=CLAIM_REMINDERS_KIND, idempotency_key=push_id(), payload={}),
            now=clock.now(),
        )
    runner = Worker(
        worker, MigrationJobs(worker, SEALER, clock, CLIENT_APP).handlers(), clock=clock
    )
    await run_due(runner)


async def messages(migrator: Database, *recipients: str) -> list[tuple[str, str, str]]:
    async with migrator.transaction() as session:
        rows = await session.execute(
            text(
                "SELECT recipient, channel, variables->>'link' AS link FROM notification_messages "
                "WHERE template = 'claim_invitation' AND recipient = ANY(:to) "
                "AND idempotency_key LIKE 'migration-reminder:%' ORDER BY created_at, id"
            ),
            {"to": list(recipients)},
        )
        return [(row.recipient, row.channel, row.link) for row in rows]


async def reminders_sent(worker: Database, client_id: str) -> int:
    async with worker.actor_transaction(SYSTEM) as session:
        view = await claims.claim_for(session, client_id)
    assert view is not None
    return view.reminders_sent


async def test_a_week_on_the_unclaimed_get_a_new_link_and_the_claimed_get_nothing(
    app_database: Database, worker_database: Database, migrator_database: Database
) -> None:
    clock = FakeClock(START)
    waiting, phone = await moved_client(
        app_database, worker_database, clock, email="grace@example.com"
    )
    claimed, claimed_phone = await moved_client(app_database, worker_database, clock, email=None)
    # The account became theirs, but their claim did not move: signing in commits on its own.
    async with migrator_database.transaction() as session:
        await session.execute(
            text(
                "UPDATE identity_clients SET status = 'active', password_hash = 'set-at-claim', "
                "password_changed_at = now() WHERE id = :id"
            ),
            {"id": claimed},
        )

    # Not a week yet: nothing.
    clock.advance(days=6)
    await run_reminders(worker_database, clock)
    assert await messages(migrator_database, phone, "grace@example.com", claimed_phone) == []

    clock.advance(days=1)
    await run_reminders(worker_database, clock)
    sent = await messages(migrator_database, phone, "grace@example.com", claimed_phone)
    assert [(to, channel) for to, channel, _ in sent] == [
        (phone, "sms"),
        ("grace@example.com", "email"),
    ]
    link = sent[0][2]
    assert link.startswith(f"{CLIENT_APP}/claim#") and sent[1][2] == link
    assert await reminders_sent(worker_database, waiting) == 1
    assert await reminders_sent(worker_database, claimed) == 0

    # The new link works, and the first one no longer does.
    async with app_database.transaction() as session:
        opened = await claims.open_link(session, clock, link.split("#", 1)[1])
    assert opened.client_id == waiting

    # The same day again sends nobody a second one.
    await run_reminders(worker_database, clock)
    assert len(await messages(migrator_database, phone, "grace@example.com", claimed_phone)) == 2
    assert await reminders_sent(worker_database, waiting) == 1


async def test_two_runs_at_once_send_one_reminder(
    app_database: Database, worker_database: Database, migrator_database: Database
) -> None:
    clock = FakeClock(START)
    client_id, phone = await moved_client(app_database, worker_database, clock, email=None)
    clock.advance(days=7)

    async def one_page() -> None:
        async with worker_database.actor_transaction(SYSTEM) as session:
            await reminders.send_page(session, clock, client_app=CLIENT_APP, limit=100_000)

    results = await asyncio.gather(one_page(), one_page(), return_exceptions=True)
    assert sum(1 for result in results if result is None) >= 1
    assert len(await messages(migrator_database, phone)) == 1
    assert await reminders_sent(worker_database, client_id) == 1
