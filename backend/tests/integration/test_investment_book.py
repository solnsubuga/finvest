"""What the dealing desk reads about running investments."""

from datetime import timedelta

import pytest

from app.infrastructure.database import Database
from app.modules.investments import service as investments
from app.shared.actors import SYSTEM
from app.shared.money import Currency, Money
from tests.support.investments import PRESENT, at, started, today
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

DAY = today()
NEXT = DAY + timedelta(days=1)


def owed_for(rows: list[investments.ProductOwed], code: str) -> tuple[int, int, int]:
    for row in rows:
        if row.product_code == code and row.currency is Currency.UGX:
            return row.investments, row.principal.minor, row.interest_accrued.minor
    return 0, 0, 0


async def test_what_is_owed_counts_principal_and_net_interest_by_product(
    app_database: Database, worker_database: Database
) -> None:
    async with worker_database.actor_transaction(SYSTEM) as session:
        before = owed_for(await investments.owed_by_product(session, NEXT), "TREA-182")

    first = await started(app_database, worker_database, PRESENT, product_code="TREA-182")
    await started(
        app_database, worker_database, PRESENT, product_code="TREA-182", amount=ugx(2_000_000)
    )
    async with worker_database.actor_transaction(SYSTEM) as session:
        # Interest is earned from the day after the start.
        await investments.accrue_investment(session, first.id, NEXT, at(NEXT))
        accrued = await investments.get_investment(session, first.id)
        after = owed_for(await investments.owed_by_product(session, NEXT), "TREA-182")
        earlier = owed_for(
            await investments.owed_by_product(session, DAY - timedelta(days=1)), "TREA-182"
        )

    assert accrued.accrued_net.minor > 0
    assert after[0] - before[0] == 2
    assert after[1] - before[1] == 3_000_000
    assert after[2] - before[2] == accrued.accrued_net.minor
    # Nothing started today was running yesterday.
    assert earlier[0] <= before[0]


async def test_running_investments_are_oldest_first_and_can_be_limited_by_start(
    app_database: Database, worker_database: Database
) -> None:
    first = await started(app_database, worker_database, PRESENT, product_code="TREA-91")
    second = await started(app_database, worker_database, PRESENT, product_code="TREA-91")
    async with worker_database.actor_transaction(SYSTEM) as session:
        running = await investments.running_investments(session, "TREA-91")
        since_yesterday = await investments.running_investments(
            session, "TREA-91", started_after=DAY - timedelta(days=1)
        )
        after_today = await investments.running_investments(session, "TREA-91", started_after=DAY)

    ids = [item.investment_id for item in running]
    assert {first.id, second.id} <= set(ids)
    assert [(i.start_date, i.investment_id) for i in running] == sorted(
        (i.start_date, i.investment_id) for i in running
    )
    assert {i.product_code for i in running} == {"TREA-91"}
    assert {first.id, second.id} <= {item.investment_id for item in since_yesterday}
    assert not {first.id, second.id} & {item.investment_id for item in after_today}
    newer = second
    found = next(item for item in running if item.investment_id == newer.id)
    assert found.client_id == newer.client_id
    assert found.principal == Money(1_000_000, Currency.UGX)
    assert found.maturity_date == newer.maturity_date


async def test_maturing_lists_investments_due_in_the_range_inclusive(
    app_database: Database, worker_database: Database
) -> None:
    investment = await started(app_database, worker_database, PRESENT)
    due = investment.maturity_date
    assert due is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        exact = await investments.maturing(session, due, due)
        before = await investments.maturing(session, due - timedelta(days=7), due - timedelta(1))

    assert investment.id in {item.investment_id for item in exact}
    assert {item.maturity_date for item in exact} == {due}
    assert investment.id not in {item.investment_id for item in before}
