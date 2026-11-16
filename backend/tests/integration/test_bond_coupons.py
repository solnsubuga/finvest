"""The coupon run against real PostgreSQL: one coupon per holding and date, after tax, paid out
or held until the client says where it goes.

Each test makes an issue whose next coupon falls in a day or two, starts holdings in it today,
and runs the coupon with a clock on that day. Ledger entries may be dated ahead of today, as
the maturity tests' are.
"""

import asyncio
import secrets
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, WorkerConfig, enqueue
from app.modules.bonds import coupons, holdings
from app.modules.bonds.domain.coupons import HoldReason
from app.modules.bonds.jobs import PAY_COUPONS_KIND, BondJobs
from app.modules.tax import withholding
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.integration.test_bond_holdings import buy, live_issue, now, pay, ugx
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.investments import approved_client, approved_destination

pytestmark = pytest.mark.integration


def coupon_day() -> date:
    """A day soon that falls on the same day of every month, so it is a coupon date of an issue
    that matures on it ten years later. Two days ahead, so a payout number approved now is past
    its 24-hour safety wait by noon that day."""
    day = business_date(datetime.now(UTC)) + timedelta(days=2)
    while day.day > 28:
        day += timedelta(days=1)
    return day


def dates_for(day: date) -> dict[str, object]:
    return {
        "issue_date": day.replace(year=day.year - 1),
        "maturity_date": day.replace(year=day.year + 10),
    }


def on(day: date) -> FakeClock:
    """Noon in Kampala on `day`."""
    return FakeClock(datetime(day.year, day.month, day.day, 9, tzinfo=UTC))


async def active(
    app_database: Database, worker_database: Database, client: Actor, code: str, clock: Clock
) -> holdings.Holding:
    holding = await buy(app_database, client, code, clock)
    await pay(worker_database, holding, holding.cost, clock)
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await holdings.start_holding(
            session, holding.id, confirmed_on=business_date(clock.now()), clock=clock
        )


async def coupon_rows(worker: Database, holding_id: str) -> list[coupons.CouponView]:
    async with worker.actor_transaction(SYSTEM) as session:
        ids = (
            await session.execute(
                text("SELECT id FROM bond_coupons WHERE holding_id = :id ORDER BY id"),
                {"id": holding_id},
            )
        ).scalars()
        return [await coupons.get_coupon(session, coupon_id) for coupon_id in ids]


async def coupon_entries(worker: Database, holding_id: str) -> int:
    async with worker.actor_transaction(SYSTEM) as session:
        found = await session.execute(
            text(
                "SELECT count(*) FROM ledger_journal_entries WHERE entry_type = 'bond.coupon' "
                "AND source_type = 'bond_holding' AND source_id = :id"
            ),
            {"id": holding_id},
        )
        return int(found.scalar_one())


async def run_coupon_job(worker: Database, day: date) -> tuple[str, str | None]:
    """Queue the run the scheduler fires at 01:00 in Kampala on `day`, and run it on that day."""
    queue = f"coupons_{secrets.token_hex(8)}"
    fired = datetime(day.year, day.month, day.day, 1, tzinfo=UTC) - timedelta(hours=3)
    clock = on(day)
    async with worker.actor_transaction(SYSTEM) as session:
        job_id = await enqueue(
            session,
            JobRequest(
                kind=PAY_COUPONS_KIND,
                idempotency_key=f"{PAY_COUPONS_KIND}:{push_id()}",
                payload={"scheduled_for": fired.isoformat()},
                queue=queue,
            ),
            now=clock.now(),
        )
    runner = Worker(worker, BondJobs(worker, clock).handlers(), WorkerConfig(queue=queue), clock)
    assert await runner.run_once() == 1
    async with worker.actor_transaction(SYSTEM) as session:
        row = (
            await session.execute(
                text("SELECT status, last_error_code FROM jobs WHERE id = :id"), {"id": job_id}
            )
        ).one()
    return row.status, row.last_error_code


