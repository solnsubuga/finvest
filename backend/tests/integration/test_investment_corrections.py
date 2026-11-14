"""Undoing a mistaken investment and refunding money that never started one, against real
PostgreSQL: one person proposes, a different one approves, the worker moves the money, and
the database holds everyone to their part."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.investments import accrual, corrections
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import InvestmentStatus
from app.modules.investments.holdings import AWAITING_START, HOLDING
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec
from app.modules.payouts import payouts
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.investments import (
    PRESENT,
    account_balance,
    approved_destination,
    at,
    pay,
    started,
    today,
)
from tests.support.ledger import staff as new_staff
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

DAY = today()
# Past a new payout account's first day's safety wait.
LATER = at(DAY + timedelta(days=2))
Kind = corrections.CorrectionKind
Status = corrections.CorrectionStatus


async def propose(
    app_database: Database,
    staff: Actor,
    investment_id: str,
    kind: corrections.CorrectionKind,
    *,
    destination_id: str | None = None,
    clock: FakeClock = LATER,
) -> corrections.Correction:
    async with app_database.actor_transaction(staff) as session:
        return await investments.propose_correction(
            session,
            staff,
            kind=kind,
            investment_id=investment_id,
            reason="The deposit was credited to the wrong client",
            destination_id=destination_id,
            clock=clock,
        )


async def approve(
    app_database: Database, staff: Actor, correction_id: str
) -> corrections.Correction:
    async with app_database.actor_transaction(staff) as session:
        return await investments.approve_correction(session, staff, correction_id, LATER)


async def apply(worker_database: Database, correction_id: str) -> corrections.Correction:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await corrections.apply_correction(session, correction_id, LATER)


async def running_with_interest(
    app_database: Database, worker_database: Database
) -> investments.Investment:
    investment = await started(app_database, worker_database, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await accrual.accrue_investment(
            session, investment.id, DAY + timedelta(days=5), at(DAY + timedelta(days=5))
        )
        return await investments.get_investment(session, investment.id)


async def interest_owed(worker_database: Database, investment: investments.Investment) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(
            session, accrual.interest_account(investment), FakeClock()
        )
        return (await ledger.get_balance(session, account.id)).minor


async def queued_jobs(worker_database: Database, correction_id: str) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return int(
            (
                await session.execute(
                    text("SELECT count(*) FROM jobs WHERE kind = :kind AND idempotency_key = :id"),
                    {"kind": corrections.APPLY_KIND, "id": correction_id},
                )
            ).scalar_one()
        )


async def test_an_unwind_takes_two_people_and_reverses_principal_and_interest(
    app_database: Database, worker_database: Database
) -> None:
    investment = await running_with_interest(app_database, worker_database)
    assert investment.accrued_gross.is_positive
    maker, checker = new_staff(), new_staff()

    proposed = await propose(app_database, maker, investment.id, Kind.UNWIND)
    assert (proposed.status, proposed.amount) == (Status.PROPOSED, ugx(1_000_000))
    # The proposer can never approve their own.
    with pytest.raises(ApplicationError) as refused:
        await approve(app_database, maker, proposed.id)
    assert refused.value.code == "forbidden"
    # Nothing has moved yet, and nothing is queued.
    assert await queued_jobs(worker_database, proposed.id) == 0
    assert await account_balance(worker_database, investment, HOLDING) == 1_000_000

    approved = await approve(app_database, checker, proposed.id)
    assert (approved.status, approved.decided_by) == (Status.APPROVED, checker.id)
    assert await queued_jobs(worker_database, proposed.id) == 1

    done = await apply(worker_database, proposed.id)
    assert done.status is Status.DONE and done.entry_id is not None
    async with worker_database.actor_transaction(SYSTEM) as session:
        after = await investments.get_investment(session, investment.id)
    assert after.status is InvestmentStatus.UNWOUND
    assert await account_balance(worker_database, investment, HOLDING) == 0
    assert await account_balance(worker_database, investment, AWAITING_START) == 1_000_000
    assert await interest_owed(worker_database, investment) == 0
    # Run again, it changes nothing.
    assert (await apply(worker_database, proposed.id)).entry_id == done.entry_id
    # And the client no longer sees it.
    client = Actor("client", investment.client_id)
    async with app_database.actor_transaction(client) as session:
        assert await investments.client_investments(session, client, LATER) == []
        with pytest.raises(ApplicationError):
            await investments.client_investment(session, client, investment.id, LATER)


async def test_a_refund_after_an_unwind_pays_the_money_back_with_two_people(
    app_database: Database, worker_database: Database
) -> None:
    investment = await running_with_interest(app_database, worker_database)
    unwind = await propose(app_database, new_staff(), investment.id, Kind.UNWIND)
    await approve(app_database, new_staff(), unwind.id)
    await apply(worker_database, unwind.id)
    client = Actor("client", investment.client_id)
    destination_id = await approved_destination(app_database, client, PRESENT)

    # A refund names where the money goes. An unwind does not.
    with pytest.raises(ApplicationError) as refused:
        await propose(app_database, new_staff(), investment.id, Kind.REFUND)
    assert refused.value.code == "validation_failed"
    refund = await propose(
        app_database, new_staff(), investment.id, Kind.REFUND, destination_id=destination_id
    )
    assert refund.amount == ugx(1_000_000)
    await approve(app_database, new_staff(), refund.id)

    done = await apply(worker_database, refund.id)
    assert done.status is Status.DONE
    assert done.payout_id is not None and done.entry_id is not None
    assert await account_balance(worker_database, investment, AWAITING_START) == 0
    # The client sees the payout under their own row-level security.
    async with app_database.actor_transaction(client) as session:
        payout = await payouts.get_payout(session, done.payout_id)
    assert (payout.amount, payout.destination_id) == (ugx(1_000_000), destination_id)


async def test_a_refund_whose_amount_changed_fails_and_moves_nothing(
    app_database: Database, worker_database: Database
) -> None:
    investment = await running_with_interest(app_database, worker_database)
    unwind = await propose(app_database, new_staff(), investment.id, Kind.UNWIND)
    await approve(app_database, new_staff(), unwind.id)
    await apply(worker_database, unwind.id)
    client = Actor("client", investment.client_id)
    destination_id = await approved_destination(app_database, client, PRESENT)
    refund = await propose(
        app_database, new_staff(), investment.id, Kind.REFUND, destination_id=destination_id
    )
    await approve(app_database, new_staff(), refund.id)
    # More money arrives for it after the proposal: what was approved is no longer the amount.
    await pay(worker_database, investment, ugx(5_000), LATER)

    failed = await apply(worker_database, refund.id)
    assert (failed.status, failed.failure_code) == (Status.FAILED, "amount_changed")
    assert failed.entry_id is None and failed.payout_id is None
    assert await account_balance(worker_database, investment, AWAITING_START) == 1_005_000


async def test_an_unwind_refuses_an_investment_that_is_not_running_or_is_being_withdrawn(
    app_database: Database, worker_database: Database
) -> None:
    investment = await running_with_interest(app_database, worker_database)
    # Nothing is waiting to refund while it runs.
    client = Actor("client", investment.client_id)
    destination_id = await approved_destination(app_database, client, PRESENT)
    with pytest.raises(ApplicationError) as refused:
        await propose(
            app_database, new_staff(), investment.id, Kind.REFUND, destination_id=destination_id
        )
    assert refused.value.code == "conflict"
    # One open correction of a kind at a time.
    await propose(app_database, new_staff(), investment.id, Kind.UNWIND)
    with pytest.raises(ApplicationError) as twice:
        await propose(app_database, new_staff(), investment.id, Kind.UNWIND)
    assert twice.value.code == "conflict"


async def test_sending_back_and_withdrawing_move_nothing(
    app_database: Database, worker_database: Database
) -> None:
    investment = await running_with_interest(app_database, worker_database)
    maker, checker = new_staff(), new_staff()
    first = await propose(app_database, maker, investment.id, Kind.UNWIND)
    async with app_database.actor_transaction(maker) as session:
        with pytest.raises(ApplicationError) as own:
            await investments.send_back_correction(session, maker, first.id, "No", LATER)
    assert own.value.code == "forbidden"
    async with app_database.actor_transaction(checker) as session:
        sent_back = await investments.send_back_correction(
            session, checker, first.id, "Check the bank statement first", LATER
        )
    assert sent_back.status is Status.SENT_BACK

    second = await propose(app_database, maker, investment.id, Kind.UNWIND)
    async with app_database.actor_transaction(checker) as session:
        with pytest.raises(ApplicationError) as not_theirs:
            await investments.withdraw_correction(session, checker, second.id, LATER)
    assert not_theirs.value.code == "forbidden"
    async with app_database.actor_transaction(maker) as session:
        withdrawn = await investments.withdraw_correction(session, maker, second.id, LATER)
    assert withdrawn.status is Status.WITHDRAWN
    # A decided one is never decided again.
    with pytest.raises(ApplicationError) as decided:
        await approve(app_database, checker, second.id)
    assert decided.value.code == "conflict"
    assert await queued_jobs(worker_database, first.id) == 0
    assert await account_balance(worker_database, investment, HOLDING) == 1_000_000


async def test_two_approvals_at_once_approve_once(
    app_database: Database, worker_database: Database
) -> None:
    investment = await running_with_interest(app_database, worker_database)
    proposed = await propose(app_database, new_staff(), investment.id, Kind.UNWIND)
    barrier = AsyncBarrier(2)

    async def one() -> str:
        staff = new_staff()
        async with app_database.actor_transaction(staff) as session:
            await barrier.wait()
            try:
                await investments.approve_correction(session, staff, proposed.id, LATER)
            except ApplicationError as error:
                return error.code
            return "approved"

    assert sorted(await asyncio.gather(one(), one())) == ["approved", "conflict"]
    assert await queued_jobs(worker_database, proposed.id) == 1


async def test_the_database_holds_everyone_to_their_part(
    app_database: Database, worker_database: Database
) -> None:
    investment = await running_with_interest(app_database, worker_database)
    maker = new_staff()
    proposed = await propose(app_database, maker, investment.id, Kind.UNWIND)

    # The proposer cannot approve it, whatever the code says.
    with pytest.raises(DBAPIError, match="second_person"):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE investment_corrections SET status = 'approved', decided_by = :me, "
                    "decided_at = now() WHERE id = :id"
                ),
                {"me": maker.id, "id": proposed.id},
            )
    # Staff never record the outcome; only the worker does, once approved. The columns are
    # not theirs to write, and the status alone is only ever a decision.
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(
                text("UPDATE investment_corrections SET done_at = now() WHERE id = :id"),
                {"id": proposed.id},
            )
    with pytest.raises(DBAPIError, match="only decided"):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(
                text("UPDATE investment_corrections SET status = 'done' WHERE id = :id"),
                {"id": proposed.id},
            )
    # And the worker cannot undo an investment without an approved correction.
    with pytest.raises(DBAPIError, match="cannot go from active to unwound"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "UPDATE investments SET status = 'unwound', unwound_at = now(), "
                    "unwind_entry_id = :entry WHERE id = :id"
                ),
                {"entry": proposed.id, "id": investment.id},
            )


async def test_a_client_reads_no_correction(
    app_database: Database, worker_database: Database
) -> None:
    investment = await running_with_interest(app_database, worker_database)
    await propose(app_database, new_staff(), investment.id, Kind.UNWIND)
    # Even the client the correction is about, asking the table directly.
    client = Actor("client", investment.client_id)
    async with app_database.actor_transaction(client) as session:
        seen = (
            await session.execute(text("SELECT count(*) FROM investment_corrections"))
        ).scalar_one()
    assert seen == 0


async def test_a_carried_over_investment_is_never_unwound(
    app_database: Database, worker_database: Database
) -> None:
    async with worker_database.actor_transaction(SYSTEM) as session:
        carried = await investments.open_existing_investment(
            session,
            SYSTEM,
            client_id=push_id(),
            product_code="TREA-91",
            principal=ugx(1_000_000),
            start_date=DAY - timedelta(days=30),
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
    with pytest.raises(ApplicationError) as refused:
        await propose(app_database, new_staff(), carried.id, Kind.UNWIND)
    assert refused.value.code == "validation_failed"
    assert refused.value.fields[0].code == "carried_over"
