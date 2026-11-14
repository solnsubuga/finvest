"""Staff pay bank payouts from the trust account in batches, and settle each from the trust
statement, two people at every step.

The trust account's statements run one day after another, and a settlement needs statements on
or after the day its batch was approved, which is today. So this file has a database of its own
and imports trust statements for today and the days after, as if those days had ended.
"""

import asyncio
import os
import secrets
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.audit import service as audit
from app.modules.payouts import bank_payouts, payouts
from app.modules.payouts.bank_payouts import BankPayoutRefused, BatchStatus, Decision
from app.modules.payouts.domain.payouts import PayoutEvent, PayoutStatus
from app.modules.payouts.payouts import PayoutView
from app.modules.reconciliation import statements
from app.modules.reconciliation.domain.statements import CsvFormat, StatementSource, parse_csv
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support import database as harness
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.ledger import key, staff, ugx
from tests.support.payouts import World

pytestmark = pytest.mark.integration

AMOUNT = 2_500_000
FORMAT = CsvFormat(
    value_date="Date",
    date_format="%Y-%m-%d",
    narrative="Details",
    reference="Reference",
    money_in="In",
    money_out="Out",
)


@pytest.fixture(scope="module")
def test_database() -> Iterator[TestDatabase]:
    """A database of this file's own, so its trust statements can start today."""
    database = harness.create(f"finvest_test_bank_{os.getpid()}")
    try:
        result = harness.migrate(database)
        if result.returncode != 0:
            pytest.fail(f"migrations failed:\n{result.stdout}\n{result.stderr}")
        harness.allow_test_entries_for_staff(database)
        yield database
    finally:
        harness.drop(database)


def now() -> FakeClock:
    """The real time. The database stamps every decision against its own clock."""
    return FakeClock(datetime.now(UTC))


@pytest.fixture
async def world(app_database: Database, worker_database: Database) -> World:
    return await World(app_database, worker_database, now()).build(AMOUNT * 4, mobile=False)


async def queued(world: World, amount: int = AMOUNT) -> PayoutView:
    payout = await world.request(amount)
    assert payout.status is PayoutStatus.QUEUED
    return payout


async def prepare(
    database: Database, payout_ids: list[str], *, by: Actor | None = None, **options: object
) -> bank_payouts.BatchView:
    maker = by or staff()
    async with database.actor_transaction(maker) as session:
        return await bank_payouts.prepare_batch(
            session,
            maker,
            currency=Currency.UGX,
            payout_ids=payout_ids,
            reason="Today's bank payouts",
            idempotency_key=str(options.pop("idempotency_key", key())),
            clock=now(),
        )


async def approve(database: Database, batch_id: str, *, by: Actor | None = None) -> Actor:
    checker = by or staff()
    async with database.actor_transaction(checker) as session:
        await bank_payouts.approve_batch(
            session, checker, batch_id, reason="Checked", idempotency_key=key(), clock=now()
        )
    return checker


async def status_of(database: Database, payout_id: str) -> PayoutStatus:
    async with database.actor_transaction(staff()) as session:
        return (await payouts.get_payout(session, payout_id)).status


async def refused(coro: object) -> str:
    with pytest.raises(BankPayoutRefused) as refusal:
        await coro  # type: ignore[misc]
    return refusal.value.code


class Trust:
    """The trust account's statements in this file's database, one day after another."""

    day: date | None = None
    closing = 50_000_000_000

    @classmethod
    async def statement(
        cls, database: Database, rows: tuple[tuple[str, int], ...] = ()
    ) -> tuple[str, date, dict[str, str]]:
        """The next day's statement, with lines going out as (reference, amount). Returns its
        ID, its day and the line IDs by reference."""
        day = business_date(datetime.now(UTC)) if cls.day is None else cls.day + timedelta(1)
        lines = "".join(
            f"{day.isoformat()},Bank transfer {n},{reference},,{amount}\n"
            for n, (reference, amount) in enumerate(rows)
        )
        closing = cls.closing - sum(amount for _, amount in rows)
        parsed = parse_csv(
            "Date,Details,Reference,In,Out\n" + lines,
            FORMAT,
            source=StatementSource.BANK,
            account_reference="trust",
            statement_date=day,
            opening_balance=ugx(cls.closing),
            closing_balance=ugx(closing),
        )
        # As if the day had ended.
        later = FakeClock(datetime.now(UTC) + timedelta(days=(day - date.today()).days + 2))
        importer = staff()
        async with database.actor_transaction(importer) as session:
            imported = await statements.import_statement(
                session, importer, parsed, secrets.token_hex(32), later
            )
            found = await statements.find_statement_lines(
                session, source=StatementSource.BANK, date_from=day, date_to=day
            )
        cls.day, cls.closing = day, closing
        references = dict(rows)
        return (
            imported.id,
            day,
            {line.reference: line.id for line in found if line.reference in references},
        )


