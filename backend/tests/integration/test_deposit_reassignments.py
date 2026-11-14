"""Moving a bank transfer credited to the wrong client, against real PostgreSQL, real
statements, the ledger and the job queue. One person proposes, a different one approves and the
money moves in that same transaction, and the database holds everyone to their part."""

import asyncio
import secrets
from dataclasses import dataclass
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker
from app.modules.investments import service as investments
from app.modules.investments.holdings import AWAITING_START
from app.modules.ledger import service as ledger
from app.modules.payments import bank_transfers as bank
from app.modules.payments import reassignments, routes, service
from app.modules.payments.bank_transfers import BankTransferJobs, BankTransferRequest, Matched
from app.modules.payments.domain.bank_transfers import ReceiptDetails, ReceiptFile
from app.modules.payments.domain.deposits import DepositStatus, Method, Purpose
from app.modules.payments.domain.reassignments import ReassignmentStatus
from app.modules.reconciliation import statements
from app.modules.reconciliation.domain.statements import CsvFormat, StatementSource, parse_csv
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.books import audit_for
from tests.support.investments import (
    PRESENT,
    account_balance,
    approved_client,
    request,
    today,
)
from tests.support.ledger import ugx
from tests.support.payments import drain

pytestmark = pytest.mark.integration

Status = ReassignmentStatus
DAY = today() - timedelta(days=1)
FORMAT = CsvFormat(
    value_date="Date",
    date_format="%Y-%m-%d",
    narrative="Details",
    reference="Reference",
    money_in="In",
    money_out="Out",
)


def staff() -> Actor:
    return Actor("staff", push_id())


def amount() -> int:
    # Statements are shared by every test, so each test pays an amount nobody else uses.
    return 1_000_000 + secrets.randbelow(10**9)


@dataclass
class Mistake:
    """A transfer from `right` that two people confirmed for `wrong`'s investment."""

    wrong: Actor
    right: Actor
    first: investments.Investment
    target: investments.Investment
    deposit: service.Deposit
    line_id: str


async def a_statement_line(database: Database, minor: int, reference: str) -> str:
    """Import a bank statement for a pot of its own with one line of money in."""
    pot = f"trust-{secrets.token_hex(4)}"
    opening = 10**12
    parsed = parse_csv(
        "Date,Details,Reference,In,Out\n"
        f"{DAY.isoformat()},Transfer from a client,{reference},{minor},\n",
        FORMAT,
        source=StatementSource.BANK,
        account_reference=pot,
        statement_date=DAY,
        opening_balance=ugx(opening),
        closing_balance=ugx(opening + minor),
    )
    importer = staff()
    async with database.actor_transaction(importer) as session:
        imported = await statements.import_statement(session, importer, parsed, "0" * 64, PRESENT)
        found = await statements.find_statement_lines(
            session,
            source=StatementSource.BANK,
            date_from=DAY,
            date_to=DAY,
            amount=ugx(minor),
        )
    [line] = [line for line in found if line.account_reference == imported.account_reference]
    return line.id


async def confirmed_transfer(
    app_database: Database,
    worker_database: Database,
    client: Actor,
    investment: investments.Investment,
    line_id: str,
) -> service.Deposit:
    """The client's receipt, matched to `line_id` by one person and approved by another."""
    async with app_database.actor_transaction(client) as session:
        transfer = await bank.submit_bank_receipt(
            session,
            client,
            BankTransferRequest(
                Purpose.INVESTMENT,
                investment.id,
                ReceiptFile("application/pdf", 48_000, secrets.token_hex(32)),
                ReceiptDetails(investment.amount, DAY, "Stanbic Bank", "FT2632100042"),
            ),
            idempotency_key=f"key-{push_id()}",
            clock=PRESENT,
        )
    matcher, approver = staff(), staff()
    async with app_database.actor_transaction(matcher) as session:
        await bank.review_bank_transfer(
            session,
            matcher,
            transfer.deposit.id,
            Matched(line_id),
            idempotency_key=f"review-{push_id()}",
            clock=PRESENT,
        )
    async with app_database.actor_transaction(approver) as session:
        await bank.approve_bank_match(
            session,
            approver,
            transfer.deposit.id,
            idempotency_key=f"approve-{push_id()}",
            clock=PRESENT,
        )
    worker = Worker(
        worker_database, BankTransferJobs(worker_database, PRESENT).handlers(), clock=PRESENT
    )
    await drain(worker)
    deposit = await reload(worker_database, transfer.deposit.id)
    assert deposit.status is DepositStatus.CONFIRMED
    return deposit


