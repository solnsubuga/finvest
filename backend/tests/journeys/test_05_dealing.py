"""Clients' money is invested in Treasury bills: an order is prepared, approved, dealt and
settled by different people, and the bills are held."""

from datetime import timedelta

import pytest

from app.infrastructure.database import Database
from app.modules.dealing import holdings
from app.modules.dealing import orders as dealing
from app.modules.dealing.domain.orders import Fill, OrderKind, OrderStatus
from app.modules.dealing.domain.settlement import BANK_TRUST, BILLS
from app.modules.ledger.domain.entries import AccountKind
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.journeys.money import (
    TrustBank,
    balance,
    invest,
    pay_by_bank_transfer,
    today,
    ugx,
)
from tests.journeys.onboarding import onboarded
from tests.support.books import audit_for, missing
from tests.support.journey import World

pytestmark = pytest.mark.journey

FACE = 3_000_000
COST = 2_910_000


async def test_bills_are_bought_with_clients_money_by_four_eyes(
    world: World, migrator_database: Database, bank: TrustBank
) -> None:
    # A client's 3,000,000 arrives in the trust bank account by transfer.
    client = await onboarded(world)
    requested = await invest(world, client, FACE)
    importer = await world.staff("finance")
    sent_on = await bank.receive(world, importer, f"FV{client.id[-6:]}", FACE)
    payment = await pay_by_bank_transfer(
        world,
        client,
        requested.id,
        FACE,
        sent_on=sent_on,
        matcher=await world.staff("finance"),
        approver=await world.staff("finance"),
    )
    assert payment["status"] == "confirmed", payment
    trust_before = await balance(world, BANK_TRUST, kind=AccountKind.ASSET)

    preparer, approver = await world.staff("operations"), await world.staff("finance")
    async with world.api_database.actor_transaction(preparer.actor) as session:
        order = await dealing.prepare_order(
            session,
            preparer.actor,
            kind=OrderKind.AUCTION_BID,
            product_code="TREA-91",
            face=ugx(FACE),
            clock=world.clock,
        )
    # Nobody approves their own order.
    with pytest.raises(ApplicationError):
        async with world.api_database.actor_transaction(preparer.actor) as session:
            await dealing.approve_order(session, preparer.actor, order.id, world.clock)
    async with world.api_database.actor_transaction(approver.actor) as session:
        await dealing.approve_order(session, approver.actor, order.id, world.clock)

    # The auction result, from the custodian's note, recorded by one person and confirmed by
    # another, which settles it.
    matures_on = today(world) + timedelta(days=91)
    result = Fill(ugx(FACE), ugx(COST), today(world), matures_on, f"CSD-{push_id()[-8:]}")
    async with world.api_database.actor_transaction(preparer.actor) as session:
        await dealing.record_result(session, preparer.actor, order.id, result, world.clock)
    with pytest.raises(ApplicationError):
        async with world.api_database.actor_transaction(preparer.actor) as session:
            await dealing.confirm_result(session, preparer.actor, order.id, world.clock)
    async with world.api_database.actor_transaction(approver.actor) as session:
        settled = await dealing.confirm_result(session, approver.actor, order.id, world.clock)

    assert settled.status is OrderStatus.SETTLED, settled.status
    assert await balance(world, BANK_TRUST, kind=AccountKind.ASSET) == trust_before - COST
    assert await balance(world, BILLS, kind=AccountKind.ASSET) >= COST
    async with world.api_database.actor_transaction(approver.actor) as session:
        held = await holdings.holdings_on(session, today(world))
        moves = await holdings.expected_bank_movements(
            session, date_from=today(world), date_to=today(world)
        )
    mine = [h for h in held if result.custodian_reference in h.custodian_references]
    assert [(h.product_code, h.matures_on, h.face_minor, h.cost_minor) for h in mine] == [
        ("TREA-91", matures_on, FACE, COST)
    ]
    assert any(move.amount_minor == -COST for move in moves), moves
    trail = await audit_for(migrator_database, [order.id])
    assert (
        missing(
            trail,
            "dealing.order.prepared",
            "dealing.order.approved",
            "dealing.order.result_recorded",
            "dealing.order.settled",
        )
        == []
    )
    assert {row.actor_id for row in trail} == {preparer.id, approver.id}
