"""Bank transfer deposits against real PostgreSQL, the job queue, the ledger and real statements.

A client tells us about a transfer with a receipt, a staff member finds it on an imported bank
statement or says why not, and the worker settles it exactly as the review says.
"""

import asyncio
import secrets
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec
from app.modules.payments import bank_transfers as bank
from app.modules.payments import service
from app.modules.payments.bank_transfers import (
    BankTransferJobs,
    BankTransferRequest,
    Matched,
    NotMatched,
)
from app.modules.payments.domain.bank_transfers import (
    MAX_RECEIPT_BYTES,
    NotMatchedReason,
    ReceiptDetails,
    ReceiptFile,
)
from app.modules.payments.domain.deposits import DepositStatus, Method, Purpose
from app.modules.payments.service import Deposit, DepositExists
from app.modules.reconciliation import statements
from app.modules.reconciliation.domain.statements import (
    CsvFormat,
    Direction,
    StatementSource,
    parse_csv,
)
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.payments import client, drain, start_deposit, ugx

pytestmark = pytest.mark.integration

S = DepositStatus
NOW = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)
SENT_ON = date(2026, 11, 17)
FORMAT = CsvFormat(
    value_date="Date",
    date_format="%Y-%m-%d",
    narrative="Details",
    reference="Reference",
    money_in="In",
    money_out="Out",
)


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def worker(worker_database: Database, fake_clock: FakeClock) -> Worker:
    return Worker(
        worker_database, BankTransferJobs(worker_database, fake_clock).handlers(), clock=fake_clock
    )


def staff() -> Actor:
    return Actor("staff", push_id())


def amount() -> int:
    # Statements are shared by every test, so each test pays an amount nobody else uses.
    return 1_000_000 + secrets.randbelow(10**9)


def receipt_file(**changes: object) -> ReceiptFile:
    values: dict[str, object] = {
        "content_type": "application/pdf",
        "byte_size": 48_000,
        "sha256": secrets.token_hex(32),
    }
    return ReceiptFile(**(values | changes))  # type: ignore[arg-type]


def details(minor: int, **changes: object) -> ReceiptDetails:
    values: dict[str, object] = {
        "amount": ugx(minor),
        "sent_on": SENT_ON,
        "bank_name": "  Stanbic   Bank ",
        "transaction_number": "FT2632100042",
    }
    return ReceiptDetails(**(values | changes))  # type: ignore[arg-type]


async def submit(
    database: Database,
    clock: FakeClock,
    minor: int,
    *,
    actor: Actor | None = None,
    purpose_id: str | None = None,
    key: str | None = None,
    file: ReceiptFile | None = None,
    receipt: ReceiptDetails | None = None,
) -> bank.BankTransfer:
    actor = actor or client()
    async with database.actor_transaction(actor) as session:
        return await bank.submit_bank_receipt(
            session,
            actor,
            BankTransferRequest(
                Purpose.INVESTMENT,
                purpose_id or push_id(),
                file or receipt_file(),
                receipt or details(minor),
            ),
            idempotency_key=key or f"key-{push_id()}",
            clock=clock,
        )


async def statement(
    database: Database,
    clock: FakeClock,
    rows: tuple[tuple[date, int, int], ...],
    *,
    day: date = date(2026, 11, 27),
) -> str:
    """Import a bank statement for a pot of its own. Rows are (value date, in, out)."""
    pot = f"trust-{secrets.token_hex(4)}"
    lines = "".join(
        f"{value_date.isoformat()},Transfer {n},REF{n},{money_in or ''},{money_out or ''}\n"
        for n, (value_date, money_in, money_out) in enumerate(rows)
    )
    opening = 10**12
    closing = opening + sum(money_in - money_out for _, money_in, money_out in rows)
    parsed = parse_csv(
        "Date,Details,Reference,In,Out\n" + lines,
        FORMAT,
        source=StatementSource.BANK,
        account_reference=pot,
        statement_date=day,
        opening_balance=ugx(opening),
        closing_balance=ugx(closing),
    )
    importer = staff()
    async with database.actor_transaction(importer) as session:
        await statements.import_statement(session, importer, parsed, "0" * 64, clock)
    return pot