async def a_mistake(
    app_database: Database, worker_database: Database, *, target_minor: int | None = None
) -> Mistake:
    minor = amount()
    wrong = await approved_client(app_database, PRESENT)
    right = await approved_client(app_database, PRESENT)
    first = await request(app_database, wrong, PRESENT, amount=ugx(minor))
    target = await request(app_database, right, PRESENT, amount=ugx(target_minor or minor))
    line_id = await a_statement_line(app_database, minor, "FINVEST TRANSFER")
    deposit = await confirmed_transfer(app_database, worker_database, wrong, first, line_id)
    return Mistake(wrong, right, first, target, deposit, line_id)


async def reload(database: Database, deposit_id: str) -> service.Deposit:
    async with database.actor_transaction(SYSTEM) as session:
        return await service.get_deposit(session, deposit_id)


async def propose(
    app_database: Database,
    maker: Actor,
    deposit_id: str,
    to_investment_id: str,
    *,
    key: str | None = None,
) -> reassignments.Reassignment:
    async with app_database.actor_transaction(maker) as session:
        return await reassignments.propose_reassignment(
            session,
            maker,
            deposit_id=deposit_id,
            to_investment_id=to_investment_id,
            reason="Matched to the wrong client's receipt; the line names the other account",
            idempotency_key=key or f"propose-{push_id()}",
            clock=PRESENT,
        )


async def approve(
    app_database: Database, checker: Actor, reassignment_id: str, *, key: str | None = None
) -> reassignments.Reassignment:
    async with app_database.actor_transaction(checker) as session:
        return await reassignments.approve_reassignment(
            session,
            checker,
            reassignment_id,
            idempotency_key=key or f"approve-{push_id()}",
            clock=PRESENT,
        )


def code(error: pytest.ExceptionInfo[ApplicationError]) -> str:
    assert error.value.code == "validation_failed", error.value.code
    return error.value.fields[0].code


async def scalar(database: Database, sql: str, **parameters: object) -> object:
    async with database.actor_transaction(SYSTEM) as session:
        return await session.scalar(text(sql), parameters)