async def test_two_people_send_a_batch_and_the_file_can_then_be_downloaded(
    world: World, app_database: Database
) -> None:
    first, second = await queued(world), await queued(world, 1_000_000)
    async with app_database.actor_transaction(staff()) as session:
        waiting = [each.id for each in await bank_payouts.waiting_payouts(session, Currency.UGX)]
    assert {first.id, second.id} <= set(waiting)

    maker = staff()
    batch = await prepare(app_database, [first.id, second.id], by=maker)
    assert batch.status is BatchStatus.PREPARED
    assert batch.total == ugx(AMOUNT + 1_000_000)
    assert await status_of(app_database, first.id) is PayoutStatus.QUEUED
    # The file is only for an approved batch.
    async with app_database.actor_transaction(maker) as session:
        assert (
            await refused(bank_payouts.transfer_file(session, maker, batch.id, now()))
            == "batch_not_approved"
        )
    # In a prepared batch, a payout is no longer waiting for one.
    async with app_database.actor_transaction(staff()) as session:
        waiting = [each.id for each in await bank_payouts.waiting_payouts(session, Currency.UGX)]
    assert first.id not in waiting

    async with app_database.actor_transaction(maker) as session:
        assert (
            await refused(
                bank_payouts.approve_batch(
                    session, maker, batch.id, reason="Mine", idempotency_key=key(), clock=now()
                )
            )
            == "prepared_by_you"
        )
    checker = await approve(app_database, batch.id)
    assert await status_of(app_database, first.id) is PayoutStatus.SENT
    assert await status_of(app_database, second.id) is PayoutStatus.SENT

    async with app_database.actor_transaction(checker) as session:
        approved = await bank_payouts.get_batch(session, batch.id)
        content = await bank_payouts.transfer_file(session, checker, batch.id, now())
        downloads = await audit.events_for_resource(session, "payout_bank_batch", batch.id)
    assert approved.status is BatchStatus.APPROVED
    rows = content.strip().split("\r\n")
    assert rows[0] == ",".join(bank_payouts.FILE_COLUMNS)
    assert len(rows) == 3
    assert rows[1].startswith(f"{first.id},Sarah Nakato,Stanbic Bank,")
    assert f",{AMOUNT},UGX," in rows[1]
    assert "payouts.bank_batch_file_downloaded" in [event.action for event in downloads]
    async with app_database.actor_transaction(checker) as session:
        assert (
            await refused(
                bank_payouts.approve_batch(
                    session, checker, batch.id, reason="Again", idempotency_key=key(), clock=now()
                )
            )
            == "batch_already_decided"
        )


async def test_a_repeated_prepare_is_the_same_batch(world: World, app_database: Database) -> None:
    payout = await queued(world)
    maker, same = staff(), key()
    first = await prepare(app_database, [payout.id], by=maker, idempotency_key=same)
    again = await prepare(app_database, [payout.id], by=maker, idempotency_key=same)
    assert again.id == first.id


async def test_a_payout_is_in_one_batch_at_a_time_until_one_is_declined_or_withdrawn(
    world: World, app_database: Database
) -> None:
    payout = await queued(world)
    maker = staff()
    batch = await prepare(app_database, [payout.id], by=maker)
    assert await refused(prepare(app_database, [payout.id])) == "payout_in_another_batch"

    async with app_database.actor_transaction(maker) as session:
        withdrawn = await bank_payouts.withdraw_batch(
            session, maker, batch.id, reason="Wrong day", clock=now()
        )
    assert withdrawn.status is BatchStatus.WITHDRAWN
    second = await prepare(app_database, [payout.id], by=maker)
    checker = staff()
    async with app_database.actor_transaction(checker) as session:
        declined = await bank_payouts.decline_batch(
            session, checker, second.id, reason="Not today", idempotency_key=key(), clock=now()
        )
    assert declined.status is BatchStatus.DECLINED
    assert await status_of(app_database, payout.id) is PayoutStatus.QUEUED
    third = await prepare(app_database, [payout.id])
    assert third.payout_ids == (payout.id,)


