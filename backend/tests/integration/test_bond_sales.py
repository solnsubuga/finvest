"""Bond sales against real PostgreSQL: a client's holding sold early by two people, paid once.

Each test makes an issue that matures seven months from now, starts a holding in it today, and
sells it today: the client asks, one member of staff prices it, another approves, and the worker
pays it. Its one coupon before maturity is a month away, so nothing crosses a coupon date.
"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.operations import operations_signals
from app.modules.bonds import coupons, holdings, maturity, sales
from app.modules.bonds.domain.coupons import HoldReason
from app.modules.bonds.domain.holdings import HoldingStatus
from app.modules.bonds.domain.issues import accrued_interest, coupon_dates, tax_on
from app.modules.bonds.domain.sales import PayoutStatus, SaleStatus
from app.modules.ledger import service as ledger
from app.modules.payouts import destinations
from app.modules.tax import withholding
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.integration.test_bond_holdings import live_issue, now, ugx
from tests.integration.test_bond_maturity import active, dates_for, maturity_day, on, status_of
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.investments import approved_client, approved_destination
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

PRICE = 972_500
# A holding the coupon and maturity runs would look at.
WAITING_FOR_A_COUPON = (
    f"SELECT h.id FROM bond_holdings h WHERE h.id = :id AND {coupons.SALE_APPROVED}"  # noqa: S608
)


def past_safety_wait(clock: Clock) -> FakeClock:
    """Two days on, when a payout number added today is past its first day's safety wait."""
    return FakeClock(clock.now() + timedelta(days=2))


async def ask(
    app_database: Database, client: Actor, holding_id: str, destination_id: str, clock: Clock
) -> sales.SaleView:
    async with app_database.actor_transaction(client) as session:
        return await sales.request_sale(
            session,
            client,
            holding_id,
            idempotency_key=push_id(),
            destination_id=destination_id,
            clock=clock,
        )


async def price(
    app_database: Database, staff: Actor, sale_id: str, clock: Clock, *, at: int = PRICE
) -> sales.SaleView:
    async with app_database.actor_transaction(staff) as session:
        return await sales.price_sale(
            session,
            staff,
            sale_id,
            price=at,
            sold_on=business_date(clock.now()),
            reason="Desk trade 4471",
            clock=clock,
        )


async def approve(
    app_database: Database, staff: Actor, sale_id: str, clock: Clock
) -> sales.SaleView:
    async with app_database.actor_transaction(staff) as session:
        return await sales.approve_sale(session, staff, sale_id, clock)


async def sold_lines(worker: Database, holding_id: str) -> dict[str, int]:
    async with worker.actor_transaction(SYSTEM) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT a.code, l.amount_minor FROM ledger_lines l "
                    "JOIN ledger_accounts a ON a.id = l.account_id "
                    "JOIN ledger_journal_entries e ON e.id = l.entry_id "
                    "WHERE e.entry_type = 'bond.sold' AND e.source_id = :id"
                ),
                {"id": holding_id},
            )
        ).all()
    return {row.code: row.amount_minor for row in rows}