async def test_two_people_move_the_money_to_the_right_client_and_their_investment_starts(
    app_database: Database, worker_database: Database, migrator_database: Database
) -> None:
    mistake = await a_mistake(app_database, worker_database)
    minor = mistake.deposit.amount.minor
    assert await account_balance(worker_database, mistake.first, AWAITING_START) == minor
    maker, checker = staff(), staff()

    proposed = await propose(app_database, maker, mistake.deposit.id, mistake.target.id)
    assert proposed.status is Status.PROPOSED
    assert (proposed.statement_line_id, proposed.to_client_id) == (
        mistake.line_id,
        mistake.right.id,
    )
    # Nothing moves on a proposal, and the proposer can never approve their own.
    assert await account_balance(worker_database, mistake.target, AWAITING_START) == 0
    with pytest.raises(ApplicationError) as refused:
        await approve(app_database, maker, proposed.id)
    assert refused.value.code == "forbidden"

    applied = await approve(app_database, checker, proposed.id, key="approve-once-only-1")
    assert (applied.status, applied.decided_by) == (Status.APPLIED, checker.id)
    assert applied.entry_id is not None and applied.new_deposit_id is not None
    assert await account_balance(worker_database, mistake.first, AWAITING_START) == 0
    assert await account_balance(worker_database, mistake.target, AWAITING_START) == minor

    # The right client has a confirmed bank transfer for their investment, from the entry.
    moved = await reload(worker_database, applied.new_deposit_id)
    assert (moved.client_id, moved.purpose_id, moved.status, moved.method) == (
        mistake.right.id,
        mistake.target.id,
        DepositStatus.CONFIRMED,
        Method.BANK_TRANSFER,
    )
    assert (moved.amount, moved.ledger_entry_id) == (mistake.deposit.amount, applied.entry_id)
    # The original deposit and its entry are as they were.
    assert await reload(worker_database, mistake.deposit.id) == mistake.deposit
    async with worker_database.actor_transaction(SYSTEM) as session:
        entry = await ledger.get_entry(session, applied.entry_id)
    assert (entry.entry_type, entry.source_type, entry.source_id) == (
        reassignments.ENTRY_TYPE,
        reassignments.SOURCE_TYPE,
        applied.id,
    )
    # Investments hears of it once, as of any confirmed deposit, and starts the investment.
    published = await scalar(
        worker_database,
        "SELECT count(*) FROM jobs WHERE kind = 'investments.start_from_deposit' "
        "AND payload->'data'->>'deposit_id' = :id",
        id=moved.id,
    )
    assert published == 1
    async with worker_database.actor_transaction(SYSTEM) as session:
        started = await investments.start_investment(
            session, mistake.target.id, confirmed_on=today(), clock=PRESENT
        )
    assert started.status.value == "active"

    # The same key again changes nothing more.
    again = await approve(app_database, checker, proposed.id, key="approve-once-only-1")
    assert again.entry_id == applied.entry_id
    # And a decided reassignment is never decided again.
    with pytest.raises(ApplicationError) as decided:
        await approve(app_database, staff(), proposed.id)
    assert decided.value.code == "conflict"

    trail = await audit_for(migrator_database, [proposed.id, moved.id])
    assert [(row.action, row.resource_id, row.actor_id) for row in trail] == [
        ("payments.reassignment_proposed", proposed.id, maker.id),
        # The approver confirmed the new deposit, in their own name.
        ("payments.deposit_confirmed", moved.id, checker.id),
        ("payments.reassignment_applied", proposed.id, checker.id),
    ]


async def test_the_right_client_sees_the_payment_without_a_receipt_of_their_own(
    app_database: Database, worker_database: Database
) -> None:
    mistake = await a_mistake(app_database, worker_database)
    proposed = await propose(app_database, staff(), mistake.deposit.id, mistake.target.id)
    applied = await approve(app_database, staff(), proposed.id)
    async with app_database.actor_transaction(mistake.right) as session:
        found = await service.live_deposit_for(
            session, purpose=Purpose.INVESTMENT, purpose_id=mistake.target.id
        )
        assert found is not None
        view = await routes._payment_view(session, found)
    assert (view.id, view.status, view.receipt) == (applied.new_deposit_id, "confirmed", None)
    # The first client cannot see the reassignment, or the new payment.
    async with app_database.actor_transaction(mistake.wrong) as session:
        assert await service.find_deposit(session, str(applied.new_deposit_id)) is None
        rows = await session.execute(text("SELECT count(*) FROM payment_reassignments"))
        assert rows.scalar_one() == 0


async def test_a_deposit_whose_investment_started_is_refused_until_it_is_undone(
    app_database: Database, worker_database: Database
) -> None:
    mistake = await a_mistake(app_database, worker_database)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(
            session, mistake.first.id, confirmed_on=today(), clock=PRESENT
        )
    with pytest.raises(ApplicationError) as refused:
        await propose(app_database, staff(), mistake.deposit.id, mistake.target.id)
    assert code(refused) == "money_moved_on"


