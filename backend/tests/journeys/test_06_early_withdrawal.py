"""A client withdraws a running investment early: investing moves the money to payouts, the
dealing desk sells bills to fund it, and investing creates the payout.

The buffer pays small withdrawals at once, but it is cash beyond the client money waiting to be
placed, and every shilling here is client money. So this withdrawal, larger than the buffer may
pay, waits for a sale, as it would on a day the buffer is empty.
"""

import secrets
from datetime import timedelta

import pytest

from app.infrastructure.database import Database
from app.modules.dealing import orders as dealing
from app.modules.dealing import withdrawals as funding
from app.modules.dealing.domain.orders import Fill, OrderKind, OrderStatus
from app.modules.dealing.domain.withdrawals import FundingStatus, Source
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import InvestmentStatus
from app.modules.payouts import destinations, payouts
from app.modules.payouts.domain.destinations import BankAccount, ProofKind
from app.shared.actors import SYSTEM
from app.shared.push_ids import push_id
from tests.journeys.gaps import expected_missing
from tests.journeys.money import TrustBank, invest, investment, pay_by_bank_transfer, today, ugx
from tests.journeys.onboarding import onboarded
from tests.journeys.payouts import approve_check, check_name
from tests.support.books import audit_for, missing
from tests.support.journey import Person, World

pytestmark = pytest.mark.journey

# The bill bought with the client's money, and sold later for a little more than it cost.
FACE = 10_000_000
COST = 9_700_000
SOLD_FOR = 9_800_000
# Above the UGX 5,000,000 the buffer may pay for one withdrawal, so it waits for a sale.
PRINCIPAL = 6_000_000


async def add_bank_account(world: World, client: Person, holder_name: str) -> str:
    """The client adds a bank account, confirms the texted code and uploads the bank's letter."""
    number = f"6{secrets.randbelow(10**9):09d}"
    async with world.api_database.actor_transaction(client.actor) as session:
        view = await destinations.add_destination(
            session, client.actor, BankAccount("stanbic", number), holder_name, world.clock
        )
        await destinations.confirm_code(session, client.actor, view.id, world.clock)
        await destinations.submit_proof(
            session, client.actor, view.id, ProofKind.BANK_LETTER, f"proof-{view.id}", world.clock
        )
    return view.id


async def deal(
    world: World, preparer: Person, approver: Person, kind: OrderKind, fill: Fill
) -> dealing.OrderView:
    """One order through its four-eyes steps: prepared, approved, result recorded, settled."""
    async with world.api_database.actor_transaction(preparer.actor) as session:
        order = await dealing.prepare_order(
            session,
            preparer.actor,
            kind=kind,
            product_code="TREA-91",
            face=fill.face,
            clock=world.clock,
        )
    async with world.api_database.actor_transaction(approver.actor) as session:
        await dealing.approve_order(session, approver.actor, order.id, world.clock)
    async with world.api_database.actor_transaction(preparer.actor) as session:
        await dealing.record_result(session, preparer.actor, order.id, fill, world.clock)
    async with world.api_database.actor_transaction(approver.actor) as session:
        settled = await dealing.confirm_result(session, approver.actor, order.id, world.clock)
    assert settled.status is OrderStatus.SETTLED, settled.status
    return settled


async def paid_by_transfer(
    world: World, bank: TrustBank, client: Person, amount: int
) -> investments.Investment:
    requested = await invest(world, client, amount)
    importer = await world.staff("finance")
    sent_on = await bank.receive(world, importer, f"FV{requested.id[-6:]}", amount)
    payment = await pay_by_bank_transfer(
        world,
        client,
        requested.id,
        amount,
        sent_on=sent_on,
        matcher=await world.staff("finance"),
        approver=await world.staff("finance"),
    )
    assert payment["status"] == "confirmed", payment
    started = await investment(world, requested.id)
    assert started.status is InvestmentStatus.ACTIVE
    return started


async def test_an_early_withdrawal_is_funded_by_a_sale_and_paid_out(
    world: World, migrator_database: Database, bank: TrustBank
) -> None:
    client = await onboarded(world)
    await paid_by_transfer(world, bank, client, FACE)
    started = await paid_by_transfer(world, bank, client, PRINCIPAL)

    # The desk places the money in a 91 day bill.
    preparer, approver = await world.staff("finance"), await world.staff("finance")
    matures_on = today(world) + timedelta(days=91)
    reference = f"CSD-{push_id()[-8:]}"
    await deal(
        world,
        preparer,
        approver,
        OrderKind.AUCTION_BID,
        Fill(ugx(FACE), ugx(COST), today(world), matures_on, reference),
    )

    checker, second = await world.staff("operations"), await world.staff("operations")
    # More than mobile money takes in one payout, so to the client's bank account.
    own = await add_bank_account(world, client, "Amara Nakato")
    await check_name(world, checker, own, proof_name="NAKATO Amara")
    await approve_check(world, second, own)
    # A number approved today is not used yet; two days later it is.
    world.clock.advance(days=2)

    async with world.api_database.actor_transaction(client.actor) as session:
        await investments.request_withdrawal(
            session,
            client.actor,
            started.id,
            destination_id=own,
            idempotency_key=f"key-{push_id()}",
            clock=world.clock,
        )
    # The worker withdraws it and hands it to the desk, which leaves it waiting for a sale.
    await world.drain()
    withdrawn = await investment(world, started.id)
    assert withdrawn.status is InvestmentStatus.WITHDRAWN, withdrawn
    assert withdrawn.withdrawal_payout_id is None
    async with world.worker_database.actor_transaction(SYSTEM) as session:
        waiting = await funding.funding_for(session, started.id)
    assert waiting is not None
    assert (waiting.source, waiting.status) == (Source.SALE, FundingStatus.WAITING)

    # The desk sells the bill, by four eyes. Once it settles, the worker funds the withdrawal
    # from what it raised, and investing pays the client on the desk's announcement.
    sale = await deal(
        world,
        preparer,
        approver,
        OrderKind.SECONDARY_SALE,
        Fill(ugx(FACE), ugx(SOLD_FOR), today(world), matures_on, f"CSD-{push_id()[-8:]}"),
    )
    await world.drain()

    paid = await investment(world, started.id)
    assert paid.withdrawal_payout_id is not None
    async with world.worker_database.actor_transaction(SYSTEM) as session:
        payout = await payouts.get_payout(session, paid.withdrawal_payout_id)
        done = await funding.funding_for(session, started.id)
    assert done is not None and done.status is FundingStatus.FUNDED
    assert (done.sale_order_id, done.funded_by) == (sale.id, None)
    assert payout.amount == done.amount == waiting.amount
    assert payout.destination_id == own
    assert await world.failed_jobs(world.clock.now() - timedelta(days=3)) == []

    trail = await audit_for(migrator_database, [started.id, payout.id, done.id])
    expected = (
        "investments.withdrawal_requested",
        "investments.withdrawn_early",
        "dealing.withdrawal.funded_from_sale",
        "payouts.payout_requested",
    )
    assert missing(trail, *expected) == expected_missing(*expected)
