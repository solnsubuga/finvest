"""Bond maturity against real PostgreSQL: each holding repaid its face value and last coupon
once, paid out or held until the client says where it goes.

Each test makes an issue that matures seven months from now, so it can still be bought today,
starts holdings in it today, and runs the repayment with a clock on the maturity date. Ledger
entries are dated ahead of today, as the coupon tests' are.
"""

import asyncio
import secrets
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, WorkerConfig, enqueue
from app.modules.bonds import coupons, holdings, maturity
from app.modules.bonds.domain.coupons import HoldReason
from app.modules.bonds.domain.holdings import HoldingStatus
from app.modules.bonds.jobs import REPAY_MATURED_KIND, BondJobs
from app.modules.ledger import service as ledger
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


def maturity_day() -> date:
    """Seven months from now, on a day every month has, so the issue's last coupon period has
    not begun and it can be bought today. Its one coupon before maturity is a month away."""
    day = business_date(datetime.now(UTC)) + timedelta(days=2)
    while day.day > 28:
        day += timedelta(days=1)
    month = day.month + 7
    return day.replace(year=day.year + (month - 1) // 12, month=(month - 1) % 12 + 1)


def dates_for(day: date) -> dict[str, object]:
    return {"issue_date": day.replace(year=day.year - 2), "maturity_date": day}


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


async def repayments(worker: Database, holding_id: str) -> list[maturity.RedemptionView]:
    async with worker.actor_transaction(SYSTEM) as session:
        ids = (
            await session.execute(
                text("SELECT id FROM bond_redemptions WHERE holding_id = :id"), {"id": holding_id}
            )
        ).scalars()
        return [await maturity.get_redemption(session, found) for found in ids]


async def entries(worker: Database, holding_id: str, entry_type: str) -> int:
    async with worker.actor_transaction(SYSTEM) as session:
        found = await session.execute(
            text(
                "SELECT count(*) FROM ledger_journal_entries WHERE entry_type = :type "
                "AND source_type = 'bond_holding' AND source_id = :id"
            ),
            {"type": entry_type, "id": holding_id},
        )
        return int(found.scalar_one())


async def status_of(worker: Database, holding_id: str) -> HoldingStatus:
    async with worker.actor_transaction(SYSTEM) as session:
        return (await holdings.get_holding(session, holding_id)).status


async def run_repay_job(worker: Database, day: date) -> tuple[str, str | None]:
    """Queue the run the scheduler fires at 01:15 in Kampala on `day`, and run it on that day."""
    queue = f"maturity_{secrets.token_hex(8)}"
    fired = datetime(day.year, day.month, day.day, 1, 15, tzinfo=UTC) - timedelta(hours=3)
    clock = on(day)
    async with worker.actor_transaction(SYSTEM) as session:
        job_id = await enqueue(
            session,
            JobRequest(
                kind=REPAY_MATURED_KIND,
                idempotency_key=f"{REPAY_MATURED_KIND}:{push_id()}",
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


async def test_a_matured_holding_is_repaid_its_face_and_last_coupon_once(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    day = maturity_day()
    client = await approved_client(app_database, clock)
    waiting = await approved_client(app_database, clock)
    destination_id = await approved_destination(app_database, client, clock)
    async with live_issue(app_database, clock, **dates_for(day)) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        unpaid = await buy(app_database, waiting, code, clock)
        async with app_database.actor_transaction(client) as session:
            await coupons.choose_destination(
                session, client, holding.id, destination_id=destination_id, clock=clock
            )

        # The day before maturity, nothing is repaid.
        before = day - timedelta(days=1)
        async with worker_database.actor_transaction(SYSTEM) as session:
            assert await maturity.repay(session, holding.id, before, on(before)) is None
        # And never ahead of the clock.
        with pytest.raises(ApplicationError) as early:
            async with worker_database.actor_transaction(SYSTEM) as session:
                await maturity.repay_batch(session, day, clock)
        assert early.value.code == "conflict"

        assert await run_repay_job(worker_database, day) == ("succeeded", None)
        # The coupon a month after buying was never paid by a coupon run, so it is paid first.
        async with worker_database.actor_transaction(SYSTEM) as session:
            missed = (
                await session.execute(
                    text("SELECT coupon_date, net_minor FROM bond_coupons WHERE holding_id = :id"),
                    {"id": holding.id},
                )
            ).all()
        assert [(row.coupon_date < day, row.net_minor) for row in missed] == [(True, 72_000)]
        [repaid] = await repayments(worker_database, holding.id)
        # The face value and the last coupon after tax: 80,000 less 10%.
        assert (repaid.redeemed_on, repaid.face, repaid.coupon_gross, repaid.coupon_tax) == (
            day,
            ugx(1_000_000),
            ugx(80_000),
            ugx(8_000),
        )
        assert (repaid.amount, repaid.status, repaid.payout_refusal) == (
            ugx(1_072_000),
            maturity.RedemptionStatus.PAID_OUT,
            None,
        )
        async with worker_database.actor_transaction(SYSTEM) as session:
            payout = (
                await session.execute(
                    text("SELECT amount_minor, client_id, purpose FROM payouts WHERE id = :id"),
                    {"id": repaid.payout_id},
                )
            ).one()
        assert tuple(payout) == (1_072_000, client.id, "bond_repayment")
        assert await status_of(worker_database, holding.id) is HoldingStatus.MATURED
        assert await status_of(worker_database, unpaid.id) is HoldingStatus.UNPAID

        # The bond holding is empty, and the price difference is the face value less the cost.
        async with worker_database.actor_transaction(SYSTEM) as session:
            held = await ledger.open_account(session, holdings.holding_account(holding), clock)
            assert (await ledger.get_balance(session, held.id)).is_zero
            entry_lines = (
                await session.execute(
                    text(
                        "SELECT a.code, l.amount_minor FROM ledger_lines l "
                        "JOIN ledger_accounts a ON a.id = l.account_id "
                        "JOIN ledger_journal_entries e ON e.id = l.entry_id "
                        "WHERE e.entry_type = 'bond.matured' AND e.source_id = :id"
                    ),
                    {"id": holding.id},
                )
            ).all()
        signed = {row.code: row.amount_minor for row in entry_lines}
        clean = holding.cost - holding.accrued
        # Bought at 98.5: Finvest makes the face value up from the clean amount, and takes in
        # the accrued interest the client paid, which the first coupon gave back.
        assert signed[maturity.BELOW_PAR] == 1_000_000 - clean.minor == 15_000
        assert signed[maturity.ACCRUED_BOUGHT] == -holding.accrued.minor
        assert maturity.ABOVE_PAR not in signed
        assert sum(signed.values()) == 0

        # Running again, as a retry or a catch-up would, repays nothing more.
        assert await run_repay_job(worker_database, day + timedelta(days=1)) == ("succeeded", None)
        assert len(await repayments(worker_database, holding.id)) == 1
        assert await entries(worker_database, holding.id, "bond.matured") == 1
        assert await entries(worker_database, holding.id, "bond.coupon") == 1

        # Only the two coupons are interest in the figures for URA, not the price difference.
        async with worker_database.actor_transaction(SYSTEM) as session:
            lines = await withholding.by_investment(
                session,
                Currency.UGX,
                date_from=business_date(clock.now()),
                date_to=day,
                investment_ids=[holding.id],
            )
        assert [(t.gross, t.tax) for t in lines] == [(ugx(160_000), ugx(16_000))]

        # The client sees the repayment after tax, and no coupons are left to come.
        async with app_database.actor_transaction(client) as session:
            mine = await maturity.client_redemptions(session, str(client.id))
            view = await holdings.client_holding(session, str(client.id), holding.id, on(day))
        assert [(r.id, r.amount) for r in mine] == [(repaid.id, ugx(1_072_000))]
        assert (view.status, view.coupons_left, view.still_to_come) == (
            HoldingStatus.MATURED,
            0,
            ugx(0),
        )
        async with app_database.actor_transaction(waiting) as session:
            assert await maturity.client_redemptions(session, str(waiting.id)) == []
        # Coupons no longer have anywhere to be chosen for.
        with pytest.raises(ApplicationError) as closed:
            async with app_database.actor_transaction(client) as session:
                await coupons.choose_destination(
                    session, client, holding.id, destination_id=None, clock=clock
                )
        assert closed.value.code == "conflict"


async def test_a_repayment_with_nowhere_to_go_waits_for_the_client(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    day = maturity_day()
    client = await approved_client(app_database, clock)
    stranger = await approved_client(app_database, clock)
    async with live_issue(app_database, clock, **dates_for(day)) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        async with worker_database.actor_transaction(SYSTEM) as session:
            assert (
                await maturity.repay(session, holding.id, day, on(day))
                == maturity.RedemptionStatus.HELD
            )
        [held] = await repayments(worker_database, holding.id)
        assert (held.status, held.payout_id, held.payout_refusal) == (
            maturity.RedemptionStatus.HELD,
            None,
            HoldReason.NO_DESTINATION,
        )

        destination_id = await approved_destination(app_database, client, clock)
        later = on(day)
        with pytest.raises(ApplicationError) as theirs:
            async with app_database.actor_transaction(stranger) as session:
                await maturity.pay_out_held(
                    session, stranger, held.id, destination_id=destination_id, clock=later
                )
        assert theirs.value.code == "not_found"
        with pytest.raises(ApplicationError) as nowhere:
            async with app_database.actor_transaction(client) as session:
                await maturity.pay_out_held(
                    session, client, held.id, destination_id=push_id(), clock=later
                )
        assert [(p.field_path[0], p.code) for p in nowhere.value.fields] == [
            ("destination_id", "not_usable")
        ]

        async with app_database.actor_transaction(client) as session:
            sent = await maturity.pay_out_held(
                session, client, held.id, destination_id=destination_id, clock=later
            )
        assert (sent.status, sent.payout_refusal) == (maturity.RedemptionStatus.PAID_OUT, None)
        async with worker_database.actor_transaction(SYSTEM) as session:
            amount = (
                await session.execute(
                    text("SELECT amount_minor, purpose FROM payouts WHERE id = :id"),
                    {"id": sent.payout_id},
                )
            ).one()
        assert tuple(amount) == (1_072_000, "bond_repayment")
        with pytest.raises(ApplicationError) as again:
            async with app_database.actor_transaction(client) as session:
                await maturity.pay_out_held(
                    session, client, held.id, destination_id=destination_id, clock=later
                )
        assert again.value.code == "conflict"
        assert await entries(worker_database, holding.id, "bond.matured") == 1


async def test_two_runs_at_once_repay_a_holding_once(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    day = maturity_day()
    client = await approved_client(app_database, clock)
    async with live_issue(app_database, clock, **dates_for(day)) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        barrier = AsyncBarrier(2)

        async def run() -> str | None:
            async with worker_database.actor_transaction(SYSTEM) as session:
                await barrier.wait()
                return await maturity.repay(session, holding.id, day, on(day))

        outcomes = await asyncio.gather(run(), run())
        assert sorted(outcomes, key=str) == sorted([maturity.RedemptionStatus.HELD, None], key=str)
        assert len(await repayments(worker_database, holding.id)) == 1
        assert await entries(worker_database, holding.id, "bond.matured") == 1
        assert await entries(worker_database, holding.id, "bond.coupon") == 1


async def test_the_database_lets_only_the_worker_repay_and_the_owner_send_on(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    day = maturity_day()
    client = await approved_client(app_database, clock)
    async with live_issue(app_database, clock, **dates_for(day)) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        other = await active(app_database, worker_database, client, code, clock)

        # The worker cannot mark a holding matured without repaying it.
        with pytest.raises(DBAPIError, match="matures only once it has been repaid"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text(
                        "UPDATE bond_holdings SET status = 'matured', updated_at = now() "
                        "WHERE id = :id"
                    ),
                    {"id": other.id},
                )
        # Nor repay a bond before it matures, nor repay anything but what was bought.
        values = {
            "id": push_id(),
            "holding": other.id,
            "client": client.id,
            "face": other.face.minor,
            "cost": other.cost.minor,
            "entry": push_id(),
        }
        insert = text(
            "INSERT INTO bond_redemptions (id, holding_id, client_id, redeemed_on, currency, "
            "face_minor, cost_minor, coupon_gross_minor, coupon_tax_minor, coupon_net_minor, "
            "amount_minor, entry_id, status, payout_refusal, created_at, updated_at) VALUES "
            "(:id, :holding, :client, :day, 'UGX', CAST(:face AS bigint), :cost, 80000, 8000, "
            "72000, CAST(:face AS bigint) + 72000, :entry, 'held', 'no_destination', now(), now())"
        )
        with pytest.raises(DBAPIError, match="once it has matured"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(insert, {**values, "day": day - timedelta(days=1)})
        with pytest.raises(DBAPIError, match="once it has matured"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(insert, {**values, "day": day, "face": 2_000_000})
        # A client cannot write one of their own.
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(insert, {**values, "day": day})

        async with worker_database.actor_transaction(SYSTEM) as session:
            await maturity.repay(session, holding.id, day, on(day))
        [held] = await repayments(worker_database, holding.id)
        # A matured holding is never repaid or started again, and its coupons go nowhere new.
        with pytest.raises(DBAPIError, match="once it has matured"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    insert, {**values, "id": push_id(), "holding": holding.id, "day": day}
                )
        with pytest.raises(DBAPIError, match="only started or cancelled"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text(
                        "UPDATE bond_holdings SET status = 'active', updated_at = now() "
                        "WHERE id = :id"
                    ),
                    {"id": holding.id},
                )
        with pytest.raises(DBAPIError, match="only chooses where their coupons go"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(
                    text(
                        "UPDATE bond_holdings SET payout_destination_id = :d, updated_at = now() "
                        "WHERE id = :id"
                    ),
                    {"id": holding.id, "d": push_id()},
                )
        with pytest.raises(DBAPIError, match="payout of its own"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(
                    text(
                        "UPDATE bond_redemptions SET status = 'paid_out', payout_id = :payout, "
                        "payout_refusal = NULL, updated_at = now() WHERE id = :id"
                    ),
                    {"id": held.id, "payout": push_id()},
                )
        with pytest.raises(DBAPIError, match=r"permission denied|never removed"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text("DELETE FROM bond_redemptions WHERE id = :id"), {"id": held.id}
                )


async def test_a_bond_bought_above_par_gives_the_difference_back(
    app_database: Database, worker_database: Database
) -> None:
    clock = now()
    day = maturity_day()
    client = await approved_client(app_database, clock)
    async with live_issue(app_database, clock, price=1_020_000, **dates_for(day)) as code:
        holding = await active(app_database, worker_database, client, code, clock)
        async with worker_database.actor_transaction(SYSTEM) as session:
            await maturity.repay(session, holding.id, day, on(day))
            rows = (
                await session.execute(
                    text(
                        "SELECT a.code, l.amount_minor FROM ledger_lines l "
                        "JOIN ledger_accounts a ON a.id = l.account_id "
                        "JOIN ledger_journal_entries e ON e.id = l.entry_id "
                        "WHERE e.entry_type = 'bond.matured' AND e.source_id = :id"
                    ),
                    {"id": holding.id},
                )
            ).all()
        signed = {row.code: row.amount_minor for row in rows}
        # Bought at 102: the client paid 20,000 more than the face value they get back.
        assert signed[maturity.ABOVE_PAR] == -20_000
        assert maturity.BELOW_PAR not in signed
        [repaid] = await repayments(worker_database, holding.id)
        assert repaid.amount == ugx(1_072_000)
