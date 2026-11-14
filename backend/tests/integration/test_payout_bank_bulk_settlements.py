"""Two people settle many bank payouts of one batch from a single bulk debit on the trust
statement.

As in test_payout_bank_batches.py, the trust statements run one day after another from today,
so this file has a database of its own.
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
from app.modules.payouts import bank_payouts, bulk_settlements, payouts
from app.modules.payouts.bank_payouts import BankPayoutRefused, Decision
from app.modules.payouts.domain.payouts import PayoutStatus
from app.modules.payouts.payouts import PayoutView
from app.modules.reconciliation import statements
from app.modules.reconciliation.domain.statements import CsvFormat, StatementSource, parse_csv
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support import database as harness
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.ledger import key, staff, ugx
from tests.support.payouts import World

pytestmark = pytest.mark.integration

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
    database = harness.create(f"finvest_test_bulk_{os.getpid()}")
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
    return await World(app_database, worker_database, now()).build(50_000_000, mobile=False)


async def sent_batch(
    world: World, database: Database, amounts: tuple[int, ...]
) -> tuple[str, list[PayoutView]]:
    """A batch of bank payouts for these amounts, prepared by one person and sent by another."""
    made = [await world.request(amount) for amount in amounts]
    maker, checker = staff(), staff()
    async with database.actor_transaction(maker) as session:
        batch = await bank_payouts.prepare_batch(
            session,
            maker,
            currency=Currency.UGX,
            payout_ids=[each.id for each in made],
            reason="Today's bank payouts",
            idempotency_key=key(),
            clock=now(),
        )
    async with database.actor_transaction(checker) as session:
        await bank_payouts.approve_batch(
            session, checker, batch.id, reason="Checked", idempotency_key=key(), clock=now()
        )
    return batch.id, made


class Trust:
    """The trust account's statements in this file's database, one day after another."""

    day: date | None = None
    closing = 50_000_000_000

    @classmethod
    async def statement(
        cls, database: Database, rows: tuple[tuple[str, int], ...] = ()
    ) -> tuple[str, dict[str, str]]:
        """The next day's statement, with lines going out as (reference, amount). Returns its
        ID and the line IDs by reference."""
        day = business_date(datetime.now(UTC)) if cls.day is None else cls.day + timedelta(1)
        lines = "".join(
            f"{day.isoformat()},Bulk transfer {n},{reference},,{amount}\n"
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
        return imported.id, {
            line.reference: line.id for line in found if line.reference in references
        }


async def propose(
    database: Database,
    batch_id: str,
    line_id: str,
    payout_ids: list[str],
    *,
    by: Actor | None = None,
    idempotency_key: str | None = None,
) -> bulk_settlements.BulkView:
    proposer = by or staff()
    async with database.actor_transaction(proposer) as session:
        return await bulk_settlements.propose(
            session,
            proposer,
            batch_id,
            statement_line_id=line_id,
            payout_ids=payout_ids,
            reason="One debit for the whole file",
            idempotency_key=idempotency_key or key(),
            clock=now(),
        )


async def status_of(database: Database, payout_id: str) -> PayoutStatus:
    async with database.actor_transaction(staff()) as session:
        return (await payouts.get_payout(session, payout_id)).status


async def refused(coro: object) -> str:
    with pytest.raises(BankPayoutRefused) as refusal:
        await coro  # type: ignore[misc]
    return refusal.value.code


async def test_two_people_settle_a_whole_batch_from_one_bulk_debit(
    world: World, app_database: Database, worker_database: Database
) -> None:
    amounts = (2_000_000, 1_500_000, 700_000)
    batch_id, made = await sent_batch(world, app_database, amounts)
    owed = [await world.owed(each) for each in made]
    pot = await world.pot_change()
    _, lines = await Trust.statement(app_database, (("BULK-1", sum(amounts)),))

    # The first person sees the batch's payouts to settle and the debit that could be theirs.
    async with app_database.actor_transaction(staff()) as session:
        debit = await bulk_settlements.bulk_debit(session, batch_id)
    assert [each.id for each in debit.payouts] == [each.id for each in made]
    assert lines["BULK-1"] in [line.id for line in debit.lines]

    proposer = staff()
    bulk = await propose(app_database, batch_id, lines["BULK-1"], [m.id for m in made], by=proposer)
    assert bulk.status is Decision.PROPOSED
    assert bulk.total == ugx(sum(amounts))
    assert set(bulk.payout_ids) == {each.id for each in made}
    # The second person sees the line itself, not only its ID.
    assert bulk.line is not None
    assert (bulk.line.id, bulk.line.reference, bulk.line.amount) == (
        lines["BULK-1"],
        "BULK-1",
        ugx(sum(amounts)),
    )
    # Each payout now shows an open settlement, which cannot be decided on its own.
    async with app_database.actor_transaction(staff()) as session:
        sent = {each.payout.id: each for each in await bank_payouts.sent_payouts(session)}
        open_one = sent[made[0].id].settlement
        assert open_one is not None and open_one.bulk_id == bulk.id
        assert (
            await refused(
                bank_payouts.approve_settlement(
                    session, staff(), open_one.id, reason="x", idempotency_key=key(),
                    clock=now(),
                )
            )
            == "settlement_in_bulk"
        )  # fmt: skip
        assert lines["BULK-1"] not in [
            line.id for line in (await bulk_settlements.bulk_debit(session, batch_id)).lines
        ]

    async with app_database.actor_transaction(proposer) as session:
        assert (
            await refused(
                bulk_settlements.approve(
                    session, proposer, bulk.id, reason="Mine", idempotency_key=key(), clock=now()
                )
            )
            == "proposed_by_you"
        )
    approver = staff()
    async with app_database.actor_transaction(approver) as session:
        approved = await bulk_settlements.approve(
            session, approver, bulk.id, reason="Matches the debit", idempotency_key=key(),
            clock=now(),
        )  # fmt: skip
    assert approved.status is Decision.APPROVED
    for each, before in zip(made, owed, strict=True):
        assert await status_of(app_database, each.id) is PayoutStatus.PAID
        assert await world.owed(each) == before - each.amount.minor
    assert await world.pot_change() == pot - sum(amounts)
    await world.no_drift()
    async with worker_database.actor_transaction(SYSTEM) as session:
        told = await session.execute(
            text(
                "SELECT count(*) FROM notifications WHERE client_id = :client "
                "AND event = 'payout_sent' AND link_id = ANY(:ids)"
            ),
            {"client": world.client_id, "ids": [each.id for each in made]},
        )
    assert told.scalar_one() == len(made)


async def test_a_bulk_can_leave_out_a_payout_the_bank_did_not_pay(
    world: World, app_database: Database
) -> None:
    batch_id, made = await sent_batch(world, app_database, (1_100_000, 900_000))
    _, lines = await Trust.statement(app_database, (("BULK-2", 1_100_000),))
    bulk = await propose(app_database, batch_id, lines["BULK-2"], [made[0].id])
    async with app_database.actor_transaction(approver := staff()) as session:
        await bulk_settlements.approve(
            session, approver, bulk.id, reason="Agree", idempotency_key=key(), clock=now()
        )
    assert await status_of(app_database, made[0].id) is PayoutStatus.PAID
    # The other stays sent, to be settled on its own.
    assert await status_of(app_database, made[1].id) is PayoutStatus.SENT
    async with app_database.actor_transaction(staff()) as session:
        debit = await bulk_settlements.bulk_debit(session, batch_id)
    assert [each.id for each in debit.payouts] == [made[1].id]


async def test_the_payouts_must_add_up_to_the_line_and_belong_to_the_batch(
    world: World, app_database: Database
) -> None:
    batch_id, made = await sent_batch(world, app_database, (1_000_000, 2_000_000))
    other_batch, others = await sent_batch(world, app_database, (500_000,))
    _, lines = await Trust.statement(
        app_database, (("BULK-3", 3_000_000), ("BULK-3-IN", 1), ("BULK-3-SHORT", 2_999_999))
    )
    ids = [each.id for each in made]
    assert await refused(propose(app_database, batch_id, lines["BULK-3-SHORT"], ids)) == (
        "amounts_do_not_add_up"
    )
    assert await refused(propose(app_database, batch_id, push_id(), ids)) == (
        "statement_line_not_this_batch"
    )
    assert await refused(
        propose(app_database, batch_id, lines["BULK-3"], [*ids, others[0].id])
    ) == ("payout_not_in_batch")
    assert await refused(propose(app_database, batch_id, lines["BULK-3"], [ids[0], ids[0]])) == (
        "bulk_payouts_invalid"
    )
    assert other_batch


async def test_a_line_is_named_once_and_a_declined_bulk_frees_it(
    world: World, app_database: Database
) -> None:
    batch_id, made = await sent_batch(world, app_database, (1_250_000,))
    _, lines = await Trust.statement(app_database, (("BULK-4", 1_250_000),))
    ids = [each.id for each in made]
    proposer = staff()
    bulk = await propose(app_database, batch_id, lines["BULK-4"], ids, by=proposer)
    # The same line for the same payout on its own is refused while the bulk is open.
    async with app_database.actor_transaction(staff()) as session:
        assert (
            await refused(
                bank_payouts.propose_settlement(
                    session, staff(), ids[0], outcome="paid", statement_line_id=lines["BULK-4"],
                    statement_id=None, reason="x", idempotency_key=key(), clock=now(),
                )
            )
            == "settlement_already_proposed"
        )  # fmt: skip
    checker = staff()
    async with app_database.actor_transaction(checker) as session:
        declined = await bulk_settlements.decline(
            session, checker, bulk.id, reason="Wrong line", clock=now()
        )
    assert declined.status is Decision.DECLINED
    assert await status_of(app_database, ids[0]) is PayoutStatus.SENT
    # Declined, the line and the payout are free again.
    again = await propose(app_database, batch_id, lines["BULK-4"], ids, by=proposer)
    async with app_database.actor_transaction(proposer) as session:
        withdrawn = await bulk_settlements.withdraw(
            session, proposer, again.id, reason="Let me check", clock=now()
        )
    assert withdrawn.status is Decision.WITHDRAWN


async def test_a_repeated_proposal_is_the_same_bulk(world: World, app_database: Database) -> None:
    batch_id, made = await sent_batch(world, app_database, (321_000,))
    _, lines = await Trust.statement(app_database, (("BULK-5", 321_000),))
    proposer, same = staff(), key()
    first = await propose(
        app_database, batch_id, lines["BULK-5"], [made[0].id], by=proposer, idempotency_key=same
    )
    again = await propose(
        app_database, batch_id, lines["BULK-5"], [made[0].id], by=proposer, idempotency_key=same
    )
    assert again.id == first.id


async def test_two_people_cannot_claim_the_same_debit_at_once(
    world: World, app_database: Database
) -> None:
    # Two batches, each with one payout of the same amount, and one line that could be either.
    first_batch, first = await sent_batch(world, app_database, (654_321,))
    second_batch, second = await sent_batch(world, app_database, (654_321,))
    _, lines = await Trust.statement(app_database, (("BULK-6", 654_321),))
    line = lines["BULK-6"]

    async def single() -> object:
        someone = staff()
        async with app_database.actor_transaction(someone) as session:
            return await bank_payouts.propose_settlement(
                session, someone, second[0].id, outcome="paid", statement_line_id=line,
                statement_id=None, reason="x", idempotency_key=key(), clock=now(),
            )  # fmt: skip

    results = await asyncio.gather(
        propose(app_database, first_batch, line, [first[0].id]),
        propose(app_database, first_batch, line, [first[0].id]),
        single(),
        return_exceptions=True,
    )
    made = [each for each in results if not isinstance(each, BaseException)]
    failed = [each for each in results if isinstance(each, BaseException)]
    assert len(made) == 1, results
    assert all(isinstance(each, BankPayoutRefused | DBAPIError) for each in failed), failed
    assert second_batch


async def test_the_database_refuses_a_bulk_decided_apart_or_not_adding_up(
    world: World, app_database: Database
) -> None:
    batch_id, made = await sent_batch(world, app_database, (400_000, 600_000))
    _, lines = await Trust.statement(app_database, (("BULK-7", 1_000_000),))
    proposer = staff()
    # A bulk whose payouts do not add up to its line cannot commit.
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(proposer) as session:
            bulk_id = push_id()
            await session.execute(
                text(
                    "INSERT INTO payout_bank_bulk_settlements (id, batch_id, statement_line_id, "
                    "reason, status, proposed_by, proposed_at) VALUES (:id, :b, :l, 'x', "
                    "'proposed', :me, now())"
                ),
                {"id": bulk_id, "b": batch_id, "l": lines["BULK-7"], "me": proposer.id},
            )
            await session.execute(
                text(
                    "INSERT INTO payout_bank_settlements (id, payout_id, batch_id, outcome, "
                    "statement_line_id, reason, status, proposed_by, proposed_at, bulk_id) "
                    "VALUES (:id, :p, :b, 'paid', :l, 'x', 'proposed', :me, now(), :bulk)"
                ),
                {
                    "id": push_id(),
                    "p": made[0].id,
                    "b": batch_id,
                    "l": lines["BULK-7"],
                    "me": proposer.id,
                    "bulk": bulk_id,
                },
            )
    bulk = await propose(
        app_database, batch_id, lines["BULK-7"], [each.id for each in made], by=proposer
    )
    # A payout cannot be added to a bulk after the transaction that proposed it.
    third_batch, third = await sent_batch(world, app_database, (10_000,))
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(proposer) as session:
            await session.execute(
                text(
                    "INSERT INTO payout_bank_settlements (id, payout_id, batch_id, outcome, "
                    "statement_line_id, reason, status, proposed_by, proposed_at, bulk_id) "
                    "VALUES (:id, :p, :b, 'paid', :l, 'x', 'proposed', :me, now(), :bulk)"
                ),
                {
                    "id": push_id(),
                    "p": third[0].id,
                    "b": third_batch,
                    "l": lines["BULK-7"],
                    "me": proposer.id,
                    "bulk": bulk.id,
                },
            )
    # Nor can one payout of it be approved apart from the bulk.
    checker = staff()
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(checker) as session:
            await session.execute(
                text(
                    "UPDATE payout_bank_settlements SET status = 'approved', decided_by = :me, "
                    "decided_at = now(), decision_reason = 'x' WHERE bulk_id = :bulk "
                    "AND payout_id = :p"
                ),
                {"me": checker.id, "bulk": bulk.id, "p": made[0].id},
            )
    # Nor the bulk approved without its payouts.
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(checker) as session:
            await session.execute(
                text(
                    "UPDATE payout_bank_bulk_settlements SET status = 'approved', "
                    "decided_by = :me, decided_at = now(), decision_reason = 'x' WHERE id = :id"
                ),
                {"me": checker.id, "id": bulk.id},
            )
    assert await status_of(app_database, made[0].id) is PayoutStatus.SENT