async def test_only_queued_bank_payouts_go_in_a_batch(
    world: World, app_database: Database, worker_database: Database
) -> None:
    assert await refused(prepare(app_database, [push_id()])) == "payout_not_bank"
    mobile = await World(app_database, worker_database, now()).build(AMOUNT)
    phone_payout = await mobile.request(1_000_000)
    assert await refused(prepare(app_database, [phone_payout.id])) == "payout_not_bank"
    sent = await queued(world)
    await approve(app_database, (await prepare(app_database, [sent.id])).id)
    assert await refused(prepare(app_database, [sent.id])) == "payout_not_queued"
    assert await refused(prepare(app_database, [sent.id, sent.id])) == "batch_payouts_invalid"


async def test_the_database_refuses_a_bank_payout_sent_or_settled_without_two_people(
    world: World, app_database: Database, worker_database: Database
) -> None:
    payout = await queued(world)
    # The worker never moves a bank payout.
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE payouts SET status = 'sent' WHERE id = :id"), {"id": payout.id}
            )
    # Nor does a member of staff without an approved batch.
    loner = staff()
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(loner) as session:
            await session.execute(
                text("UPDATE payouts SET status = 'sent' WHERE id = :id"), {"id": payout.id}
            )
    # Nor does approving one's own batch, written straight to the table.
    maker = staff()
    batch = await prepare(app_database, [payout.id], by=maker)
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE payout_bank_batches SET status = 'approved', decided_by = :me, "
                    "decided_at = now(), decision_reason = 'x' WHERE id = :id"
                ),
                {"me": maker.id, "id": batch.id},
            )
    # An approval that does not send its payouts cannot commit.
    checker = staff()
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(checker) as session:
            await session.execute(
                text(
                    "UPDATE payout_bank_batches SET status = 'approved', decided_by = :me, "
                    "decided_at = now(), decision_reason = 'x' WHERE id = :id"
                ),
                {"me": checker.id, "id": batch.id},
            )
    # Items are added only as their batch is prepared.
    other = await queued(world, 1_000_000)
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text("INSERT INTO payout_bank_batch_items (batch_id, payout_id) VALUES (:b, :p)"),
                {"b": batch.id, "p": other.id},
            )
    # Paid, from sent, needs an approved settlement too.
    await approve(app_database, batch.id)
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await payouts.apply_event(
                session, SYSTEM, payout.id, PayoutEvent.STATUS_PAID, world.clock
            )
    async with app_database.actor_transaction(checker) as session:
        with pytest.raises(ApplicationError):
            await payouts.apply_event(
                session, checker, payout.id, PayoutEvent.BANK_SETTLED_PAID, world.clock
            )
    assert await status_of(app_database, payout.id) is PayoutStatus.SENT


async def test_two_people_settle_a_bank_payout_from_its_statement_line(
    world: World, app_database: Database
) -> None:
    payout = await queued(world)
    owed = await world.owed(payout)
    pot = await world.pot_change()
    await approve(app_database, (await prepare(app_database, [payout.id])).id)
    statement_id, _, lines = await Trust.statement(app_database, ((payout.id, AMOUNT),))

    proposer = staff()
    async with app_database.actor_transaction(proposer) as session:
        sent = [each.payout.id for each in await bank_payouts.sent_payouts(session)]
        assert payout.id in sent
        settlement = await bank_payouts.propose_settlement(
            session,
            proposer,
            payout.id,
            outcome="paid",
            statement_line_id=lines[payout.id],
            statement_id=None,
            reason="On the trust statement",
            idempotency_key=key(),
            clock=now(),
        )
    assert settlement.status is Decision.PROPOSED
    async with app_database.actor_transaction(proposer) as session:
        assert (
            await refused(
                bank_payouts.approve_settlement(
                    session, proposer, settlement.id, reason="Mine", idempotency_key=key(),
                    clock=now(),
                )
            )
            == "proposed_by_you"
        )  # fmt: skip
    approver = staff()
    async with app_database.actor_transaction(approver) as session:
        moved = await bank_payouts.approve_settlement(
            session, approver, settlement.id, reason="Agree", idempotency_key=key(), clock=now()
        )
    assert moved.status is PayoutStatus.PAID
    # The money left the trust account and the client is owed that much less.
    assert await world.owed(payout) == owed - AMOUNT
    assert await world.pot_change() == pot - AMOUNT
    await world.no_drift()
    assert statement_id


