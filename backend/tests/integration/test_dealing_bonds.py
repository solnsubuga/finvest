"""The dealing desk's bonds against real PostgreSQL: buying and selling bonds of an issue at the
custodian by two people, the position against the face value clients hold, the buffer, the
maturity run, and what the database refuses whatever the application does.

Bond holdings start at the real time, as the bond tests' do. Desk orders use the settlement
tests' day, and each test makes its own issue, so no test sees another's bonds.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.dealing import bond_book, holdings, maturities, withdrawals
from app.modules.dealing import desk as desk_view
from app.modules.dealing import orders as dealing
from app.modules.dealing.domain.orders import Fill, Instrument, OrderKind, OrderStatus
from app.modules.dealing.domain.position import Cover
from app.modules.dealing.domain.settlement import (
    BANK_TRUST,
    BILLS,
    BONDS,
    MATURITY_LOSSES,
    SALE_GAINS,
    Settlement,
    settlement_entry,
)
from app.modules.dealing.domain.settlement import accounts as settlement_accounts
from app.modules.ledger import service as ledger
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.integration.test_bond_holdings import live_issue, now
from tests.integration.test_bond_maturity import active, dates_for, maturity_day
from tests.integration.test_dealing_maturities import on
from tests.integration.test_dealing_settlement import (
    NOW,
    TODAY,
    M,
    balance,
    confirm,
    entry_lines,
    fund_bank,
    refused,
)
from tests.support.investments import approved_client
from tests.support.ledger import staff as new_staff
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

# The live_issue default.
MATURES = date(2037, 5, 14)


def bond_fill(face: int, amount: int, *, matures_on: date = MATURES) -> Fill:
    return Fill(ugx(face), ugx(amount), TODAY, matures_on, f"CSD-{push_id()[-8:]}")


async def bond_order(
    database: Database,
    code: str,
    face: int,
    *,
    kind: OrderKind = OrderKind.SECONDARY_PURCHASE,
) -> tuple[dealing.OrderView, Actor]:
    """An approved order for bonds of `code`, and who prepared it."""
    preparer, approver = new_staff(), new_staff()
    async with database.actor_transaction(preparer) as session:
        order = await dealing.prepare_order(
            session,
            preparer,
            kind=kind,
            product_code=code,
            face=ugx(face),
            clock=NOW,
            instrument=Instrument.BOND,
        )
    async with database.actor_transaction(approver) as session:
        await dealing.approve_order(session, approver, order.id, NOW)
    return order, preparer


async def recorded(
    database: Database, code: str, result: Fill, *, kind: OrderKind = OrderKind.SECONDARY_PURCHASE
) -> dealing.OrderView:
    order, preparer = await bond_order(database, code, result.face.minor, kind=kind)
    async with database.actor_transaction(preparer) as session:
        return await dealing.record_result(session, preparer, order.id, result, NOW)


async def bought(database: Database, code: str, face: int, cost: int) -> dealing.OrderView:
    await fund_bank(database, cost)
    order = await recorded(database, code, bond_fill(face, cost))
    return await confirm(database, order.id)


async def line_of(database: Database, code: str) -> bond_book.BondLine:
    async with database.actor_transaction(new_staff()) as session:
        lines = await bond_book.bond_positions(session, Currency.UGX, TODAY)
    return next(line for line in lines if line.code == code)


async def test_two_people_buy_a_bond_into_dealing_bonds_and_it_covers_the_holding(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    client = await approved_client(app_database, clock)
    async with live_issue(app_database, clock) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        before = await line_of(app_database, code)
        async with app_database.actor_transaction(new_staff()) as session:
            to_place_before = (await withdrawals.buffer_now(session, Currency.UGX, NOW)).to_place
        bonds_before = await balance(app_database, BONDS)

        settled = await bought(app_database, code, 1 * M, 1_012_500)

        after = await line_of(app_database, code)
        staff = new_staff()
        async with app_database.actor_transaction(staff) as session:
            to_place_after = (await withdrawals.buffer_now(session, Currency.UGX, NOW)).to_place
            desk = await desk_view.desk(session, staff, Currency.UGX, NOW)
            events = await events_for_resource(session, dealing.ORDER_RESOURCE, settled.id)

    # Before: the client's face value has no bond behind it, and their money is not the buffer's.
    assert (before.isin, before.holders, before.position.clients_face) == (
        f"UGT{code[5:]}0",
        1,
        ugx(1 * M),
    )
    assert before.position.cover is Cover.BID_NEEDED
    assert before.position.to_place == holding.cost
    # Bought above face value, which a bond may be, and posted to its own asset.
    assert (settled.status, settled.instrument) == (OrderStatus.SETTLED, Instrument.BOND)
    assert await entry_lines(app_database, settled) == {BONDS: 1_012_500, BANK_TRUST: -1_012_500}
    assert await balance(app_database, BONDS) == bonds_before + 1_012_500
    assert (after.position.face_held, after.position.cost_held) == (ugx(1 * M), ugx(1_012_500))
    assert after.position.cover is Cover.COVERED
    assert after.position.to_place == ugx(0)
    assert to_place_before - to_place_after == holding.cost
    # The desk shows it with the bonds, by the issue's name, and never as a bill.
    assert any(line.code == code for line in desk.bonds)
    assert all(position.product_code != code for position in desk.positions)
    assert {"kind": "secondary_purchase", "product": code, "instrument": "bond"} == (
        events[-1].details
    )


async def test_an_open_bond_order_counts_as_on_order(app_database: Database) -> None:
    clock = now()
    async with live_issue(app_database, clock) as code:
        order, _ = await bond_order(app_database, code, 2 * M)
        line = await line_of(app_database, code)
        async with app_database.actor_transaction(new_staff()) as session:
            bills_on_order = await dealing.on_order(session)
    assert line.position.on_order == ugx(2 * M)
    assert code not in bills_on_order
    assert order.instrument is Instrument.BOND


async def test_a_bond_result_matures_on_its_issues_date(app_database: Database) -> None:
    clock = now()
    async with live_issue(app_database, clock) as code:
        order, preparer = await bond_order(app_database, code, 1 * M)
        with pytest.raises(ApplicationError) as error:
            async with app_database.actor_transaction(preparer) as session:
                await dealing.record_result(
                    session,
                    preparer,
                    order.id,
                    bond_fill(1 * M, 990_000, matures_on=MATURES - timedelta(days=1)),
                    NOW,
                )
    assert refused(error) == ("validation_failed", [("matures_on", "wrong_maturity")])


@pytest.mark.parametrize("code", ["BOND-NONE", "TREA-182"])
async def test_a_bond_order_names_an_issue_in_the_catalogue(
    app_database: Database, code: str
) -> None:
    staff = new_staff()
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(staff) as session:
            await dealing.prepare_order(
                session,
                staff,
                kind=OrderKind.AUCTION_BID,
                product_code=code,
                face=ugx(1 * M),
                clock=NOW,
                instrument=Instrument.BOND,
            )
    assert error.value.code == "not_found"


async def test_selling_bonds_takes_bonds_and_pays_no_early_withdrawals(
    app_database: Database,
) -> None:
    clock = now()
    async with live_issue(app_database, clock) as code:
        await bought(app_database, code, 3 * M, 2_955_000)
        surplus = await line_of(app_database, code)
        with pytest.raises(ApplicationError) as error:
            await recorded(
                app_database, code, bond_fill(4 * M, 4 * M), kind=OrderKind.SECONDARY_SALE
            )
        sale = await recorded(
            app_database, code, bond_fill(1 * M, 1_001_000), kind=OrderKind.SECONDARY_SALE
        )
        settled = await confirm(app_database, sale.id)
        after = await line_of(app_database, code)
        async with app_database.actor_transaction(new_staff()) as session:
            jobs = (
                await session.execute(
                    text("SELECT count(*) FROM jobs WHERE idempotency_key = :key"),
                    {"key": f"sale:{settled.id}"},
                )
            ).scalar_one()

    # Nobody holds the issue, so every bond held is to sell.
    assert (surplus.position.cover, surplus.position.to_sell) == (Cover.SURPLUS, ugx(3 * M))
    assert refused(error) == ("validation_failed", [("face", "more_than_held")])
    assert await entry_lines(app_database, settled) == {
        BANK_TRUST: 1_001_000,
        BONDS: -985_000,
        SALE_GAINS: -16_000,
    }
    assert (after.position.face_held, after.position.cost_held) == (ugx(2 * M), ugx(1_970_000))
    assert jobs == 0


async def test_bonds_and_bills_of_one_code_are_held_apart(app_database: Database) -> None:
    """A sale of bills never takes bonds, whatever the code."""
    clock = now()
    async with live_issue(app_database, clock) as code:
        await bought(app_database, code, 1 * M, 990_000)
        async with app_database.actor_transaction(new_staff()) as session:
            bills = await holdings.face_held(session, code, Currency.UGX, on=TODAY)
            bonds = await holdings.face_held(
                session, code, Currency.UGX, on=TODAY, instrument=Instrument.BOND
            )
            held = [h for h in await holdings.holdings_on(session, TODAY) if h.product_code == code]
    assert (bills, bonds) == (ugx(0), ugx(1 * M))
    assert [(h.instrument, h.matures_on, h.face_minor) for h in held] == [
        (Instrument.BOND, MATURES, 1 * M)
    ]


async def test_the_issuer_repays_bonds_into_the_trust_account_at_maturity(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    matures = maturity_day()
    async with live_issue(app_database, clock, **dates_for(matures)) as code:
        await fund_bank(app_database, 1_004_000)
        result = await recorded(app_database, code, bond_fill(1 * M, 1_004_000, matures_on=matures))
        order = await confirm(app_database, result.id)
        await maturities_run(worker_database, matures)
        async with app_database.actor_transaction(new_staff()) as session:
            lot = (
                await session.execute(
                    text("SELECT id FROM dealing_lots WHERE order_id = :id"), {"id": order.id}
                )
            ).scalar_one()
            entry_id = (
                await session.execute(
                    text("SELECT ledger_entry_id FROM dealing_lot_maturities WHERE lot_id = :id"),
                    {"id": lot},
                )
            ).scalar_one()
            entry = await ledger.get_entry(session, entry_id)
            codes = {
                (
                    await session.execute(
                        text("SELECT code FROM ledger_accounts WHERE id = :id"),
                        {"id": line.account_id},
                    )
                ).scalar_one(): line.amount.minor
                for line in entry.lines
            }
    # Bought above face value, so the repayment books a loss against the cost.
    assert codes == {BANK_TRUST: 1 * M, BONDS: -1_004_000, MATURITY_LOSSES: 4_000}


async def maturities_run(worker: Database, day: date) -> None:
    async with worker.actor_transaction(SYSTEM) as session:
        await maturities.record_maturities(session, on(day), limit=500)


async def test_the_database_refuses_a_bond_settled_into_bills(app_database: Database) -> None:
    clock = now()
    async with live_issue(app_database, clock) as code:
        await fund_bank(app_database, 990_000)
        order = await recorded(app_database, code, bond_fill(1 * M, 990_000))
        assert order.result is not None
        grace = new_staff()
        with pytest.raises(DBAPIError, match="needs the ledger entry and bills"):
            async with app_database.actor_transaction(grace) as session:
                ids = {
                    found: (await ledger.open_account(session, spec, NOW)).id
                    for found, spec in settlement_accounts(Currency.UGX).items()
                }
                # As a bill: dealing.bills, not dealing.bonds.
                draft = settlement_entry(
                    Settlement(order.id, order.kind, order.result, order.result.amount), ids
                )
                assert ids[BILLS] in {line.account_id for line in draft.lines}
                entry = await ledger.post_entry(session, draft, NOW)
                await session.execute(
                    text(
                        "INSERT INTO dealing_lots (id, order_id, product_code, currency, "
                        "face_minor, cost_minor, settled_on, matures_on, custodian_reference) "
                        "SELECT :id, id, product_code, currency, result_face_minor, "
                        "result_amount_minor, result_settled_on, result_matures_on, "
                        "custodian_reference FROM dealing_orders WHERE id = :order_id"
                    ),
                    {"id": push_id(), "order_id": order.id},
                )
                await session.execute(
                    text(
                        "UPDATE dealing_orders SET status = 'settled', settled_by = :by, "
                        "settled_at = now(), settlement_entry_id = :entry WHERE id = :id"
                    ),
                    {"by": grace.id, "entry": entry.id, "id": order.id},
                )


async def test_what_an_order_deals_never_changes(app_database: Database) -> None:
    clock = now()
    async with live_issue(app_database, clock) as code:
        order, _ = await bond_order(app_database, code, 1 * M)
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(new_staff()) as session:
                await session.execute(
                    text("UPDATE dealing_orders SET instrument = 'bill' WHERE id = :id"),
                    {"id": order.id},
                )