async def test_a_holding_sold_by_two_people_is_paid_the_price_and_interest_after_tax_once(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    today = business_date(clock.now())
    client = await approved_client(app_database, clock)
    destination_id = await approved_destination(app_database, client, clock)
    maker, checker = new_staff(), new_staff()
    async with live_issue(app_database, clock, **dates_for(maturity_day())) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        asked = await ask(app_database, client, holding.id, destination_id, clock)
        assert (asked.status, asked.face, asked.cost) == (
            SaleStatus.REQUESTED,
            holding.face,
            holding.cost,
        )
        priced = await price(app_database, maker, asked.id, clock)
        assert (priced.status, priced.price, priced.sold_on, priced.priced_by) == (
            SaleStatus.PRICED,
            PRICE,
            today,
            maker.id,
        )
        approved = await approve(app_database, checker, asked.id, clock)
        assert (approved.status, approved.decided_by) == (SaleStatus.APPROVED, checker.id)
        async with worker_database.actor_transaction(SYSTEM) as session:
            job = (
                await session.execute(
                    text("SELECT kind, payload FROM jobs WHERE idempotency_key = :key"),
                    {"key": f"bond-sale:{asked.id}"},
                )
            ).one()
        assert (job.kind, job.payload) == (sales.SETTLE_KIND, {"sale_id": asked.id})

        # The coupon run and the maturity run leave an approved holding to its sale.
        async with worker_database.actor_transaction(SYSTEM) as session:
            issue = holding.issue
            assert (
                await session.execute(
                    text(WAITING_FOR_A_COUPON),
                    {"id": holding.id},
                )
            ).first() is None
            day = issue.maturity_date
            assert await maturity.repay(session, holding.id, day, on(day)) is None

        async with worker_database.actor_transaction(SYSTEM) as session:
            sold = await sales.settle(session, asked.id, past_safety_wait(clock))
        assert sold is not None
        accrued = accrued_interest(holding.issue, holding.face, today)
        tax = tax_on(holding.issue, accrued)
        assert (sold.status, sold.clean, sold.accrued, sold.tax) == (
            SaleStatus.SOLD,
            ugx(972_500),
            accrued,
            tax,
        )
        assert sold.amount == ugx(972_500) + accrued - tax
        assert (sold.payout_status, sold.payout_refusal) == (PayoutStatus.PAID_OUT, None)
        async with worker_database.actor_transaction(SYSTEM) as session:
            payout = (
                await session.execute(
                    text(
                        "SELECT amount_minor, client_id, source_reference FROM payouts "
                        "WHERE id = :id"
                    ),
                    {"id": sold.payout_id},
                )
            ).one()
        assert (payout.amount_minor, payout.client_id, payout.source_reference) == (
            sold.amount.minor if sold.amount else None,
            client.id,
            asked.id,
        )
        assert await status_of(worker_database, holding.id) is HoldingStatus.SOLD

        # The holding is empty. Sold for less than the clean amount paid: Finvest's income.
        signed = await sold_lines(worker_database, holding.id)
        clean_paid = holding.cost - holding.accrued
        assert signed[sales.BELOW_COST] == -(clean_paid.minor - 972_500)
        assert sales.ABOVE_COST not in signed
        assert signed[maturity.ACCRUED_BOUGHT] == -holding.accrued.minor
        assert sum(signed.values()) == 0
        async with worker_database.actor_transaction(SYSTEM) as session:
            held = await ledger.open_account(session, holdings.holding_account(holding), clock)
            assert (await ledger.get_balance(session, held.id)).is_zero

        # Paid once: settling again changes nothing.
        async with worker_database.actor_transaction(SYSTEM) as session:
            assert await sales.settle(session, asked.id, past_safety_wait(clock)) is None
        assert len(await sold_lines(worker_database, holding.id)) == len(signed)

        # The accrued interest is interest in the figures for URA.
        if accrued.is_positive:
            async with worker_database.actor_transaction(SYSTEM) as session:
                lines = await withholding.by_investment(
                    session,
                    Currency.UGX,
                    date_from=today,
                    date_to=today + timedelta(days=2),
                    investment_ids=[holding.id],
                )
            assert [(t.gross, t.tax) for t in lines] == [(accrued, tax)]

        # The client sees what it paid, and nothing more can be asked of the holding.
        async with app_database.actor_transaction(client) as session:
            mine = await sales.client_sales(session, str(client.id))
        assert [(s.id, s.status, s.amount) for s in mine] == [
            (asked.id, SaleStatus.SOLD, sold.amount)
        ]
        with pytest.raises(ApplicationError) as again:
            await ask(app_database, client, holding.id, destination_id, clock)
        assert [(p.field_path[0], p.code) for p in again.value.fields] == [
            ("holding_id", "holding_not_active")
        ]
        with pytest.raises(ApplicationError) as closed:
            async with app_database.actor_transaction(client) as session:
                await coupons.choose_destination(
                    session, client, holding.id, destination_id=None, clock=clock
                )
        assert closed.value.code == "conflict"


async def test_a_sale_left_approved_raises_the_alert_until_it_is_paid(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    client = await approved_client(app_database, clock)
    destination_id = await approved_destination(app_database, client, clock)
    soon, later = clock.now() + timedelta(minutes=50), clock.now() + timedelta(hours=2)

    async def unsettled() -> tuple[int, int]:
        signals = [await operations_signals(app_database, at) for at in (soon, later)]
        return signals[0]["bond_sales_unsettled"], signals[1]["bond_sales_unsettled"]

    async with live_issue(app_database, clock, **dates_for(maturity_day())) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        asked = await ask(app_database, client, holding.id, destination_id, clock)
        await price(app_database, new_staff(), asked.id, clock)
        before_soon, before_later = await unsettled()
        await approve(app_database, new_staff(), asked.id, clock)
        # Not within the hour the worker has to pay it, and once that hour has passed.
        assert await unsettled() == (before_soon, before_later + 1)
        async with worker_database.actor_transaction(SYSTEM) as session:
            assert await sales.settle(session, asked.id, past_safety_wait(clock)) is not None
        assert await unsettled() == (before_soon, before_later)


async def test_two_people_price_and_decide_and_the_client_cancels_before_a_price(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    client = await approved_client(app_database, clock)
    stranger = await approved_client(app_database, clock)
    destination_id = await approved_destination(app_database, client, clock)
    maker, checker = new_staff(), new_staff()
    async with live_issue(app_database, clock, **dates_for(maturity_day())) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        asked = await ask(app_database, client, holding.id, destination_id, clock)
        # One ask at a time.
        with pytest.raises(ApplicationError) as twice:
            await ask(app_database, client, holding.id, destination_id, clock)
        assert [(p.field_path[0], p.code) for p in twice.value.fields] == [
            ("holding_id", "sale_already_asked")
        ]
        # Nobody else asks for it.
        with pytest.raises(ApplicationError) as theirs:
            await ask(app_database, stranger, holding.id, destination_id, clock)
        assert theirs.value.code == "not_found"

        # A price from the future, or below 50, is refused.
        with pytest.raises(ApplicationError) as future:
            async with app_database.actor_transaction(maker) as session:
                await sales.price_sale(
                    session,
                    maker,
                    asked.id,
                    price=PRICE,
                    sold_on=business_date(clock.now()) + timedelta(days=1),
                    reason="Desk trade",
                    clock=clock,
                )
        assert [(p.field_path[0], p.code) for p in future.value.fields] == [
            ("sold_on", "sold_on_in_future")
        ]
        with pytest.raises(ApplicationError) as cheap:
            await price(app_database, maker, asked.id, clock, at=499_999)
        assert [(p.field_path[0], p.code) for p in cheap.value.fields] == [
            ("price", "price_out_of_range")
        ]

        await price(app_database, maker, asked.id, clock)
        # The client cannot cancel once it is priced, and the maker cannot approve their own.
        with pytest.raises(ApplicationError) as late:
            async with app_database.actor_transaction(client) as session:
                await sales.cancel_sale(session, client, asked.id, clock)
        assert late.value.code == "conflict"
        with pytest.raises(ApplicationError) as own:
            await approve(app_database, maker, asked.id, clock)
        assert own.value.code == "forbidden"

        # Sent back by someone else, with a note. It waits to be priced again.
        with pytest.raises(ApplicationError) as by_maker:
            async with app_database.actor_transaction(maker) as session:
                await sales.send_back_sale(session, maker, asked.id, note="Wrong", clock=clock)
        assert by_maker.value.code == "forbidden"
        async with app_database.actor_transaction(checker) as session:
            back = await sales.send_back_sale(
                session, checker, asked.id, note="The price is the bid, not the trade", clock=clock
            )
        assert (back.status, back.price, back.priced_by, back.note) == (
            SaleStatus.REQUESTED,
            None,
            None,
            "The price is the bid, not the trade",
        )

        # Priced again and withdrawn by the one who priced it; nobody else can withdraw it.
        await price(app_database, checker, asked.id, clock)
        with pytest.raises(ApplicationError) as not_theirs:
            async with app_database.actor_transaction(maker) as session:
                await sales.withdraw_sale(session, maker, asked.id, clock)
        assert not_theirs.value.code == "forbidden"
        async with app_database.actor_transaction(checker) as session:
            withdrawn = await sales.withdraw_sale(session, checker, asked.id, clock)
        assert withdrawn.status is SaleStatus.REQUESTED

        # Before a price, the client cancels, and may ask again.
        async with app_database.actor_transaction(client) as session:
            cancelled = await sales.cancel_sale(session, client, asked.id, clock)
        assert cancelled.status is SaleStatus.CANCELLED
        again = await ask(app_database, client, holding.id, destination_id, clock)
        assert again.status is SaleStatus.REQUESTED
        assert await status_of(worker_database, holding.id) is HoldingStatus.ACTIVE

        # The dealing desk sees both; the client only their own.
        async with app_database.actor_transaction(maker) as session:
            listed = await sales.staff_sales(session, maker, SaleStatus.REQUESTED, 500)
        assert again.id in {s.id for s in listed}
        async with app_database.actor_transaction(stranger) as session:
            assert await sales.client_sales(session, str(stranger.id)) == []


async def test_a_sale_dated_before_a_coupon_that_has_since_passed_is_refused(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    client = await approved_client(app_database, clock)
    destination_id = await approved_destination(app_database, client, clock)
    maker, checker = new_staff(), new_staff()
    async with live_issue(app_database, clock, **dates_for(maturity_day())) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        asked = await ask(app_database, client, holding.id, destination_id, clock)
        await price(app_database, maker, asked.id, clock)
        # Approved on the coupon date: the run may have paid the holding that coupon.
        coupon_day = next(d for d in coupon_dates(holding.issue) if d > business_date(clock.now()))
        with pytest.raises(ApplicationError) as crossed:
            await approve(app_database, checker, asked.id, on(coupon_day))
        assert [(p.field_path[0], p.code) for p in crossed.value.fields] == [
            ("sold_on", "coupon_since_sale")
        ]


async def test_proceeds_with_nowhere_to_go_wait_for_the_client(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    client = await approved_client(app_database, clock)
    destination_id = await approved_destination(app_database, client, clock)
    async with live_issue(app_database, clock, **dates_for(maturity_day())) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        asked = await ask(app_database, client, holding.id, destination_id, clock)
        await price(app_database, new_staff(), asked.id, clock)
        await approve(app_database, new_staff(), asked.id, clock)
        # The number named for the money was removed while the desk sold the bond.
        async with app_database.actor_transaction(client) as session:
            await destinations.remove(session, client, destination_id, clock)
        async with worker_database.actor_transaction(SYSTEM) as session:
            held = await sales.settle(session, asked.id, past_safety_wait(clock))
        assert held is not None and held.amount is not None
        assert (held.payout_status, held.payout_id) == (PayoutStatus.HELD, None)
        assert held.payout_refusal not in (None, HoldReason.NO_DESTINATION)

        other = await approved_destination(app_database, client, clock)
        async with app_database.actor_transaction(client) as session:
            sent = await sales.pay_out_held(
                session, client, asked.id, destination_id=other, clock=past_safety_wait(clock)
            )
        assert (sent.payout_status, sent.payout_refusal) == (PayoutStatus.PAID_OUT, None)
        async with worker_database.actor_transaction(SYSTEM) as session:
            amount = (
                await session.execute(
                    text("SELECT amount_minor FROM payouts WHERE id = :id"), {"id": sent.payout_id}
                )
            ).scalar_one()
        assert amount == held.amount.minor
        with pytest.raises(ApplicationError) as again:
            async with app_database.actor_transaction(client) as session:
                await sales.pay_out_held(
                    session, client, asked.id, destination_id=other, clock=clock
                )
        assert again.value.code == "conflict"


async def test_concurrent_asks_and_settlements_sell_a_holding_once(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    client = await approved_client(app_database, clock)
    destination_id = await approved_destination(app_database, client, clock)
    async with live_issue(app_database, clock, **dates_for(maturity_day())) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        barrier = AsyncBarrier(2)

        async def ask_once() -> str:
            async with app_database.actor_transaction(client) as session:
                await barrier.wait()
                try:
                    view = await sales.request_sale(
                        session,
                        client,
                        holding.id,
                        idempotency_key=push_id(),
                        destination_id=destination_id,
                        clock=clock,
                    )
                except ApplicationError as error:
                    return error.code
                return view.status.value

        assert sorted(await asyncio.gather(ask_once(), ask_once())) == ["conflict", "requested"]
        async with app_database.actor_transaction(client) as session:
            [asked] = await sales.client_sales(session, str(client.id))
        await price(app_database, new_staff(), asked.id, clock)
        await approve(app_database, new_staff(), asked.id, clock)

        settle_barrier = AsyncBarrier(2)

        async def settle_once() -> bool:
            async with worker_database.actor_transaction(SYSTEM) as session:
                await settle_barrier.wait()
                return await sales.settle(session, asked.id, past_safety_wait(clock)) is not None

        assert sorted(await asyncio.gather(settle_once(), settle_once())) == [False, True]
        async with worker_database.actor_transaction(SYSTEM) as session:
            count = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM ledger_journal_entries "
                        "WHERE entry_type = 'bond.sold' "
                        "AND source_id = :id"
                    ),
                    {"id": holding.id},
                )
            ).scalar_one()
        assert count == 1


async def test_an_ask_waiting_when_the_bond_matures_is_closed(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    day = maturity_day()
    client = await approved_client(app_database, clock)
    destination_id = await approved_destination(app_database, client, clock)
    async with live_issue(app_database, clock, **dates_for(day)) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        asked = await ask(app_database, client, holding.id, destination_id, clock)
        await price(app_database, new_staff(), asked.id, clock)
        async with worker_database.actor_transaction(SYSTEM) as session:
            await maturity.repay(session, holding.id, day, on(day))
            closed = await sales.get_sale(session, asked.id)
        assert closed.status is SaleStatus.CLOSED
        assert closed.closed_at is not None
        assert await status_of(worker_database, holding.id) is HoldingStatus.MATURED


async def test_the_database_holds_who_does_each_step(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    client = await approved_client(app_database, clock)
    destination_id = await approved_destination(app_database, client, clock)
    maker, checker = new_staff(), new_staff()
    async with live_issue(app_database, clock, **dates_for(maturity_day())) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        other = await active(app_database, worker_database, client, code, clock)
        insert = text(
            "INSERT INTO bond_sales (id, holding_id, client_id, currency, face_minor, cost_minor, "
            "destination_id, requested_at, status, updated_at) VALUES (:id, :holding, :client, "
            "'UGX', :face, :cost, :destination, now(), 'requested', now())"
        )
        values = {
            "id": push_id(),
            "holding": other.id,
            "client": client.id,
            "face": other.face.minor,
            "cost": other.cost.minor,
            "destination": destination_id,
        }
        # Staff do not ask in a client's name, and a client asks only for what they hold.
        with pytest.raises(DBAPIError, match=r"asked for by the client|row-level security"):
            async with app_database.actor_transaction(maker) as session:
                await session.execute(insert, values)
        with pytest.raises(DBAPIError, match="asked for by the client"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(insert, {**values, "face": 2_000_000})
        # The worker cannot mark a holding sold without paying its sale.
        with pytest.raises(DBAPIError, match="sold only once its sale has been paid"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text(
                        "UPDATE bond_holdings SET status = 'sold', updated_at = now() "
                        "WHERE id = :id"
                    ),
                    {"id": other.id},
                )

        asked = await ask(app_database, client, holding.id, destination_id, clock)
        set_price = text(
            "UPDATE bond_sales SET status = 'priced', price = 972500, sold_on = current_date, "
            "reason = 'Desk', priced_by = :by, priced_at = now(), updated_at = now() WHERE id = :id"
        )
        # The client cannot price their own sale, nor staff price it in another's name.
        with pytest.raises(DBAPIError, match="priced by the member of staff signed in"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(set_price, {"id": asked.id, "by": client.id})
        with pytest.raises(DBAPIError, match="priced by the member of staff signed in"):
            async with app_database.actor_transaction(maker) as session:
                await session.execute(set_price, {"id": asked.id, "by": checker.id})
        await price(app_database, maker, asked.id, clock)
        approve_sql = text(
            "UPDATE bond_sales SET status = 'approved', decided_by = :by, decided_at = now(), "
            "updated_at = now() WHERE id = :id"
        )
        # The one who priced it cannot approve it, even writing the row by hand.
        with pytest.raises(DBAPIError, match="ck_bond_sales_decided"):
            async with app_database.actor_transaction(maker) as session:
                await session.execute(approve_sql, {"id": asked.id, "by": maker.id})
        # Approving never changes the price.
        with pytest.raises(DBAPIError, match="approved as priced"):
            async with app_database.actor_transaction(checker) as session:
                await session.execute(
                    text(
                        "UPDATE bond_sales SET status = 'approved', price = 1000000, "
                        "decided_by = :by, decided_at = now(), updated_at = now() WHERE id = :id"
                    ),
                    {"id": asked.id, "by": checker.id},
                )
        # Staff never record what a sale paid.
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_database.actor_transaction(checker) as session:
                await session.execute(
                    text("UPDATE bond_sales SET amount_minor = 1 WHERE id = :id"), {"id": asked.id}
                )
        await approve(app_database, checker, asked.id, clock)
        async with worker_database.actor_transaction(SYSTEM) as session:
            sold = await sales.settle(session, asked.id, past_safety_wait(clock))
        assert sold is not None
        # A sale paid out is never sent on again, nor removed.
        with pytest.raises(DBAPIError, match="only sent on, once"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(
                    text(
                        "UPDATE bond_sales SET payout_status = 'paid_out', payout_id = :p, "
                        "payout_refusal = NULL, updated_at = now() WHERE id = :id"
                    ),
                    {"id": asked.id, "p": push_id()},
                )
        with pytest.raises(DBAPIError, match=r"permission denied|never removed"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text("DELETE FROM bond_sales WHERE id = :id"), {"id": asked.id}
                )
        # A sold holding never goes back.
        with pytest.raises(DBAPIError, match="only started or cancelled"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text(
                        "UPDATE bond_holdings SET status = 'active', updated_at = now() "
                        "WHERE id = :id"
                    ),
                    {"id": holding.id},
                )
