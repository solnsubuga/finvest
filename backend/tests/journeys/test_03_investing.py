"""An investment earns interest each day, then rolls over or pays out at maturity."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.infrastructure.database import Database
from app.modules.investments import accrual
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity, InvestmentStatus
from app.modules.investments.holdings import HOLDING
from app.modules.ledger import service as ledger
from app.modules.payouts import payouts
from app.modules.payouts.domain.destinations import DestinationError
from app.modules.payouts.domain.posting import funding_account
from app.shared.actors import SYSTEM
from app.shared.business_day import KAMPALA
from app.shared.errors import ApplicationError
from tests.journeys.gaps import expected_missing
from tests.journeys.money import balance, invest, investment, pay_by_mobile_money
from tests.journeys.onboarding import onboarded
from tests.journeys.payouts import add_number, check_name
from tests.support.books import audit_for, missing
from tests.support.journey import Person, World, a_phone

pytestmark = pytest.mark.journey

INTEREST_RUN = "investments.accrue_interest"
MATURITY_RUN = "investments.mature"


def kampala(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), KAMPALA).astimezone(UTC)


async def running(world: World, client: Person, amount: int, **choice: AtMaturity) -> str:
    requested = await invest(world, client, amount, **choice)
    payment = await pay_by_mobile_money(world, client, requested.id)
    assert payment["status"] == "confirmed", payment
    started = await investment(world, requested.id)
    assert started.status is InvestmentStatus.ACTIVE
    return started.id


async def earned(world: World, investment_id: str) -> int:
    found = await investment(world, investment_id)
    return await balance(
        world,
        accrual.INTEREST_ACCRUED,
        owner=found.client_id,
        reference=found.id,
    )


async def test_interest_is_paid_each_day_and_the_investment_rolls_over(world: World) -> None:
    client = await onboarded(world)
    investment_id = await running(world, client, 1_000_000)
    started = await investment(world, investment_id)
    assert started.start_date is not None and started.maturity_date is not None

    # The scheduler's daily run, three nights in a row. Each pays one day.
    paid = []
    for night in range(1, 4):
        await world.fire(INTEREST_RUN, kampala(started.start_date + timedelta(days=night), 0, 30))
        paid.append(await earned(world, investment_id))
    assert 0 < paid[0] < paid[1] < paid[2]
    assert paid[1] - paid[0] in (paid[0], paid[0] + 1, paid[0] - 1)
    # Running a night again pays nothing more.
    await world.fire(INTEREST_RUN, kampala(started.start_date + timedelta(days=3), 0, 30))
    assert await earned(world, investment_id) == paid[2]

    # The night after the term ends, the maturity run rolls it over with its interest.
    await world.fire(MATURITY_RUN, kampala(started.maturity_date, 1))
    matured = await investment(world, investment_id)
    assert matured.status is InvestmentStatus.MATURED and matured.rolled_into_id
    rolled = await investment(world, matured.rolled_into_id)
    assert rolled.status is InvestmentStatus.ACTIVE
    assert rolled.start_date == started.maturity_date
    assert rolled.amount.minor == 1_000_000 + matured.accrued_net.minor
    assert rolled.amount.minor > 1_000_000
    assert await balance(world, HOLDING, owner=client.id, reference=investment_id) == 0
    assert await earned(world, investment_id) == 0
    assert (
        await balance(world, HOLDING, owner=client.id, reference=rolled.id) == rolled.amount.minor
    )


async def test_a_matured_investment_pays_out_to_a_number_in_the_clients_own_name(
    world: World, migrator_database: Database
) -> None:
    checker = await world.staff("operations")
    client = await onboarded(world)
    # KYC verified the client as NAKATO Amara. A number registered to someone else is
    # refused, whoever checks it; the client's own is approved.
    other = await add_number(world, client, "0772" + a_phone()[-6:], "Okello Peter")
    with pytest.raises(DestinationError) as refused:
        await check_name(world, checker, client, other, proof_name="OKELLO Peter")
    assert refused.value.code == "name_different"
    spelt = await add_number(world, client, "0772" + a_phone()[-6:], "Nakate Amara")
    with pytest.raises(DestinationError):
        await check_name(world, checker, client, spelt, proof_name="NAKATE Amara")
    own = await add_number(world, client, "0772" + a_phone()[-6:], "Amara Nakato")
    approved = await check_name(world, checker, client, own, proof_name="Nakato  amara")
    assert approved.status.value == "approved"

    investment_id = await running(world, client, 600_000, at_maturity=AtMaturity.PAY_OUT)
    async with world.api_database.actor_transaction(client.actor) as session:
        # A refused number cannot be chosen.
        with pytest.raises(ApplicationError) as not_usable:
            await investments.choose_at_maturity(
                session,
                client.actor,
                investment_id,
                at_maturity=AtMaturity.PAY_OUT,
                destination_id=other,
                clock=world.clock,
            )
        assert not_usable.value.code in ("not_found", "validation_failed"), not_usable.value.code
    async with world.api_database.actor_transaction(client.actor) as session:
        await investments.choose_at_maturity(
            session,
            client.actor,
            investment_id,
            at_maturity=AtMaturity.PAY_OUT,
            destination_id=own,
            clock=world.clock,
        )
    started = await investment(world, investment_id)
    assert started.maturity_date is not None

    await world.fire(MATURITY_RUN, kampala(started.maturity_date, 1))

    matured = await investment(world, investment_id)
    assert matured.status is InvestmentStatus.MATURED, matured
    assert matured.payout_refusal is None and matured.payout_id is not None
    async with world.worker_database.actor_transaction(SYSTEM) as session:
        payout = await payouts.get_payout(session, matured.payout_id)
    assert payout.destination_id == own
    assert payout.amount.minor == 600_000 + matured.accrued_net.minor
    # The money left the holding for the payout; nothing is left waiting for a person.
    assert await balance(world, HOLDING, owner=client.id, reference=investment_id) == 0
    async with world.worker_database.actor_transaction(SYSTEM) as session:
        pending = await ledger.open_account(
            session, funding_account(client.id, investment_id, payout.amount.currency), world.clock
        )
        assert (await ledger.get_balance(session, pending.id)).minor == 0

    trail = await audit_for(
        migrator_database, [client.id, own, other, spelt, payout.id, investment_id]
    )
    expected = (
        "investments.requested",
        "investments.maturity_chosen",
        "investments.started",
        "investments.matured",
        "payouts.destination_added",
        "payouts.destination_code_confirmed",
        "payouts.destination_proof_submitted",
        "payouts.destination_approved",
        "payouts.payout_requested",
    )
    assert missing(trail, *expected) == expected_missing(*expected)
    approvals = [row for row in trail if row.action == "payouts.destination_approved"]
    assert [row.resource_id for row in approvals] == [own]