async def test_a_statement_line_that_is_not_the_payout_is_refused(
    world: World, app_database: Database
) -> None:
    payout = await queued(world)
    await approve(app_database, (await prepare(app_database, [payout.id])).id)
    _, _, lines = await Trust.statement(app_database, (("someone-else", AMOUNT - 1),))
    proposer = staff()
    async with app_database.actor_transaction(proposer) as session:
        code = await refused(
            bank_payouts.propose_settlement(
                session,
                proposer,
                payout.id,
                outcome="paid",
                statement_line_id=lines["someone-else"],
                statement_id=None,
                reason="x",
                idempotency_key=key(),
                clock=now(),
            )
        )
    assert code == "statement_line_not_this_payout"


async def test_failed_needs_every_day_imported_and_no_line_it_could_be(
    world: World, app_database: Database
) -> None:
    payout = await queued(world, 1_234_567)
    await approve(app_database, (await prepare(app_database, [payout.id])).id)
    # A statement on the day of approval is too soon to say the money never left.
    first_id, first_day, _ = await Trust.statement(app_database)
    proposer = staff()

    async def propose_failed(statement_id: str) -> str:
        async with app_database.actor_transaction(proposer) as session:
            try:
                await bank_payouts.propose_settlement(
                    session,
                    proposer,
                    payout.id,
                    outcome="failed",
                    statement_line_id=None,
                    statement_id=statement_id,
                    reason="The bank refused the account",
                    idempotency_key=key(),
                    clock=now(),
                )
            except BankPayoutRefused as refusal:
                return refusal.code
        return "proposed"

    if first_day <= business_date(datetime.now(UTC)):
        assert await propose_failed(first_id) == "statement_not_enough"
    # A line going out for the payout's amount that nothing accounts for could be this payout.
    await Trust.statement(app_database, (("unexplained", 1_234_567),))
    later_id, _, _ = await Trust.statement(app_database)
    assert await propose_failed(later_id) == "statement_not_enough"


async def test_two_people_settle_a_refused_bank_payout_as_failed(
    world: World, app_database: Database, worker_database: Database
) -> None:
    amount = 777_001
    payout = await queued(world, amount)
    await approve(app_database, (await prepare(app_database, [payout.id])).id)
    owed = await world.owed(payout)
    statement_id = ""
    for _ in range(3):
        statement_id, _, _ = await Trust.statement(app_database)
    proposer = staff()
    async with app_database.actor_transaction(proposer) as session:
        settlement = await bank_payouts.propose_settlement(
            session,
            proposer,
            payout.id,
            outcome="failed",
            statement_line_id=None,
            statement_id=statement_id,
            reason="The bank refused the account",
            idempotency_key=key(),
            clock=now(),
        )
    approver = staff()
    async with app_database.actor_transaction(approver) as session:
        moved = await bank_payouts.approve_settlement(
            session, approver, settlement.id, reason="Agree", idempotency_key=key(), clock=now()
        )
    assert (moved.status, moved.failure_code) == (PayoutStatus.FAILED, "bank_refused")
    # Nothing left: the money waits in the client's pending account, still owed.
    assert await world.owed(payout) == owed
    assert await world.pending() == amount
    async with worker_database.actor_transaction(SYSTEM) as session:
        told = await session.execute(
            text(
                "SELECT event FROM notifications WHERE client_id = :client AND link_id = :id "
                "ORDER BY created_at, event"
            ),
            {"client": world.client_id, "id": payout.id},
        )
        events = list(told.scalars())
    assert events == ["payout_processing", "payout_failed"]


async def test_two_batches_cannot_take_the_same_payout_at_once(
    world: World, app_database: Database
) -> None:
    payout = await queued(world)
    results = await asyncio.gather(
        prepare(app_database, [payout.id]),
        prepare(app_database, [payout.id]),
        return_exceptions=True,
    )
    made = [each for each in results if isinstance(each, bank_payouts.BatchView)]
    failed = [each for each in results if not isinstance(each, bank_payouts.BatchView)]
    assert len(made) == 1
    assert len(failed) == 1
    assert isinstance(failed[0], BankPayoutRefused | DBAPIError)


async def test_a_batch_can_take_a_payout_beyond_the_first_page_waiting(
    app_database: Database, worker_database: Database
) -> None:
    # More are waiting than one page of the list shows; the newest still goes in a batch.
    many = bank_payouts.MAX_BATCH + 1
    world = await World(app_database, worker_database, now()).build(many * 1_000, mobile=False)
    newest = [await queued(world, 1_000) for _ in range(many)][-1]
    async with app_database.actor_transaction(staff()) as session:
        page = await bank_payouts.waiting_payouts(session, Currency.UGX)
    assert newest.id not in {each.id for each in page}
    batch = await prepare(app_database, [newest.id])
    assert batch.payout_ids == (newest.id,)