async def test_the_target_must_be_another_clients_investment_waiting_for_exactly_the_amount(
    app_database: Database, worker_database: Database
) -> None:
    mistake = await a_mistake(app_database, worker_database, target_minor=1_000_000)
    with pytest.raises(ApplicationError) as differs:
        await propose(app_database, staff(), mistake.deposit.id, mistake.target.id)
    assert code(differs) == "amount_differs_from_investment"

    same = await request(app_database, mistake.wrong, PRESENT, amount=mistake.deposit.amount)
    with pytest.raises(ApplicationError) as own:
        await propose(app_database, staff(), mistake.deposit.id, same.id)
    assert code(own) == "same_client"

    with pytest.raises(ApplicationError) as unknown:
        await propose(app_database, staff(), mistake.deposit.id, push_id())
    assert code(unknown) == "not_found"


async def test_a_target_that_has_a_payment_of_its_own_is_refused(
    app_database: Database, worker_database: Database
) -> None:
    mistake = await a_mistake(app_database, worker_database)
    other_line = await a_statement_line(app_database, mistake.deposit.amount.minor, "SOMEONE ELSE")
    await confirmed_transfer(
        app_database, worker_database, mistake.right, mistake.target, other_line
    )
    with pytest.raises(ApplicationError) as refused:
        await propose(app_database, staff(), mistake.deposit.id, mistake.target.id)
    assert code(refused) == "already_has_a_payment"


async def test_only_the_proposer_withdraws_and_only_someone_else_sends_back(
    app_database: Database, worker_database: Database
) -> None:
    mistake = await a_mistake(app_database, worker_database)
    maker, other = staff(), staff()
    proposed = await propose(app_database, maker, mistake.deposit.id, mistake.target.id)
    # One open proposal per deposit.
    with pytest.raises(ApplicationError) as twice:
        await propose(app_database, other, mistake.deposit.id, mistake.target.id)
    assert twice.value.code == "conflict"

    async with app_database.actor_transaction(other) as session:
        with pytest.raises(ApplicationError) as not_theirs:
            await reassignments.withdraw_reassignment(
                session, other, proposed.id, idempotency_key=f"w-{push_id()}", clock=PRESENT
            )
    assert not_theirs.value.code == "forbidden"
    async with app_database.actor_transaction(maker) as session:
        with pytest.raises(ApplicationError) as own:
            await reassignments.send_back_reassignment(
                session, maker, proposed.id, "No", idempotency_key=f"s-{push_id()}", clock=PRESENT
            )
    assert own.value.code == "forbidden"
    async with app_database.actor_transaction(other) as session:
        sent_back = await reassignments.send_back_reassignment(
            session,
            other,
            proposed.id,
            "The line's reference names neither client. Ask the bank first.",
            idempotency_key=f"s-{push_id()}",
            clock=PRESENT,
        )
    assert (sent_back.status, sent_back.decided_by) == (Status.SENT_BACK, other.id)
    assert await account_balance(worker_database, mistake.first, AWAITING_START) == (
        mistake.deposit.amount.minor
    )

    # Sent back frees the deposit for a new proposal, which its proposer can withdraw.
    again = await propose(app_database, maker, mistake.deposit.id, mistake.target.id)
    async with app_database.actor_transaction(maker) as session:
        withdrawn = await reassignments.withdraw_reassignment(
            session, maker, again.id, idempotency_key=f"w-{push_id()}", clock=PRESENT
        )
    assert withdrawn.status is Status.WITHDRAWN


