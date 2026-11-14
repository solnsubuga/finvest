"""Staff post to the ledger only in the ways the migrations list, and PostgreSQL holds them to it.

The test database also lists `test.fixture`, so tests can set up books as staff. Nothing else is
added, so every other source type here is refused exactly as in production.
"""

from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.errors import StaffSourceNotAllowed
from app.shared.actors import SYSTEM
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.ledger import (
    balances,
    client,
    deposit,
    funded_books,
    key,
    open_books,
    payout,
    staff,
)

pytestmark = pytest.mark.integration

PRODUCTION_LIST = [
    "dealing.buffer_capital",
    "dealing.order",
    "ledger.correction",
    "payment.reassignment",
    "payout",
    "pot_transfer",
    "tax.remittance",
]

RAW_ENTRY = text(
    "INSERT INTO ledger_journal_entries (id, entry_type, currency, effective_date, posted_at, "
    "idempotency_key, request_digest, source_type, source_id) VALUES (:id, 'test.raw', 'UGX', "
    "DATE '2026-11-13', now(), :key, repeat('0', 64), :source_type, :source_id)"
)


async def test_the_list_is_what_the_migrations_seed(migrator_database: Database) -> None:
    async with migrator_database.transaction() as session:
        listed = (
            await session.execute(
                text("SELECT source_type FROM ledger_staff_source_types ORDER BY source_type")
            )
        ).scalars()
        assert [name for name in listed if name != "test.fixture"] == PRODUCTION_LIST


@pytest.mark.parametrize(
    ("source_type", "with_id"),
    [
        (None, False),
        ("test.other", True),
        ("payment.deposit", True),
        ("ledger.entry", True),
        ("dealing.lot", True),
        ("payouts", True),
    ],
)
async def test_staff_cannot_post_anything_else(
    app_database: Database, fake_clock: FakeClock, source_type: str | None, with_id: bool
) -> None:
    books = await funded_books(app_database, fake_clock, 0)
    draft = replace(
        deposit(books, 100),
        source_type=source_type,
        source_id=push_id() if with_id else None,
    )
    async with app_database.actor_transaction(staff()) as session:
        with pytest.raises(StaffSourceNotAllowed):
            await ledger.post_entry(session, draft, fake_clock)
        # The refusal left the transaction usable and holding nothing of the entry.
        await ledger.post_entry(session, deposit(books, 7), fake_clock)
    assert await balances(app_database, books) == (7, 7)
    # Plain SQL is refused the same way.
    with pytest.raises(DBAPIError, match="staff cannot post a ledger entry"):
        async with app_database.actor_transaction(staff()) as session:
            await session.execute(
                RAW_ENTRY,
                {
                    "id": push_id(),
                    "key": key(),
                    "source_type": source_type,
                    "source_id": push_id() if with_id else None,
                },
            )


async def test_staff_can_post_a_listed_type(app_database: Database, fake_clock: FakeClock) -> None:
    books = await funded_books(app_database, fake_clock, 0)
    async with app_database.actor_transaction(staff()) as session:
        entry = await ledger.post_entry(
            session,
            replace(deposit(books, 300), source_type="payout", source_id=push_id()),
            fake_clock,
        )
    assert entry.source_type == "payout"
    assert await balances(app_database, books) == (300, 300)


async def test_the_worker_and_clients_are_not_limited_by_the_list(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    alice = client()
    async with worker_database.actor_transaction(SYSTEM) as session:
        books = await open_books(session, fake_clock, alice)
        await ledger.post_entry(
            session,
            replace(deposit(books, 500), source_type="dealing.lot", source_id=push_id()),
            fake_clock,
        )
        await ledger.post_entry(
            session, replace(deposit(books, 1), source_type=None, source_id=None), fake_clock
        )
    async with app_database.actor_transaction(alice) as session:
        await ledger.post_entry(
            session,
            replace(payout(books, 200), source_type="client.request", source_id=push_id()),
            fake_clock,
        )
    assert await balances(worker_database, books) == (301, 301)


async def test_no_application_role_can_change_the_list(
    app_database: Database, worker_database: Database
) -> None:
    for database, actor in ((app_database, staff()), (worker_database, SYSTEM)):
        for statement in (
            "SELECT * FROM ledger_staff_source_types",
            "INSERT INTO ledger_staff_source_types VALUES ('test.sneaky', 'no')",
            "DELETE FROM ledger_staff_source_types",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                async with database.actor_transaction(actor) as session:
                    await session.execute(text(statement))
