"""What a client owes for a deposit credited by mistake once the money has left, against real
PostgreSQL and the ledger: opened, then recovered or written off, each by two people, and the
database holds everyone to their part."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.investments import service as investments
from app.modules.investments.holdings import AWAITING_START
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.payments import receivables, service
from app.modules.payments.domain.receivables import StepKind, StepStatus
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.investments import PRESENT, account_balance, today
from tests.support.ledger import staff, ugx
from tests.support.mistaken_deposits import MINOR, paid, start

pytestmark = pytest.mark.integration

Kind = StepKind
Status = StepStatus


async def money_left(worker_database: Database, deposit: service.Deposit) -> None:
    """Move the money waiting for the investment out, as a refund or a payout would."""
    async with worker_database.actor_transaction(SYSTEM) as session:
        waiting = await ledger.open_account(
            session,
            AccountSpec(
                AWAITING_START,
                AccountKind.LIABILITY,
                deposit.amount.currency,
                "Paid in, awaiting start",
                owner_client_id=deposit.client_id,
                reference=deposit.purpose_id,
            ),
            PRESENT,
        )
        gone = await ledger.open_account(
            session,
            AccountSpec("test.paid_out", AccountKind.LIABILITY, deposit.amount.currency, "Out"),
            PRESENT,
        )
        await ledger.post_entry(
            session,
            EntryDraft(
                entry_type="test.money_left",
                effective_date=business_date(PRESENT.now()),
                lines=(debit(waiting.id, deposit.amount), credit(gone.id, deposit.amount)),
                idempotency_key=f"left-{push_id()}",
            ),
            PRESENT,
        )


async def propose(
    app_database: Database, maker: Actor, kind: StepKind, deposit_id: str
) -> receivables.Step:
    async with app_database.actor_transaction(maker) as session:
        return await receivables.propose_step(
            session,
            maker,
            kind=kind,
            deposit_id=deposit_id,
            reason="The provider confirmed a payment that never reached our wallet",
            idempotency_key=f"propose-{push_id()}",
            clock=PRESENT,
        )


async def approve(app_database: Database, checker: Actor, step_id: str) -> receivables.Step:
    async with app_database.actor_transaction(checker) as session:
        return await receivables.approve_step(
            session, checker, step_id, idempotency_key=f"approve-{push_id()}", clock=PRESENT
        )


async def both(app_database: Database, kind: StepKind, deposit_id: str) -> receivables.Step:
    proposed = await propose(app_database, staff(), kind, deposit_id)
    return await approve(app_database, staff(), proposed.id)


async def balance(database: Database, account_id: str) -> int:
    async with database.actor_transaction(SYSTEM) as session:
        return (await ledger.get_balance(session, account_id)).minor


async def cash_account(database: Database, deposit: service.Deposit) -> str:
    async with database.actor_transaction(SYSTEM) as session:
        entry = await ledger.get_entry(session, deposit.ledger_entry_id or "")
    [line] = [line for line in entry.lines if line.amount.minor > 0]
    return line.account_id


async def owed(database: Database, deposit: service.Deposit) -> int:
    async with database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(
            session,
            AccountSpec(
                receivables.RECEIVABLE,
                AccountKind.ASSET,
                deposit.amount.currency,
                "Owed for a payment credited by mistake",
                owner_client_id=deposit.client_id,
                reference=deposit.id,
            ),
            FakeClock(),
        )
        return (await ledger.get_balance(session, account.id)).minor


async def outstanding(app_database: Database) -> list[str]:
    viewer = staff()
    async with app_database.actor_transaction(viewer) as session:
        return [step.deposit_id for step in await receivables.outstanding(session)]


def code(error: pytest.ExceptionInfo[ApplicationError]) -> str:
    assert error.value.code == "validation_failed", error.value.code
    return error.value.fields[0].code


async def test_two_people_open_a_receivable_and_two_more_recover_it(
    app_database: Database, worker_database: Database
) -> None:
    investment, deposit = await paid(app_database, worker_database)
    cash = await cash_account(worker_database, deposit)
    # While the money waits, the deposit is reversed instead.
    with pytest.raises(ApplicationError) as waiting:
        await propose(app_database, staff(), Kind.OPEN, deposit.id)
    assert code(waiting) == "reverse_instead"
    # Nothing to recover before it is opened.
    with pytest.raises(ApplicationError) as unopened:
        await propose(app_database, staff(), Kind.RECOVER, deposit.id)
    assert code(unopened) == "not_open"
    await money_left(worker_database, deposit)
    before = await balance(worker_database, cash)

    maker, checker = staff(), staff()
    proposed = await propose(app_database, maker, Kind.OPEN, deposit.id)
    assert (proposed.status, proposed.amount) == (Status.PROPOSED, ugx(MINOR))
    with pytest.raises(ApplicationError) as own:
        await approve(app_database, maker, proposed.id)
    assert own.value.code == "forbidden"
    assert await owed(worker_database, deposit) == 0

    opened = await approve(app_database, checker, proposed.id)
    assert (opened.status, opened.decided_by) == (Status.APPLIED, checker.id)
    assert await owed(worker_database, deposit) == MINOR
    # The money never reached the wallet, so the books stop saying it did.
    assert await balance(worker_database, cash) == before - MINOR
    assert deposit.id in await outstanding(app_database)
    with pytest.raises(ApplicationError) as twice:
        await propose(app_database, staff(), Kind.OPEN, deposit.id)
    assert code(twice) == "already_open"

    recovered = await both(app_database, Kind.RECOVER, deposit.id)
    assert recovered.status is Status.APPLIED
    assert await owed(worker_database, deposit) == 0
    assert await balance(worker_database, cash) == before
    assert deposit.id not in await outstanding(app_database)
    with pytest.raises(ApplicationError) as cleared:
        await propose(app_database, staff(), Kind.WRITE_OFF, deposit.id)
    assert code(cleared) == "already_cleared"
    async with worker_database.actor_transaction(SYSTEM) as session:
        entry = await ledger.get_entry(session, recovered.entry_id or "")
    assert (entry.entry_type, entry.source_type, entry.source_id) == (
        "payment.receivable_recovered",
        "payment.receivable",
        recovered.id,
    )
    assert await account_balance(worker_database, investment, AWAITING_START) == 0


async def test_a_receivable_written_off_is_booked_as_a_loss(
    app_database: Database, worker_database: Database
) -> None:
    _, deposit = await paid(app_database, worker_database)
    await money_left(worker_database, deposit)
    await both(app_database, Kind.OPEN, deposit.id)

    written_off = await both(app_database, Kind.WRITE_OFF, deposit.id)
    assert await owed(worker_database, deposit) == 0
    async with worker_database.actor_transaction(SYSTEM) as session:
        entry = await ledger.get_entry(session, written_off.entry_id or "")
        losses = await ledger.open_account(
            session,
            AccountSpec(receivables.LOSSES, AccountKind.EXPENSE, deposit.amount.currency, "x"),
            FakeClock(),
        )
    assert entry.entry_type == "payment.receivable_written_off"
    assert {(line.account_id, line.amount.minor) for line in entry.lines} >= {(losses.id, MINOR)}


async def test_a_running_investment_is_unwound_first(
    app_database: Database, worker_database: Database
) -> None:
    investment, deposit = await paid(app_database, worker_database)
    await start(worker_database, investment.id)
    with pytest.raises(ApplicationError) as refused:
        await propose(app_database, staff(), Kind.OPEN, deposit.id)
    assert code(refused) == "unwind_first"
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert (await investments.get_investment(session, investment.id)).start_date == today()


async def test_two_approvals_at_once_post_once(
    app_database: Database, worker_database: Database
) -> None:
    _, deposit = await paid(app_database, worker_database)
    await money_left(worker_database, deposit)
    proposed = await propose(app_database, staff(), Kind.OPEN, deposit.id)

    async def attempt() -> str:
        try:
            await approve(app_database, staff(), proposed.id)
        except ApplicationError as error:
            return error.code
        return "applied"

    assert sorted(await asyncio.gather(attempt(), attempt())) == ["applied", "conflict"]
    assert await owed(worker_database, deposit) == MINOR


async def test_only_the_proposer_withdraws_and_only_someone_else_sends_back(
    app_database: Database, worker_database: Database
) -> None:
    _, deposit = await paid(app_database, worker_database)
    await money_left(worker_database, deposit)
    maker, other = staff(), staff()
    proposed = await propose(app_database, maker, Kind.OPEN, deposit.id)
    # One step waits at a time.
    with pytest.raises(ApplicationError) as waiting:
        await propose(app_database, other, Kind.OPEN, deposit.id)
    assert waiting.value.code == "conflict"
    async with app_database.actor_transaction(other) as session:
        with pytest.raises(ApplicationError) as not_yours:
            await receivables.withdraw_step(
                session, other, proposed.id, idempotency_key=f"w-{push_id()}", clock=PRESENT
            )
    assert not_yours.value.code == "forbidden"
    async with app_database.actor_transaction(other) as session:
        sent_back = await receivables.send_back_step(
            session,
            other,
            proposed.id,
            "Check the wallet statement first",
            idempotency_key=f"s-{push_id()}",
            clock=PRESENT,
        )
    assert sent_back.status is Status.SENT_BACK
    again = await propose(app_database, maker, Kind.OPEN, deposit.id)
    async with app_database.actor_transaction(maker) as session:
        withdrawn = await receivables.withdraw_step(
            session, maker, again.id, idempotency_key=f"w-{push_id()}", clock=PRESENT
        )
    assert withdrawn.status is Status.WITHDRAWN
    assert await owed(worker_database, deposit) == 0


async def refused_sql(database: Database, actor: Actor, sql: str, **parameters: object) -> None:
    with pytest.raises(DBAPIError):
        async with database.actor_transaction(actor) as session:
            await session.execute(text(sql), parameters)


async def test_the_database_holds_everyone_to_their_part(
    app_database: Database, worker_database: Database
) -> None:
    _, deposit = await paid(app_database, worker_database)
    await money_left(worker_database, deposit)
    someone = staff()
    insert = (
        "INSERT INTO payment_receivable_steps (id, deposit_id, kind, client_id, currency, "
        "amount_minor, reason, proposed_by, proposed_at) VALUES (:id, :deposit, :kind, :client, "
        "'UGX', :minor, 'reason', :by, now())"
    )
    # Clearing before opening, another amount, or in someone else's name.
    for kind, minor, by in (
        ("recover", MINOR, someone.id),
        ("open", MINOR + 1, someone.id),
        ("open", MINOR, push_id()),
    ):
        await refused_sql(
            app_database,
            someone,
            insert,
            id=push_id(),
            deposit=deposit.id,
            kind=kind,
            client=deposit.client_id,
            minor=minor,
            by=by,
        )
    proposed = await propose(app_database, someone, Kind.OPEN, deposit.id)
    checker = staff()
    # Applied with an entry that is not its own.
    await refused_sql(
        app_database,
        checker,
        "UPDATE payment_receivable_steps SET status = 'applied', decided_by = :by, "
        "decided_at = now(), entry_id = :entry WHERE id = :id",
        by=checker.id,
        entry=deposit.ledger_entry_id,
        id=proposed.id,
    )
    applied = await approve(app_database, checker, proposed.id)
    await refused_sql(
        app_database,
        checker,
        "UPDATE payment_receivable_steps SET decision_note = 'x' WHERE id = :id",
        id=applied.id,
    )
    await refused_sql(
        app_database,
        checker,
        "DELETE FROM payment_receivable_steps WHERE id = :id",
        id=applied.id,
    )
    # The worker and clients do not record steps, and a client reads none.
    await refused_sql(
        worker_database,
        SYSTEM,
        insert,
        id=push_id(),
        deposit=deposit.id,
        kind="recover",
        client=deposit.client_id,
        minor=MINOR,
        by=someone.id,
    )
    client = Actor("client", deposit.client_id)
    async with app_database.actor_transaction(client) as session:
        seen = await session.scalar(text("SELECT count(*) FROM payment_receivable_steps"))
    assert seen == 0