async def test_two_approvals_at_once_move_the_money_once(
    app_database: Database, worker_database: Database
) -> None:
    mistake = await a_mistake(app_database, worker_database)
    proposed = await propose(app_database, staff(), mistake.deposit.id, mistake.target.id)

    async def attempt() -> str:
        try:
            await approve(app_database, staff(), proposed.id)
        except ApplicationError as error:
            return error.code
        return "applied"

    results = sorted(await asyncio.gather(attempt(), attempt()))
    assert results == ["applied", "conflict"]
    assert await account_balance(worker_database, mistake.target, AWAITING_START) == (
        mistake.deposit.amount.minor
    )
    entries = await scalar(
        worker_database,
        "SELECT count(*) FROM ledger_journal_entries WHERE source_id = :id",
        id=proposed.id,
    )
    assert entries == 1


async def test_an_approval_after_the_money_moved_is_refused_and_stays_open(
    app_database: Database, worker_database: Database
) -> None:
    mistake = await a_mistake(app_database, worker_database)
    proposed = await propose(app_database, staff(), mistake.deposit.id, mistake.target.id)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await investments.start_investment(
            session, mistake.first.id, confirmed_on=today(), clock=PRESENT
        )
    checker = staff()
    with pytest.raises(ApplicationError) as refused:
        await approve(app_database, checker, proposed.id)
    assert code(refused) == "money_moved_on"
    async with app_database.actor_transaction(checker) as session:
        detail = await reassignments.get_reassignment(session, checker, proposed.id, PRESENT)
    assert detail.reassignment.status is Status.PROPOSED
    assert detail.refusal is not None and detail.refusal.value == "money_moved_on"


async def test_a_reassigned_deposit_can_itself_be_moved_on_the_same_evidence(
    app_database: Database, worker_database: Database
) -> None:
    mistake = await a_mistake(app_database, worker_database)
    first = await approve(
        app_database,
        staff(),
        (await propose(app_database, staff(), mistake.deposit.id, mistake.target.id)).id,
    )
    third = await approved_client(app_database, PRESENT)
    elsewhere = await request(app_database, third, PRESENT, amount=mistake.deposit.amount)
    assert first.new_deposit_id is not None
    second = await propose(app_database, staff(), first.new_deposit_id, elsewhere.id)
    assert second.statement_line_id == mistake.line_id
    viewer = staff()
    async with app_database.actor_transaction(viewer) as session:
        detail = await reassignments.get_reassignment(session, viewer, second.id, PRESENT)
        for_line = await reassignments.reassignments_for_line(session, mistake.line_id)
    assert detail.line is not None and detail.line.id == mistake.line_id
    assert [r.id for r in for_line] == [first.id, second.id]
    done = await approve(app_database, staff(), second.id)
    assert done.status is Status.APPLIED
    assert await account_balance(worker_database, mistake.target, AWAITING_START) == 0
    assert await account_balance(worker_database, elsewhere, AWAITING_START) == (
        mistake.deposit.amount.minor
    )


# The database's own fences, tried directly.


async def refused_sql(database: Database, actor: Actor, sql: str, **parameters: object) -> None:
    with pytest.raises(DBAPIError):
        async with database.actor_transaction(actor) as session:
            await session.execute(text(sql), parameters)


