"""Taking back a deposit credited by mistake, against real PostgreSQL, the ledger and the job
queue. One person proposes, a different one approves, the worker reverses the deposit's entry
under the investment's lock, and the database holds everyone to their part."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.investments import corrections, holdings
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import CANCEL_GRACE, InvestmentStatus
from app.modules.investments.holdings import AWAITING_START
from app.modules.ledger import service as ledger
from app.modules.payments import reversals, service
from app.modules.payments.domain.deposits import DepositStatus, Purpose
from app.modules.payments.domain.outcomes import Accepted, Confirmed
from app.modules.payments.domain.reversals import ReversalStatus
from app.shared.actors import SYSTEM, Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.books import audit_for
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.investments import PRESENT, account_balance, approved_client, request, today
from tests.support.ledger import staff
from tests.support.payments import (
    FakeCollections,
    deposit_worker,
    drain,
    phone,
    start_deposit,
    ugx,
)

pytestmark = pytest.mark.integration

Status = ReversalStatus
MINOR = 1_000_000


@pytest.fixture
async def pools(test_database: TestDatabase) -> AsyncIterator[list[Database]]:
    """Two more worker pools, for races between independent transactions."""
    made = [Database(test_database.settings("finvest_worker")) for _ in range(2)]
    yield made
    for database in made:
        await database.close()


async def paid(
    app_database: Database, worker_database: Database, clock: Clock = PRESENT
) -> tuple[investments.Investment, service.Deposit]:
    """An investment waiting to start, with a mobile money deposit the provider confirmed."""
    client = await approved_client(app_database, clock)
    investment = await request(app_database, client, clock, amount=ugx(MINOR))
    deposit = await pay_by_phone(app_database, worker_database, client, investment, clock)
    return investment, deposit


async def pay_by_phone(
    app_database: Database,
    worker_database: Database,
    client: Actor,
    investment: investments.Investment,
    clock: Clock,
) -> service.Deposit:
    provider, msisdn, ref = FakeCollections(), phone(), f"ref-{push_id()}"
    provider.on_send(msisdn, Accepted(ref))
    deposit = await start_deposit(
        app_database,
        clock,
        actor=client,
        msisdn=msisdn,
        amount=investment.amount.minor,
        purpose_id=investment.id,
    )
    await drain(deposit_worker(worker_database, provider, clock))
    async with worker_database.actor_transaction(SYSTEM) as session:
        confirmed = await service.settle_deposit(
            session, deposit.id, Confirmed(ref, investment.amount), clock, cause="status_read"
        )
    assert confirmed.status is DepositStatus.CONFIRMED
    return confirmed


async def propose(app_database: Database, maker: Actor, deposit_id: str) -> reversals.Reversal:
    async with app_database.actor_transaction(maker) as session:
        return await reversals.propose_reversal(
            session,
            maker,
            deposit_id=deposit_id,
            reason="The provider confirmed a payment that never reached our wallet",
            idempotency_key=f"propose-{push_id()}",
            clock=PRESENT,
        )


async def approve(app_database: Database, checker: Actor, reversal_id: str) -> reversals.Reversal:
    async with app_database.actor_transaction(checker) as session:
        return await reversals.approve_reversal(
            session, checker, reversal_id, idempotency_key=f"approve-{push_id()}", clock=PRESENT
        )


async def apply(database: Database, reversal_id: str) -> reversals.Reversal:
    async with database.actor_transaction(SYSTEM) as session:
        return await reversals.apply_reversal(session, reversal_id, PRESENT)


async def reload(database: Database, deposit_id: str) -> service.Deposit:
    async with database.actor_transaction(SYSTEM) as session:
        return await service.get_deposit(session, deposit_id)


async def live(database: Database, investment_id: str) -> bool:
    async with database.actor_transaction(SYSTEM) as session:
        return await service.has_live_deposit(
            session, purpose=Purpose.INVESTMENT, purpose_id=investment_id
        )


async def scalar(database: Database, sql: str, **parameters: object) -> object:
    async with database.actor_transaction(SYSTEM) as session:
        return await session.scalar(text(sql), parameters)


async def start(database: Database, investment_id: str) -> investments.Investment:
    async with database.actor_transaction(SYSTEM) as session:
        return await investments.start_investment(
            session, investment_id, confirmed_on=today(), clock=PRESENT
        )


def code(error: pytest.ExceptionInfo[ApplicationError]) -> str:
    assert error.value.code == "validation_failed", error.value.code
    return error.value.fields[0].code


async def test_two_people_take_back_a_mistaken_deposit_and_the_client_can_pay_again(
    app_database: Database, worker_database: Database, migrator_database: Database
) -> None:
    investment, deposit = await paid(app_database, worker_database)
    assert await account_balance(worker_database, investment, AWAITING_START) == MINOR
    maker, checker = staff(), staff()

    proposed = await propose(app_database, maker, deposit.id)
    assert (proposed.status, proposed.amount, proposed.investment_id) == (
        Status.PROPOSED,
        ugx(MINOR),
        investment.id,
    )
    with pytest.raises(ApplicationError) as refused:
        await approve(app_database, maker, proposed.id)
    assert refused.value.code == "forbidden"
    # A second proposal for the same deposit is refused while the first is open.
    with pytest.raises(ApplicationError) as twice:
        await propose(app_database, staff(), deposit.id)
    assert twice.value.code == "conflict"

    approved = await approve(app_database, checker, proposed.id)
    assert (approved.status, approved.decided_by) == (Status.APPROVED, checker.id)
    queued = await scalar(
        worker_database,
        "SELECT count(*) FROM jobs WHERE kind = :kind AND idempotency_key = :id",
        kind=reversals.APPLY_KIND,
        id=proposed.id,
    )
    assert queued == 1
    # Nothing moves until the worker runs.
    assert await account_balance(worker_database, investment, AWAITING_START) == MINOR

    done = await apply(worker_database, proposed.id)
    assert done.status is Status.DONE and done.entry_id is not None
    assert await account_balance(worker_database, investment, AWAITING_START) == 0
    async with worker_database.actor_transaction(SYSTEM) as session:
        entry = await ledger.get_entry(session, done.entry_id)
        original = await ledger.get_entry(session, deposit.ledger_entry_id or "")
    assert entry.reverses_entry_id == original.id
    assert entry.entry_type == reversals.ENTRY_TYPE
    assert sorted((line.account_id, -line.amount.minor) for line in entry.lines) == sorted(
        (line.account_id, line.amount.minor) for line in original.lines
    )
    reversed_deposit = await reload(worker_database, deposit.id)
    # Still confirmed, because it was, but no longer the investment's payment.
    assert (reversed_deposit.status, reversed_deposit.reversal_id) == (
        DepositStatus.CONFIRMED,
        proposed.id,
    )
    assert not await live(worker_database, investment.id)
    # Running it again changes nothing.
    assert (await apply(worker_database, proposed.id)).entry_id == done.entry_id
    actions = [event.action for event in await audit_for(migrator_database, [proposed.id])]
    assert actions == [
        "payments.reversal_proposed",
        "payments.reversal_approved",
        "payments.reversal_done",
    ]
    assert "payments.deposit_reversed" in [
        event.action for event in await audit_for(migrator_database, [deposit.id])
    ]
    # The client can pay for the same investment again, and it starts as usual.
    client = Actor("client", investment.client_id)
    again = await pay_by_phone(app_database, worker_database, client, investment, PRESENT)
    assert await live(worker_database, investment.id)
    assert (await start(worker_database, investment.id)).status is InvestmentStatus.ACTIVE
    assert again.reversal_id is None


async def test_an_unpaid_investment_is_cancelled_at_its_pay_by_time_once_reversed(
    app_database: Database, worker_database: Database
) -> None:
    investment, deposit = await paid(app_database, worker_database)
    proposed = await propose(app_database, staff(), deposit.id)
    await approve(app_database, staff(), proposed.id)
    await apply(worker_database, proposed.id)

    late = FakeClock(investment.pay_by + CANCEL_GRACE + timedelta(seconds=1))
    async with worker_database.actor_transaction(SYSTEM) as session:
        await holdings.cancel_unpaid(session, late, limit=10_000)
        after = await investments.get_investment(session, investment.id)
    assert after.status is InvestmentStatus.CANCELLED


async def test_a_started_investment_is_unwound_first_then_the_deposit_is_reversed(
    app_database: Database, worker_database: Database
) -> None:
    investment, deposit = await paid(app_database, worker_database)
    await start(worker_database, investment.id)
    with pytest.raises(ApplicationError) as refused:
        await propose(app_database, staff(), deposit.id)
    assert code(refused) == "money_moved_on"

    # Investments puts the principal back where the deposit left it.
    maker, checker = staff(), staff()
    async with app_database.actor_transaction(maker) as session:
        unwind = await investments.propose_correction(
            session,
            maker,
            kind=corrections.CorrectionKind.UNWIND,
            investment_id=investment.id,
            reason="Started by a payment that never arrived",
            clock=PRESENT,
        )
    async with app_database.actor_transaction(checker) as session:
        await investments.approve_correction(session, checker, unwind.id, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await corrections.apply_correction(session, unwind.id, PRESENT)

    proposed = await propose(app_database, staff(), deposit.id)
    await approve(app_database, staff(), proposed.id)
    assert (await apply(worker_database, proposed.id)).status is Status.DONE
    assert await account_balance(worker_database, investment, AWAITING_START) == 0
    async with worker_database.actor_transaction(SYSTEM) as session:
        after = await investments.get_investment(session, investment.id)
    assert after.status is InvestmentStatus.UNWOUND


async def test_money_that_moved_after_approval_fails_the_reversal_and_moves_nothing(
    app_database: Database, worker_database: Database
) -> None:
    investment, deposit = await paid(app_database, worker_database)
    proposed = await propose(app_database, staff(), deposit.id)
    await approve(app_database, staff(), proposed.id)
    await start(worker_database, investment.id)

    failed = await apply(worker_database, proposed.id)
    assert (failed.status, failed.failure_code, failed.entry_id) == (
        Status.FAILED,
        "money_moved_on",
        None,
    )
    assert (await reload(worker_database, deposit.id)).reversal_id is None
    assert await live(worker_database, investment.id)
    # A failed reversal frees the deposit for a fresh proposal once the money is back.
    with pytest.raises(ApplicationError) as refused:
        await propose(app_database, staff(), deposit.id)
    assert code(refused) == "money_moved_on"


async def test_a_reversal_racing_the_start_moves_the_money_once(
    app_database: Database, worker_database: Database, pools: list[Database]
) -> None:
    investment, deposit = await paid(app_database, worker_database)
    proposed = await propose(app_database, staff(), deposit.id)
    await approve(app_database, staff(), proposed.id)

    async def starting() -> str:
        try:
            return (await start(pools[0], investment.id)).status.value
        except ApplicationError as error:
            return error.code

    async def reversing() -> str:
        finished = await apply(pools[1], proposed.id)
        return finished.failure_code or finished.status.value

    started, reversed_ = await asyncio.gather(starting(), reversing())
    assert (started, reversed_) in {("active", "money_moved_on"), ("conflict", "done")}
    holding = await account_balance(worker_database, investment, holdings.HOLDING)
    waiting = await account_balance(worker_database, investment, AWAITING_START)
    assert (holding, waiting) == ((MINOR, 0) if started == "active" else (0, 0))


async def test_only_the_proposer_withdraws_and_only_someone_else_sends_back(
    app_database: Database, worker_database: Database
) -> None:
    _, deposit = await paid(app_database, worker_database)
    maker, other = staff(), staff()
    proposed = await propose(app_database, maker, deposit.id)

    async with app_database.actor_transaction(other) as session:
        with pytest.raises(ApplicationError) as not_yours:
            await reversals.withdraw_reversal(
                session, other, proposed.id, idempotency_key=f"w-{push_id()}", clock=PRESENT
            )
    assert not_yours.value.code == "forbidden"
    async with app_database.actor_transaction(maker) as session:
        with pytest.raises(ApplicationError) as own:
            await reversals.send_back_reversal(
                session, maker, proposed.id, "No", idempotency_key=f"s-{push_id()}", clock=PRESENT
            )
    assert own.value.code == "forbidden"
    async with app_database.actor_transaction(other) as session:
        sent_back = await reversals.send_back_reversal(
            session,
            other,
            proposed.id,
            "The wallet statement shows it arrived",
            idempotency_key=f"s-{push_id()}",
            clock=PRESENT,
        )
    assert (sent_back.status, sent_back.decision_note) == (
        Status.SENT_BACK,
        "The wallet statement shows it arrived",
    )
    # A sent back proposal frees the deposit for a new one, which its proposer can withdraw.
    again = await propose(app_database, maker, deposit.id)
    async with app_database.actor_transaction(maker) as session:
        withdrawn = await reversals.withdraw_reversal(
            session, maker, again.id, idempotency_key=f"w-{push_id()}", clock=PRESENT
        )
    assert withdrawn.status is Status.WITHDRAWN
    assert (await reload(worker_database, deposit.id)).reversal_id is None


async def test_a_deposit_that_is_not_a_confirmed_payment_is_refused(
    app_database: Database, worker_database: Database
) -> None:
    client = await approved_client(app_database, PRESENT)
    investment = await request(app_database, client, PRESENT, amount=ugx(MINOR))
    pending = await start_deposit(
        app_database, PRESENT, actor=client, amount=MINOR, purpose_id=investment.id
    )
    with pytest.raises(ApplicationError) as refused:
        await propose(app_database, staff(), pending.id)
    assert code(refused) == "not_confirmed"
    with pytest.raises(ApplicationError) as missing:
        await propose(app_database, staff(), push_id())
    assert missing.value.code == "not_found"


async def refused_sql(database: Database, actor: Actor, sql: str, **parameters: object) -> None:
    with pytest.raises(DBAPIError):
        async with database.actor_transaction(actor) as session:
            await session.execute(text(sql), parameters)


async def test_the_database_holds_everyone_to_their_part(
    app_database: Database, worker_database: Database
) -> None:
    investment, deposit = await paid(app_database, worker_database)
    someone = staff()
    insert = (
        "INSERT INTO payment_deposit_reversals (id, deposit_id, client_id, investment_id, "
        "currency, amount_minor, reason, status, proposed_by, proposed_at) VALUES (:id, "
        ":deposit, :client, :investment, 'UGX', :minor, 'reason', :status, :by, now())"
    )
    # In someone else's name, for another amount, or already approved.
    for by, minor, status in (
        (push_id(), MINOR, "proposed"),
        (someone.id, MINOR + 1, "proposed"),
        (someone.id, MINOR, "approved"),
    ):
        await refused_sql(
            app_database,
            someone,
            insert,
            id=push_id(),
            deposit=deposit.id,
            client=investment.client_id,
            investment=investment.id,
            minor=minor,
            status=status,
            by=by,
        )
    proposed = await propose(app_database, someone, deposit.id)
    # The proposer cannot approve their own, even in SQL.
    await refused_sql(
        app_database,
        someone,
        "UPDATE payment_deposit_reversals SET status = 'approved', decided_by = :by, "
        "decided_at = now() WHERE id = :id",
        by=someone.id,
        id=proposed.id,
    )
    checker = staff()
    await approve(app_database, checker, proposed.id)
    # Staff cannot record an outcome. Only the worker does.
    await refused_sql(
        app_database,
        checker,
        "UPDATE payment_deposit_reversals SET status = 'failed', done_at = now(), "
        "failure_code = 'x' WHERE id = :id",
        id=proposed.id,
    )
    # The worker cannot call it done without its own reversing entry.
    await refused_sql(
        worker_database,
        SYSTEM,
        "UPDATE payment_deposit_reversals SET status = 'done', done_at = now(), "
        "entry_id = :entry WHERE id = :id",
        entry=deposit.ledger_entry_id,
        id=proposed.id,
    )
    # Nor mark a deposit reversed without a done reversal.
    await refused_sql(
        worker_database,
        SYSTEM,
        "UPDATE payment_deposits SET reversal_id = :id WHERE id = :deposit",
        id=proposed.id,
        deposit=deposit.id,
    )
    done = await apply(worker_database, proposed.id)
    assert done.status is Status.DONE
    # Once set, the mark never changes, and a finished reversal never changes.
    await refused_sql(
        worker_database,
        SYSTEM,
        "UPDATE payment_deposits SET reversal_id = NULL WHERE id = :deposit",
        deposit=deposit.id,
    )
    await refused_sql(
        worker_database,
        SYSTEM,
        "UPDATE payment_deposit_reversals SET failure_code = 'x' WHERE id = :id",
        id=proposed.id,
    )
    await refused_sql(
        worker_database,
        SYSTEM,
        "DELETE FROM payment_deposit_reversals WHERE id = :id",
        id=proposed.id,
    )
    # A client reads no reversal.
    client = Actor("client", investment.client_id)
    async with app_database.actor_transaction(client) as session:
        seen = await session.scalar(text("SELECT count(*) FROM payment_deposit_reversals"))
    assert seen == 0