async def test_the_run_pays_each_holding_its_coupon_after_tax_once(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    day = coupon_day()
    client = await approved_client(app_database, clock)
    waiting = await approved_client(app_database, clock)
    destination_id = await approved_destination(app_database, client, clock)
    async with live_issue(app_database, clock, **dates_for(day)) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        unpaid = await buy(app_database, waiting, code, clock)
        async with app_database.actor_transaction(client) as session:
            chosen = await coupons.choose_destination(
                session, client, holding.id, destination_id=destination_id, clock=clock
            )
        assert chosen.payout_destination_id == destination_id

        # Not a coupon date: nothing to pay.
        async with worker_database.actor_transaction(SYSTEM) as session:
            next_day = day + timedelta(days=1)
            assert await coupons.pay_coupon(session, holding.id, next_day, clock) is None
        # Never before the date.
        with pytest.raises(ApplicationError) as early:
            async with worker_database.actor_transaction(SYSTEM) as session:
                await coupons.pay_batch(session, day, clock)
        assert early.value.code == "conflict"

        assert await run_coupon_job(worker_database, day) == ("succeeded", None)
        [paid] = await coupon_rows(worker_database, holding.id)
        # 16% a year on 1,000,000 is 80,000 a half year; 10% of it is withheld.
        assert (paid.coupon_date, paid.gross, paid.tax, paid.net) == (
            day,
            ugx(80_000),
            ugx(8_000),
            ugx(72_000),
        )
        assert (paid.status, paid.payout_refusal) == (coupons.CouponStatus.PAID_OUT, None)
        assert paid.payout_id is not None
        async with worker_database.actor_transaction(SYSTEM) as session:
            payout = (
                await session.execute(
                    text("SELECT amount_minor, client_id FROM payouts WHERE id = :id"),
                    {"id": paid.payout_id},
                )
            ).one()
        assert (payout.amount_minor, payout.client_id) == (72_000, client.id)
        assert await coupon_rows(worker_database, unpaid.id) == []

        # The run again, as a retry or a catch-up would, pays nothing more.
        assert await run_coupon_job(worker_database, day) == ("succeeded", None)
        assert len(await coupon_rows(worker_database, holding.id)) == 1
        assert await coupon_entries(worker_database, holding.id) == 1

        # The tax is in the figures for URA, as a line named after the bond.
        async with worker_database.actor_transaction(SYSTEM) as session:
            lines = await withholding.by_investment(
                session, Currency.UGX, date_from=day, date_to=day, investment_ids=[holding.id]
            )
            months = await withholding.withheld_by_month(
                session, Currency.UGX, date_from=day, date_to=day
            )
        assert [(t.investment_id, t.product_name, t.gross, t.tax) for t in lines] == [
            (holding.id, holding.name, ugx(80_000), ugx(8_000))
        ]
        assert months[day.replace(day=1)] >= ugx(8_000)

        # The client sees the coupon after tax.
        async with app_database.actor_transaction(client) as session:
            mine = await coupons.client_coupons(session, str(client.id))
        assert [(c.id, c.net) for c in mine] == [(paid.id, ugx(72_000))]
        async with app_database.actor_transaction(waiting) as session:
            assert await coupons.client_coupons(session, str(waiting.id)) == []


async def test_a_coupon_with_nowhere_to_go_waits_for_the_client(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    day = coupon_day()
    client = await approved_client(app_database, clock)
    stranger = await approved_client(app_database, clock)
    async with live_issue(app_database, clock, **dates_for(day)) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        async with worker_database.actor_transaction(SYSTEM) as session:
            assert (
                await coupons.pay_coupon(session, holding.id, day, on(day))
                == coupons.CouponStatus.HELD
            )
        [held] = await coupon_rows(worker_database, holding.id)
        assert (held.status, held.payout_id, held.payout_refusal) == (
            coupons.CouponStatus.HELD,
            None,
            HoldReason.NO_DESTINATION,
        )

        destination_id = await approved_destination(app_database, client, clock)
        later = on(day)
        with pytest.raises(ApplicationError) as theirs:
            async with app_database.actor_transaction(stranger) as session:
                await coupons.pay_out_held(
                    session, stranger, held.id, destination_id=destination_id, clock=later
                )
        assert theirs.value.code == "not_found"
        with pytest.raises(ApplicationError) as nowhere:
            async with app_database.actor_transaction(client) as session:
                await coupons.pay_out_held(
                    session, client, held.id, destination_id=push_id(), clock=later
                )
        assert [(p.field_path[0], p.code) for p in nowhere.value.fields] == [
            ("destination_id", "not_usable")
        ]

        async with app_database.actor_transaction(client) as session:
            sent = await coupons.pay_out_held(
                session, client, held.id, destination_id=destination_id, clock=later
            )
        assert (sent.status, sent.payout_refusal) == (coupons.CouponStatus.PAID_OUT, None)
        assert sent.payout_id is not None
        with pytest.raises(ApplicationError) as again:
            async with app_database.actor_transaction(client) as session:
                await coupons.pay_out_held(
                    session, client, held.id, destination_id=destination_id, clock=later
                )
        assert again.value.code == "conflict"
        assert await coupon_entries(worker_database, holding.id) == 1


async def test_two_runs_at_once_pay_a_holding_one_coupon(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    day = coupon_day()
    client = await approved_client(app_database, clock)
    async with live_issue(app_database, clock, **dates_for(day)) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        barrier = AsyncBarrier(2)

        async def run() -> str | None:
            async with worker_database.actor_transaction(SYSTEM) as session:
                await barrier.wait()
                return await coupons.pay_coupon(session, holding.id, day, on(day))

        outcomes = await asyncio.gather(run(), run())
        assert sorted(outcomes, key=str) == sorted([coupons.CouponStatus.HELD, None], key=str)
        assert len(await coupon_rows(worker_database, holding.id)) == 1
        assert await coupon_entries(worker_database, holding.id) == 1


async def test_the_database_lets_only_the_worker_pay_and_the_owner_send_on(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    day = coupon_day()
    client = await approved_client(app_database, clock)
    stranger = await approved_client(app_database, clock)
    async with live_issue(app_database, clock, **dates_for(day)) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        async with worker_database.actor_transaction(SYSTEM) as session:
            await coupons.pay_coupon(session, holding.id, day, on(day))
        [held] = await coupon_rows(worker_database, holding.id)

        # A client cannot write a coupon of their own.
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(
                    text(
                        "INSERT INTO bond_coupons (id, holding_id, client_id, coupon_date, "
                        "currency, gross_minor, tax_minor, net_minor, entry_id, status, "
                        "created_at, updated_at) VALUES (:id, :holding, :client, :day, 'UGX', "
                        "1, 0, 1, :entry, 'held', now(), now())"
                    ),
                    {
                        "id": push_id(),
                        "holding": holding.id,
                        "client": client.id,
                        "day": day + timedelta(days=182),
                        "entry": push_id(),
                    },
                )
        # Nor mark one paid out without a payout, nor touch another client's.
        with pytest.raises(DBAPIError, match="only sent on"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(
                    text(
                        "UPDATE bond_coupons SET status = 'held', updated_at = now() WHERE id = :id"
                    ),
                    {"id": held.id},
                )
        with pytest.raises(DBAPIError, match="payout of its own"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(
                    text(
                        "UPDATE bond_coupons SET status = 'paid_out', payout_id = :payout, "
                        "updated_at = now() WHERE id = :id"
                    ),
                    {"id": held.id, "payout": push_id()},
                )
        async with app_database.actor_transaction(stranger) as session:
            touched = await session.execute(
                text(
                    "UPDATE bond_coupons SET status = 'paid_out', payout_id = :payout "
                    "WHERE id = :id"
                ),
                {"id": held.id, "payout": push_id()},
            )
            assert touched.rowcount == 0  # type: ignore[attr-defined]
            moved = await session.execute(
                text(
                    "UPDATE bond_holdings SET payout_destination_id = :d, updated_at = now() "
                    "WHERE id = :id"
                ),
                {"id": holding.id, "d": push_id()},
            )
            assert moved.rowcount == 0  # type: ignore[attr-defined]
        # Even the worker never removes one.
        with pytest.raises(DBAPIError, match=r"permission denied|never removed"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text("DELETE FROM bond_coupons WHERE id = :id"), {"id": held.id}
                )


async def test_a_later_run_pays_a_coupon_an_earlier_run_missed(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    day = coupon_day()
    client = await approved_client(app_database, clock)
    async with live_issue(app_database, clock, **dates_for(day)) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        # The scheduler was down on the coupon date. Three days later is not a coupon date,
        # but that day's run pays the coupon all the same, once.
        later = day + timedelta(days=3)
        assert await run_coupon_job(worker_database, later) == ("succeeded", None)
        [paid] = await coupon_rows(worker_database, holding.id)
        assert (paid.coupon_date, paid.net, paid.status) == (
            day,
            ugx(72_000),
            coupons.CouponStatus.HELD,
        )
        assert await run_coupon_job(worker_database, later + timedelta(days=1)) == (
            "succeeded",
            None,
        )
        assert len(await coupon_rows(worker_database, holding.id)) == 1
        assert await coupon_entries(worker_database, holding.id) == 1