async def line_ids(database: Database, pot: str, minor: int) -> dict[tuple[date, bool], str]:
    """The pot's lines for this amount, by value date and whether money came in."""
    async with database.actor_transaction(staff()) as session:
        found = await statements.find_statement_lines(
            session,
            source=StatementSource.BANK,
            date_from=SENT_ON - timedelta(days=30),
            date_to=SENT_ON + timedelta(days=30),
            amount=ugx(minor),
        )
    return {
        (line.value_date, line.direction is Direction.IN): line.id
        for line in found
        if line.account_reference == pot
    }


async def review(
    database: Database,
    clock: FakeClock,
    transfer: bank.BankTransfer,
    outcome: bank.Review,
    *,
    reviewer: Actor | None = None,
    key: str | None = None,
) -> None:
    reviewer = reviewer or staff()
    async with database.actor_transaction(reviewer) as session:
        await bank.review_bank_transfer(
            session,
            reviewer,
            transfer.deposit.id,
            outcome,
            idempotency_key=key or f"review-{push_id()}",
            clock=clock,
        )


async def approve(
    database: Database,
    clock: FakeClock,
    transfer: bank.BankTransfer,
    *,
    approver: Actor | None = None,
    key: str | None = None,
) -> None:
    approver = approver or staff()
    async with database.actor_transaction(approver) as session:
        await bank.approve_bank_match(
            session,
            approver,
            transfer.deposit.id,
            idempotency_key=key or f"approve-{push_id()}",
            clock=clock,
        )


async def reload(database: Database, deposit: Deposit) -> Deposit:
    async with database.actor_transaction(SYSTEM) as session:
        return await service.get_deposit(session, deposit.id)


async def scalar(database: Database, sql: str, **parameters: object) -> object:
    async with database.actor_transaction(SYSTEM) as session:
        return await session.scalar(text(sql), parameters)


async def bank_balance(database: Database, pot: str) -> int:
    async with database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(
            session,
            AccountSpec(
                bank.BANK_ACCOUNT_CODE,
                AccountKind.ASSET,
                Currency.UGX,
                f"Bank account ({pot})",
                reference=pot,
            ),
            FakeClock(NOW),
        )
        return (await ledger.get_balance(session, account.id)).minor


def problem(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, str]:
    fields = error.value.fields
    return str(fields[0].field_path[-1]), fields[0].code
    return fields[0].field_path[-1], fields[0].code


# Submitting


