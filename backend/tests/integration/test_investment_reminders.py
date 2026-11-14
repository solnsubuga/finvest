"""Reminding a client that an investment matures soon, on the days Settings names."""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from app.modules.investments import reminders
from app.modules.investments import service as investments
from app.modules.ledger.domain.entries import AccountKind, AccountSpec
from app.shared.actors import SYSTEM
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support.investments import PRESENT, today
from tests.support.ledger import ugx
from tests.support.settings import clear_settings, set_setting

pytestmark = pytest.mark.integration

DAY = today()


@pytest.fixture(autouse=True)
async def clean(migrator_database: Database) -> AsyncIterator[None]:
    await clear_settings(migrator_database)
    yield
    await clear_settings(migrator_database)


async def maturing_in(worker_database: Database, days: int) -> investments.Investment:
    """A running 91-day bill that matures `days` from today."""
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.open_existing_investment(
            session,
            SYSTEM,
            client_id=push_id(),
            product_code="TREA-91",
            principal=ugx(1_000_000),
            start_date=DAY - timedelta(days=91 - days),
            annual_rate_bps=1300,
            tax_bps=1500,
            interest_through=DAY - timedelta(days=1),
            opened_on=DAY,
            legacy_reference=f"FV1-INV-{push_id()[-8:].encode().hex()}",
            opening_account=AccountSpec(
                "migration.opening_balances",
                AccountKind.ASSET,
                Currency.UGX,
                "Finvest 1.0 balances carried over",
            ),
            clock=PRESENT,
        )


async def remind(worker_database: Database, *, limit: int = reminders.BATCH) -> int:
    told, after = 0, ""
    while True:
        async with worker_database.actor_transaction(SYSTEM) as session:
            batch = await reminders.remind_batch(session, DAY, PRESENT, after=after, limit=limit)
        told += batch.told
        if batch.last_id is None:
            return told
        after = batch.last_id


async def told(worker_database: Database, client_id: str) -> list[tuple[str, str, str]]:
    async with worker_database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT idempotency_key, link_kind, body FROM notifications "
                "WHERE client_id = :client_id AND event = 'investment_maturing'"
            ),
            {"client_id": client_id},
        )
        return [(row.idempotency_key, row.link_kind, row.body) for row in rows]


async def test_a_client_is_reminded_once_seven_days_and_one_day_before(
    worker_database: Database,
) -> None:
    week, tomorrow, other = (
        await maturing_in(worker_database, 7),
        await maturing_in(worker_database, 1),
        await maturing_in(worker_database, 5),
    )
    assert await remind(worker_database, limit=1) >= 2
    [(key, link, body)] = await told(worker_database, week.client_id)
    matures = week.maturity_date
    assert matures is not None
    assert key == reminders.reminder_key(week.id, matures, 7)
    assert link == "investment" and matures.strftime("%-d %b %Y") in body
    assert len(await told(worker_database, tomorrow.client_id)) == 1
    assert await told(worker_database, other.client_id) == []
    # A second run the same morning tells nobody again.
    assert await remind(worker_database) == 0


async def test_staff_choose_the_days_and_an_empty_list_sends_none(
    app_database: Database, worker_database: Database
) -> None:
    five = await maturing_in(worker_database, 5)
    seven = await maturing_in(worker_database, 7)
    await set_setting(app_database, "maturity_reminders", [], PRESENT)
    assert await remind(worker_database) == 0
    await set_setting(app_database, "maturity_reminders", [5], PRESENT)
    await remind(worker_database)
    assert len(await told(worker_database, five.client_id)) == 1
    assert await told(worker_database, seven.client_id) == []