async def test_the_database_refuses_staff_moving_money_outside_a_reassignment(
    app_database: Database, worker_database: Database
) -> None:
    mistake = await a_mistake(app_database, worker_database)
    someone = staff()
    # A confirmed deposit saved by staff with no applied reassignment behind it.
    await refused_sql(
        app_database,
        someone,
        "INSERT INTO payment_deposits (id, client_id, purpose_type, purpose_id, method, "
        "provider, currency, amount_minor, status, ledger_entry_id, created_at, updated_at, "
        "settled_at) VALUES (:id, :client, 'investment', :target, 'bank_transfer', 'bank', "
        "'UGX', :minor, 'confirmed', :entry, now(), now(), now())",
        id=push_id(),
        client=mistake.right.id,
        target=mistake.target.id,
        minor=mistake.deposit.amount.minor,
        entry=mistake.deposit.ledger_entry_id,
    )
    # A proposal in someone else's name, or for a line the review did not match.
    for proposer, line in ((push_id(), mistake.line_id), (someone.id, push_id())):
        await refused_sql(
            app_database,
            someone,
            "INSERT INTO payment_reassignments (id, deposit_id, from_client_id, "
            "from_investment_id, to_client_id, to_investment_id, currency, amount_minor, "
            "statement_line_id, reason, proposed_by, proposed_at) VALUES (:id, :deposit, "
            ":from_client, :first, :to_client, :target, 'UGX', :minor, :line, 'r', :by, now())",
            id=push_id(),
            deposit=mistake.deposit.id,
            from_client=mistake.wrong.id,
            first=mistake.first.id,
            to_client=mistake.right.id,
            target=mistake.target.id,
            minor=mistake.deposit.amount.minor,
            line=line,
            by=proposer,
        )
    proposed = await propose(app_database, someone, mistake.deposit.id, mistake.target.id)
    checker = staff()
    # Applied with an entry that is not its own.
    await refused_sql(
        app_database,
        checker,
        "UPDATE payment_reassignments SET status = 'applied', decided_by = :by, "
        "decided_at = now(), entry_id = :entry, new_deposit_id = :new WHERE id = :id",
        by=checker.id,
        entry=mistake.deposit.ledger_entry_id,
        new=push_id(),
        id=proposed.id,
    )
    # Decided in someone else's name, or by the proposer.
    await refused_sql(
        app_database,
        checker,
        "UPDATE payment_reassignments SET status = 'sent_back', decided_by = :by, "
        "decided_at = now(), decision_note = 'n' WHERE id = :id",
        by=push_id(),
        id=proposed.id,
    )
    await refused_sql(
        app_database,
        someone,
        "UPDATE payment_reassignments SET status = 'sent_back', decided_by = :by, "
        "decided_at = now(), decision_note = 'n' WHERE id = :id",
        by=someone.id,
        id=proposed.id,
    )
    # The terms never change.
    await refused_sql(
        app_database,
        checker,
        "UPDATE payment_reassignments SET to_investment_id = :other, status = 'sent_back', "
        "decided_by = :by, decided_at = now(), decision_note = 'n' WHERE id = :id",
        other=push_id(),
        by=checker.id,
        id=proposed.id,
    )
    applied = await approve(app_database, checker, proposed.id)
    # A finished one never changes and nothing is deleted, whoever asks.
    await refused_sql(
        app_database,
        checker,
        "UPDATE payment_reassignments SET decision_note = 'later' WHERE id = :id",
        id=applied.id,
    )
    await refused_sql(
        worker_database, SYSTEM, "DELETE FROM payment_reassignments WHERE id = :id", id=applied.id
    )
    # The worker and clients cannot reassign at all.
    await refused_sql(
        worker_database,
        SYSTEM,
        "UPDATE payment_reassignments SET reason = 'x' WHERE id = :id",
        id=applied.id,
    )


async def test_an_applied_reassignment_without_its_new_deposit_does_not_commit(
    app_database: Database, worker_database: Database
) -> None:
    mistake = await a_mistake(app_database, worker_database)
    proposed = await propose(app_database, staff(), mistake.deposit.id, mistake.target.id)
    checker = staff()
    with pytest.raises(DBAPIError) as refused:
        async with app_database.actor_transaction(checker) as session:
            # The right entry, posted as the approval would, but no new deposit saved after it.
            entry_id = await reassignments._move_money(session, proposed, PRESENT)
            await session.execute(
                text(
                    "UPDATE payment_reassignments SET status = 'applied', decided_by = :by, "
                    "decided_at = now(), entry_id = :entry, new_deposit_id = :new WHERE id = :id"
                ),
                {"by": checker.id, "entry": entry_id, "new": push_id(), "id": proposed.id},
            )
    assert "has its new deposit" in str(refused.value)
    assert await account_balance(worker_database, mistake.target, AWAITING_START) == 0
