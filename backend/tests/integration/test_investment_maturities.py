"""The staff view of the maturity run against real PostgreSQL: each day's outcome, the terms
the run has not closed, and money held for the client.

The database is shared with other tests, so the day counts are compared before and after.
"""

from datetime import date, timedelta

import pytest

from app.infrastructure.database import Database
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity
from app.shared.actors import SYSTEM
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from tests.support.investments import (
    PRESENT,
    approved_client,
    at,
    pay,
    request,
    started,
    today,
)
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

DAY = today()
# Two days after a 91 day term that starts today ends.
LOOKED = DAY + timedelta(days=93)


def day(offset: int) -> date:
    return DAY + timedelta(days=offset)


async def look(app_database: Database) -> investments.Maturities:
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        return await investments.maturities(session, staff, at(LOOKED))


def due_on(found: investments.Maturities, on: date) -> investments.MaturityDay | None:
    return next((d for d in found.days if d.day == on and d.currency is Currency.UGX), None)


def counts(found: investments.Maturities, on: date) -> tuple[int, ...]:
    d = due_on(found, on)
    if d is None:
        return (0, 0, 0, 0, 0, 0, 0)
    return (
        d.investments,
        d.principal.minor,
        d.rolled_over,
        d.paid_out,
        d.held,
        d.open_roll_over,
        d.open_pay_out,
    )


async def test_each_day_says_what_its_terms_became_and_what_needs_a_person(
    app_database: Database, worker_database: Database
) -> None:
    before = counts(await look(app_database), day(91))
    rolled = await started(app_database, worker_database, PRESENT)
    client = await approved_client(app_database, PRESENT)
    to_pay = await request(app_database, client, PRESENT, at_maturity=AtMaturity.PAY_OUT)
    await pay(worker_database, to_pay, to_pay.amount, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(session, to_pay.id, confirmed_on=DAY, clock=PRESENT)
    left = await started(app_database, worker_database, PRESENT)
    for investment in (rolled, to_pay):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await investments.mature_investment(session, investment.id, day(91), at(day(91)))

    found = await look(app_database)

    assert found.today == LOOKED
    amount = rolled.amount.minor + to_pay.amount.minor + left.amount.minor
    after = counts(found, day(91))
    # Three more terms end that day: one rolled over, one held, one the run has not closed.
    assert tuple(a - b for a, b in zip(after, before, strict=True)) == (3, amount, 1, 0, 1, 1, 0)
    assert left.id in [i.id for i in found.not_closed]
    assert rolled.id not in [i.id for i in found.not_closed + found.held]
    held = next(i for i in found.held if i.id == to_pay.id)
    assert held.payout_refusal == "no_destination"
    # The rolled over term's new one ends 91 days later, past the window.
    assert all(d.day <= day(93 + 14) for d in found.days)
    assert all(d.day >= day(93 - 7) for d in found.days)


async def test_only_staff_may_look(app_database: Database) -> None:
    client = await approved_client(app_database, PRESENT)
    async with app_database.actor_transaction(client) as session:
        with pytest.raises(ApplicationError) as refused:
            await investments.maturities(session, client, at(LOOKED))
    assert refused.value.code == "forbidden"