async def test_a_receipt_saves_a_transfer_in_review_and_moves_no_money(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    minor = amount()
    transfer = await submit(app_database, fake_clock, minor)
    deposit = transfer.deposit
    assert deposit.status is S.IN_REVIEW
    assert deposit.method is Method.BANK_TRANSFER
    assert deposit.network is None and deposit.payer_msisdn is None
    assert deposit.amount == ugx(minor)
    assert transfer.receipt.storage_key == f"payments/receipts/{deposit.id}"
    assert transfer.receipt.bank_name == "Stanbic Bank"
    assert transfer.receipt.sent_on == SENT_ON
    assert deposit.ledger_entry_id is None
    assert (
        await scalar(
            worker_database,
            "SELECT count(*) FROM payment_deposit_events WHERE deposit_id = :id AND "
            "to_status = 'in_review' AND cause = 'request'",
            id=deposit.id,
        )
        == 1
    )


async def test_a_repeated_key_returns_the_same_transfer_and_other_details_are_refused(
    app_database: Database, fake_clock: FakeClock
) -> None:
    actor, purpose_id, file = client(), push_id(), receipt_file()
    minor = amount()
    first = await submit(
        app_database,
        fake_clock,
        minor,
        actor=actor,
        purpose_id=purpose_id,
        key="bank-key-0001",
        file=file,
    )
    again = await submit(
        app_database,
        fake_clock,
        minor,
        actor=actor,
        purpose_id=purpose_id,
        key="bank-key-0001",
        file=file,
    )
    assert again.deposit.id == first.deposit.id
    with pytest.raises(ApplicationError) as error:
        await submit(
            app_database,
            fake_clock,
            minor,
            actor=actor,
            purpose_id=purpose_id,
            key="bank-key-0001",
            file=file,
            receipt=details(minor, transaction_number="FT2632100043"),
        )
    assert error.value.code == "idempotency_key_reused"


@pytest.mark.parametrize(
    ("file", "receipt", "field", "code"),
    [
        (receipt_file(content_type="text/html"), None, "file", "unsupported_content_type"),
        (receipt_file(byte_size=MAX_RECEIPT_BYTES + 1), None, "file", "invalid_size"),
        (receipt_file(sha256="nope"), None, "file", "invalid_digest"),
        (None, details(1000, sent_on=date(2026, 11, 29)), "sent_on", "in_the_future"),
        (None, details(1000, sent_on=date(2026, 10, 20)), "sent_on", "too_old"),
        (None, details(1000, amount=ugx(0)), "amount", "invalid_amount"),
        (None, details(1000, bank_name=" "), "bank_name", "invalid_bank_name"),
        (
            None,
            details(1000, transaction_number="<b>"),
            "transaction_number",
            "invalid_transaction_number",
        ),
    ],
)
async def test_a_bad_receipt_saves_nothing(
    app_database: Database,
    worker_database: Database,
    fake_clock: FakeClock,
    file: ReceiptFile | None,
    receipt: ReceiptDetails | None,
    field: str,
    code: str,
) -> None:
    actor = client()
    with pytest.raises(ApplicationError) as error:
        await submit(app_database, fake_clock, 1000, actor=actor, file=file, receipt=receipt)
    assert problem(error) == (field, code)
    assert (
        await scalar(
            worker_database,
            "SELECT count(*) FROM payment_deposits WHERE client_id = :id",
            id=actor.id,
        )
        == 0
    )


async def test_only_a_client_submits_a_receipt(
    app_database: Database, fake_clock: FakeClock
) -> None:
    with pytest.raises(ApplicationError) as error:
        await submit(app_database, fake_clock, 1000, actor=staff())
    assert error.value.code == "forbidden"


async def test_an_investment_takes_one_live_payment_whichever_way_it_is_paid(
    app_database: Database, fake_clock: FakeClock
) -> None:
    actor, purpose_id = client(), push_id()
    transfer = await submit(app_database, fake_clock, 50_000, actor=actor, purpose_id=purpose_id)
    with pytest.raises(DepositExists) as by_phone:
        await start_deposit(app_database, fake_clock, actor=actor, purpose_id=purpose_id)
    assert by_phone.value.deposit_id == transfer.deposit.id
    assert by_phone.value.status is S.IN_REVIEW
    with pytest.raises(DepositExists):
        await submit(app_database, fake_clock, 50_000, actor=actor, purpose_id=purpose_id)

    other = push_id()
    deposit = await start_deposit(app_database, fake_clock, actor=actor, purpose_id=other)
    with pytest.raises(DepositExists) as by_bank:
        await submit(app_database, fake_clock, 50_000, actor=actor, purpose_id=other)
    assert by_bank.value.deposit_id == deposit.id


async def test_a_client_sees_only_their_own_transfers(
    app_database: Database, fake_clock: FakeClock
) -> None:
    transfer = await submit(app_database, fake_clock, 1000)
    stranger = client()
    async with app_database.actor_transaction(stranger) as session:
        with pytest.raises(ApplicationError) as error:
            await bank.get_bank_transfer(session, transfer.deposit.id)
        assert error.value.code == "not_found"
        seen = await session.scalar(text("SELECT count(*) FROM payment_bank_receipts"))
        assert seen == 0
    async with app_database.actor_transaction(staff()) as session:
        assert (await bank.get_bank_transfer(session, transfer.deposit.id)).receipt


# Matching


async def test_a_matched_transfer_is_confirmed_and_posted_to_the_bank_account(
    app_database: Database, worker_database: Database, worker: Worker, fake_clock: FakeClock
) -> None:
    minor = amount()
    transfer = await submit(app_database, fake_clock, minor)
    pot = await statement(app_database, fake_clock, ((date(2026, 11, 18), minor, 0),))
    reviewer = staff()
    async with app_database.actor_transaction(reviewer) as session:
        candidates = await bank.candidate_lines(session, reviewer, transfer.deposit.id)
    line = next(found for found in candidates if found.account_reference == pot)

    await review(app_database, fake_clock, transfer, Matched(line.id), reviewer=reviewer)
    # A match moves no money by itself. It waits for a second person.
    await drain(worker)
    assert (await reload(worker_database, transfer.deposit)).status is S.IN_REVIEW
    approver = staff()
    async with app_database.actor_transaction(approver) as session:
        seen = await bank.get_review(session, approver, transfer.deposit.id)
    assert seen is not None and seen.statement_line_id == line.id
    assert seen.reviewed_by == reviewer.id and seen.approved_by is None
    await approve(app_database, fake_clock, transfer, approver=approver)
    assert (await reload(worker_database, transfer.deposit)).status is S.IN_REVIEW
    await drain(worker)
    await drain(worker)

    confirmed = await reload(worker_database, transfer.deposit)
    assert confirmed.status is S.CONFIRMED
    assert confirmed.ledger_entry_id is not None
    assert await bank_balance(worker_database, pot) == minor
    assert (
        await scalar(
            worker_database,
            "SELECT count(*) FROM ledger_journal_entries WHERE source_type = 'payment.deposit' "
            "AND source_id = :id",
            id=confirmed.id,
        )
        == 1
    )
    assert (
        await scalar(
            worker_database,
            "SELECT cause FROM payment_deposit_events WHERE deposit_id = :id "
            "AND to_status = 'confirmed'",
            id=confirmed.id,
        )
        == "staff_review"
    )
    async with app_database.actor_transaction(staff()) as session:
        audited = await session.scalar(
            text(
                "SELECT count(*) FROM audit_events WHERE resource_id = :id "
                "AND action = 'payments.bank_transfer_reviewed' AND actor_id = :staff"
            ),
            {"id": confirmed.id, "staff": reviewer.id},
        )
    assert audited == 1
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await service.has_live_deposit(
            session, purpose=Purpose.INVESTMENT, purpose_id=confirmed.purpose_id
        )


async def test_only_lines_that_can_be_the_transfer_are_offered_or_accepted(
    app_database: Database, fake_clock: FakeClock
) -> None:
    minor = amount()
    transfer = await submit(app_database, fake_clock, minor)
    early, good, late = date(2026, 11, 16), date(2026, 11, 18), date(2026, 11, 25)
    pot = await statement(
        app_database,
        fake_clock,
        (
            (early, minor, 0),
            (good, minor, 0),
            (late, minor, 0),
            (good, 0, minor),
            (good, minor + 1, 0),
        ),
    )
    ids = await line_ids(app_database, pot, minor)
    reviewer = staff()
    async with app_database.actor_transaction(reviewer) as session:
        offered = {
            line.id
            for line in await bank.candidate_lines(session, reviewer, transfer.deposit.id)
            if line.account_reference == pot
        }
    assert offered == {ids[good, True]}
    for refused in (ids[early, True], ids[late, True], push_id()):
        with pytest.raises(ApplicationError) as error:
            await review(app_database, fake_clock, transfer, Matched(refused))
        assert problem(error) == ("statement_line_id", "line_not_found")
    with pytest.raises(ApplicationError) as error:
        await review(app_database, fake_clock, transfer, Matched(ids[good, False]))
    assert problem(error) == ("statement_line_id", "line_is_money_out")
    # A line for a different amount is not this transfer either.
    async with app_database.actor_transaction(staff()) as session:
        other_amount = await statements.find_statement_lines(
            session,
            source=StatementSource.BANK,
            date_from=good,
            date_to=good,
            amount=ugx(minor + 1),
        )
    wrong = next(line.id for line in other_amount if line.account_reference == pot)
    with pytest.raises(ApplicationError) as error:
        await review(app_database, fake_clock, transfer, Matched(wrong))
    assert problem(error) == ("statement_line_id", "line_not_found")


async def test_a_statement_line_confirms_one_transfer_only(
    app_database: Database, worker: Worker, worker_database: Database, fake_clock: FakeClock
) -> None:
    minor = amount()
    first = await submit(app_database, fake_clock, minor)
    second = await submit(app_database, fake_clock, minor)
    pot = await statement(app_database, fake_clock, ((date(2026, 11, 18), minor, 0),))
    line_id = (await line_ids(app_database, pot, minor))[date(2026, 11, 18), True]
    await review(app_database, fake_clock, first, Matched(line_id))
    with pytest.raises(ApplicationError) as error:
        await review(app_database, fake_clock, second, Matched(line_id))
    assert problem(error) == ("statement_line_id", "line_already_used")
    reviewer = staff()
    async with app_database.actor_transaction(reviewer) as session:
        assert line_id not in {
            line.id for line in await bank.candidate_lines(session, reviewer, second.deposit.id)
        }
    await drain(worker)
    assert (await reload(worker_database, second.deposit)).status is S.IN_REVIEW


async def test_two_matches_of_one_line_at_once_confirm_one_transfer(
    test_database: object,
    app_database: Database,
    fake_clock: FakeClock,
    worker: Worker,
    worker_database: Database,
) -> None:
    minor = amount()
    first = await submit(app_database, fake_clock, minor)
    second = await submit(app_database, fake_clock, minor)
    pot = await statement(app_database, fake_clock, ((date(2026, 11, 18), minor, 0),))
    line_id = (await line_ids(app_database, pot, minor))[date(2026, 11, 18), True]
    other = Database(test_database.settings("finvest_app"))  # type: ignore[attr-defined]
    try:
        results = await asyncio.gather(
            review(app_database, fake_clock, first, Matched(line_id)),
            review(other, fake_clock, second, Matched(line_id)),
            return_exceptions=True,
        )
    finally:
        await other.close()
    assert sum(result is None for result in results) == 1
    winner = first if results[0] is None else second
    await approve(app_database, fake_clock, winner)
    await drain(worker)
    statuses = {
        (await reload(worker_database, transfer.deposit)).status for transfer in (first, second)
    }
    assert statuses == {S.CONFIRMED, S.IN_REVIEW}
    assert await bank_balance(worker_database, pot) == minor


async def test_an_unmatched_transfer_fails_with_its_reason_and_frees_the_investment(
    app_database: Database, worker: Worker, worker_database: Database, fake_clock: FakeClock
) -> None:
    actor, purpose_id = client(), push_id()
    transfer = await submit(app_database, fake_clock, 50_000, actor=actor, purpose_id=purpose_id)
    await review(app_database, fake_clock, transfer, NotMatched(NotMatchedReason.NAME_DIFFERS))
    await drain(worker)
    failed = await reload(worker_database, transfer.deposit)
    assert failed.status is S.FAILED
    assert failed.code == "name_differs"
    assert failed.ledger_entry_id is None
    again = await submit(app_database, fake_clock, 50_000, actor=actor, purpose_id=purpose_id)
    assert again.deposit.id != transfer.deposit.id
    assert again.deposit.status is S.IN_REVIEW


async def test_a_transfer_is_reviewed_once_and_only_by_staff(
    app_database: Database, worker: Worker, fake_clock: FakeClock
) -> None:
    transfer = await submit(app_database, fake_clock, 50_000)
    reviewer = staff()
    await review(
        app_database,
        fake_clock,
        transfer,
        NotMatched(NotMatchedReason.UNREADABLE),
        reviewer=reviewer,
        key="review-key-0001",
    )
    # The same request again is answered from its receipt.
    await review(
        app_database,
        fake_clock,
        transfer,
        NotMatched(NotMatchedReason.UNREADABLE),
        reviewer=reviewer,
        key="review-key-0001",
    )
    with pytest.raises(ApplicationError) as error:
        await review(app_database, fake_clock, transfer, NotMatched(NotMatchedReason.OTHER))
    assert error.value.code == "conflict"
    await drain(worker)
    with pytest.raises(ApplicationError) as error:
        await review(app_database, fake_clock, transfer, NotMatched(NotMatchedReason.OTHER))
    assert error.value.code == "conflict"

    fresh = await submit(app_database, fake_clock, 50_000)
    with pytest.raises(ApplicationError) as error:
        await review(
            app_database, fake_clock, fresh, NotMatched(NotMatchedReason.OTHER), reviewer=client()
        )
    assert error.value.code == "forbidden"


async def test_a_mobile_money_deposit_cannot_be_reviewed_as_a_transfer(
    app_database: Database, fake_clock: FakeClock
) -> None:
    deposit = await start_deposit(app_database, fake_clock)
    reviewer = staff()
    async with app_database.actor_transaction(reviewer) as session:
        with pytest.raises(ApplicationError) as error:
            await bank.review_bank_transfer(
                session,
                reviewer,
                deposit.id,
                NotMatched(NotMatchedReason.OTHER),
                idempotency_key="review-key-0001",
                clock=fake_clock,
            )
    assert error.value.code == "not_found"


async def test_a_match_needs_a_second_person_and_is_approved_once(
    app_database: Database, worker: Worker, worker_database: Database, fake_clock: FakeClock
) -> None:
    minor = amount()
    transfer = await submit(app_database, fake_clock, minor)
    pot = await statement(app_database, fake_clock, ((date(2026, 11, 18), minor, 0),))
    line_id = (await line_ids(app_database, pot, minor))[date(2026, 11, 18), True]
    reviewer = staff()
    await review(app_database, fake_clock, transfer, Matched(line_id), reviewer=reviewer)
    with pytest.raises(ApplicationError) as error:
        await approve(app_database, fake_clock, transfer, approver=reviewer)
    assert error.value.code == "forbidden"
    with pytest.raises(ApplicationError) as error:
        await approve(app_database, fake_clock, transfer, approver=client())
    assert error.value.code == "forbidden"

    approver = staff()
    await approve(app_database, fake_clock, transfer, approver=approver, key="approve-key-0001")
    # The same request again is answered from its receipt; anyone else's is refused.
    await approve(app_database, fake_clock, transfer, approver=approver, key="approve-key-0001")
    with pytest.raises(ApplicationError) as error:
        await approve(app_database, fake_clock, transfer)
    assert error.value.code == "conflict"
    await drain(worker)
    assert (await reload(worker_database, transfer.deposit)).status is S.CONFIRMED
    with pytest.raises(ApplicationError) as error:
        await approve(app_database, fake_clock, transfer)
    assert error.value.code == "conflict"


async def test_only_a_match_waiting_for_approval_can_be_approved(
    app_database: Database, fake_clock: FakeClock
) -> None:
    unreviewed = await submit(app_database, fake_clock, 50_000)
    with pytest.raises(ApplicationError) as error:
        await approve(app_database, fake_clock, unreviewed)
    assert error.value.code == "conflict"
    refused = await submit(app_database, fake_clock, 50_000)
    await review(app_database, fake_clock, refused, NotMatched(NotMatchedReason.OTHER))
    with pytest.raises(ApplicationError) as error:
        await approve(app_database, fake_clock, refused)
    assert error.value.code == "conflict"


async def test_the_database_refuses_an_approval_from_the_person_who_matched(
    app_database: Database, fake_clock: FakeClock
) -> None:
    minor = amount()
    transfer = await submit(app_database, fake_clock, minor)
    pot = await statement(app_database, fake_clock, ((date(2026, 11, 18), minor, 0),))
    line_id = (await line_ids(app_database, pot, minor))[date(2026, 11, 18), True]
    reviewer = staff()
    await review(app_database, fake_clock, transfer, Matched(line_id), reviewer=reviewer)
    insert = text(
        "INSERT INTO payment_bank_approvals (deposit_id, approved_by, approved_at) "
        "VALUES (:id, :by, now())"
    )
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(reviewer) as session:
            await session.execute(insert, {"id": transfer.deposit.id, "by": reviewer.id})
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(staff()) as session:
            await session.execute(insert, {"id": transfer.deposit.id, "by": push_id()})


# What the database holds to, whatever the code does


async def test_the_worker_cannot_settle_a_transfer_other_than_as_reviewed(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    transfer = await submit(app_database, fake_clock, 50_000)
    move = text(
        "UPDATE payment_deposits SET status = :status, code = :code, settled_at = now(), "
        "ledger_entry_id = :entry WHERE id = :id"
    )
    for status, code, entry in (("confirmed", None, push_id()), ("failed", "other", None)):
        with pytest.raises(DBAPIError, match="settled only as its review says"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    move,
                    {"status": status, "code": code, "entry": entry, "id": transfer.deposit.id},
                )
    # A match nobody else approved cannot be confirmed either.
    minor = amount()
    matched = await submit(app_database, fake_clock, minor)
    pot = await statement(app_database, fake_clock, ((date(2026, 11, 18), minor, 0),))
    line_id = (await line_ids(app_database, pot, minor))[date(2026, 11, 18), True]
    await review(app_database, fake_clock, matched, Matched(line_id))
    with pytest.raises(DBAPIError, match="settled only as its review says"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                move,
                {"status": "confirmed", "code": None, "entry": push_id(), "id": matched.deposit.id},
            )

    await review(app_database, fake_clock, transfer, NotMatched(NotMatchedReason.UNREADABLE))
    with pytest.raises(DBAPIError, match="settled only as its review says"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                move,
                {"status": "failed", "code": "other", "entry": None, "id": transfer.deposit.id},
            )
    with pytest.raises(DBAPIError, match="settled only as its review says"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                move,
                {
                    "status": "confirmed",
                    "code": None,
                    "entry": push_id(),
                    "id": transfer.deposit.id,
                },
            )


RAW_DEPOSIT = text(
    "INSERT INTO payment_deposits (id, client_id, purpose_type, purpose_id, method, provider, "
    "currency, amount_minor, payer_msisdn, status, created_at, updated_at) VALUES (:id, "
    ":client_id, 'investment', :purpose_id, :method, :provider, 'UGX', 1000, :msisdn, :status, "
    "now(), now())"
)


@pytest.mark.parametrize(
    ("method", "provider", "msisdn", "status"),
    [
        ("bank_transfer", "bank", None, "created"),  # a transfer starts in review
        ("mtn", "fake", "+256772000000", "in_review"),  # mobile money is never in review
        ("bank_transfer", "bank", "+256772000000", "in_review"),  # a transfer has no payer number
        ("mtn", "fake", None, "created"),  # mobile money always has one
        ("bank_transfer", "fake", None, "in_review"),  # a transfer is the bank's
    ],
)
async def test_the_database_refuses_a_deposit_on_the_wrong_path(
    app_database: Database, method: str, provider: str, msisdn: str | None, status: str
) -> None:
    actor = client()
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(actor) as session:
            await session.execute(
                RAW_DEPOSIT,
                {
                    "id": push_id(),
                    "client_id": actor.id,
                    "purpose_id": push_id(),
                    "method": method,
                    "provider": provider,
                    "msisdn": msisdn,
                    "status": status,
                },
            )


async def test_receipts_and_reviews_are_kept_as_written_and_added_only_by_the_right_people(
    app_database: Database,
    worker_database: Database,
    migrator_database: Database,
    fake_clock: FakeClock,
) -> None:
    owner = client()
    transfer = await submit(app_database, fake_clock, 50_000, actor=owner)
    deposit_id = transfer.deposit.id
    await review(app_database, fake_clock, transfer, NotMatched(NotMatchedReason.OTHER))
    changes = (
        "UPDATE payment_bank_receipts SET bank_name = 'Other' WHERE deposit_id = :id",
        "DELETE FROM payment_bank_receipts WHERE deposit_id = :id",
        "UPDATE payment_bank_reviews SET reason = 'unreadable' WHERE deposit_id = :id",
        "DELETE FROM payment_bank_reviews WHERE deposit_id = :id",
    )
    for change in changes:
        for actor in (owner, staff()):
            with pytest.raises(DBAPIError, match="permission denied"):
                async with app_database.actor_transaction(actor) as session:
                    await session.execute(text(change), {"id": deposit_id})
        with pytest.raises(DBAPIError, match="permission denied"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(text(change), {"id": deposit_id})
        with pytest.raises(DBAPIError, match="does not allow this"):
            async with migrator_database.transaction() as session:
                await session.execute(text(change), {"id": deposit_id})
    for table in ("payment_bank_receipts", "payment_bank_reviews", "payment_bank_approvals"):
        with pytest.raises(DBAPIError, match="does not allow this"):
            async with migrator_database.transaction() as session:
                await session.execute(text(f"TRUNCATE {table} CASCADE"))

    # A client cannot review, and staff cannot review in someone else's name.
    fresh = await submit(app_database, fake_clock, 50_000, actor=owner)
    insert_review = text(
        "INSERT INTO payment_bank_reviews (deposit_id, outcome, reason, reviewed_by, "
        "reviewed_at) VALUES (:id, 'not_matched', 'other', :by, now())"
    )
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(owner) as session:
            await session.execute(insert_review, {"id": fresh.deposit.id, "by": owner.id})
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(staff()) as session:
            await session.execute(insert_review, {"id": fresh.deposit.id, "by": push_id()})
    # A client cannot add a receipt to someone else's transfer.
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(client()) as session:
            await session.execute(
                text(
                    "INSERT INTO payment_bank_receipts (deposit_id, client_id, storage_key, "
                    "content_type, byte_size, sha256, sent_on, bank_name, transaction_number, "
                    "created_at) VALUES (:id, finvest_actor_id(), 'payments/receipts/' || :id, "
                    "'image/png', 10, repeat('a', 64), DATE '2026-11-17', 'Bank', 'FT1', now())"
                ),
                {"id": fresh.deposit.id},
            )
