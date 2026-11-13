"""Matching and the day close: what ties, what two people sign, and what the database refuses.

The integration database is shared and neither a ledger close nor a day close can be undone,
so these tests work in 2001, each on the day after the last close, with pots of their own.
"""

import asyncio
import secrets
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.ledger import closing as ledger_closing
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.ledger.domain.errors import DayClosed
from app.modules.reconciliation import day_close, statements
from app.modules.reconciliation.domain.day_close import CloseError, CloseStatus
from app.modules.reconciliation.domain.matching import ExceptionKind
from app.modules.reconciliation.domain.pots import Pot
from app.modules.reconciliation.domain.statements import (
    Direction,
    Statement,
    StatementLine,
    StatementSource,
)
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.ledger import client, key, staff, ugx

pytestmark = pytest.mark.integration

FIRST_TEST_DAY = date(2001, 1, 1)
DIGEST = statements.file_digest(b"the uploaded file")


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 20, 9, 0, tzinfo=UTC))


def new_pot() -> Pot:
    name = secrets.token_hex(6)
    return Pot(StatementSource.BANK, f"pot-{name}", "bank.trust", f"test-{name}", "Test pot")


def line(day: date, amount: int, reference: str = "", *, out: bool = False) -> StatementLine:
    return StatementLine(
        value_date=day,
        direction=Direction.OUT if out else Direction.IN,
        amount=ugx(amount),
        reference=reference,
        narrative="Transfer",
        fingerprint=statements.file_digest(secrets.token_bytes(8)),
    )


class Books:
    """One pot of its own, with the ledger entries and statements a test gives it."""

    def __init__(
        self, database: Database, clock: FakeClock, worker: Database | None = None
    ) -> None:
        self.database, self.clock, self.worker = database, clock, worker
        self.pot = new_pot()
        self.statement_balance = 0

    async def post(self, day: date, amount: int, reference: str | None = None) -> str:
        """Money into the pot (or out of it, if negative), owed to a test account."""
        # Test money arrives as the worker posts it: staff may post only reviewed kinds.
        assert self.worker is not None, "posting needs the worker database"
        async with self.worker.actor_transaction(SYSTEM) as session:
            pot = await ledger.open_account(session, self.pot.ledger_account, self.clock)
            owed = await ledger.open_account(
                session,
                AccountSpec(
                    "test.owed",
                    AccountKind.LIABILITY,
                    Currency.UGX,
                    "Owed",
                    reference=self.pot.ledger_reference,
                ),
                self.clock,
            )
            lines = (
                (debit(pot.id, ugx(amount)), credit(owed.id, ugx(amount)))
                if amount > 0
                else (debit(owed.id, ugx(-amount)), credit(pot.id, ugx(-amount)))
            )
            posted = await ledger.post_entry(
                session,
                EntryDraft(
                    entry_type="test.movement",
                    effective_date=day,
                    lines=lines,
                    idempotency_key=key(),
                    source_type=None if reference is None else "test",
                    source_id=reference,
                ),
                self.clock,
            )
        return posted.id

    async def statement(
        self,
        day: date,
        *lines: StatementLine,
        importer: Actor | None = None,
        opening: int | None = None,
    ) -> None:
        opening = self.statement_balance if opening is None else opening
        self.statement_balance = opening
        for item in lines:
            self.statement_balance += item.signed_amount.minor
        importer = importer or staff()
        parsed = Statement(
            self.pot.source,
            self.pot.account_reference,
            day,
            ugx(opening),
            ugx(self.statement_balance),
            lines,
        )
        async with self.database.actor_transaction(importer) as session:
            await statements.import_statement(session, importer, parsed, DIGEST, self.clock)

    async def prepare(self, day: date, who: Actor | None = None) -> day_close.DayCloseView:
        who = who or staff()
        async with self.database.actor_transaction(who) as session:
            return await day_close.prepare(session, who, day, self.clock, pots=[self.pot])

    async def approve(self, day: date, who: Actor | None = None) -> day_close.DayCloseView:
        who = who or staff()
        async with self.database.actor_transaction(who) as session:
            return await day_close.approve(session, who, day, self.clock, pots=[self.pot])

    async def close(self, day: date) -> None:
        await self.prepare(day)
        await self.approve(day)


async def next_day(database: Database, clock: FakeClock) -> date:
    """The next day these tests can use: after the last day close and the last ledger close.

    Ledger tests close days too, without a day close. Days they have run ahead by are closed
    here first, with an empty pot, because day closes go one day after another.
    """
    while True:
        reader = staff()
        async with database.actor_transaction(reader) as session:
            mine = await day_close.last_closed(session)
            ledgers = await ledger_closing.closed_through(session)
        if mine is None:
            return FIRST_TEST_DAY if ledgers is None else ledgers + timedelta(days=1)
        if ledgers is None or mine >= ledgers:
            return mine + timedelta(days=1)
        filler = Books(database, clock)
        await filler.statement(mine + timedelta(days=1))
        await filler.close(mine + timedelta(days=1))


@pytest.fixture
async def books(app_database: Database, worker_database: Database, fake_clock: FakeClock) -> Books:
    return Books(app_database, fake_clock, worker_database)


async def test_a_day_that_ties_is_closed_by_two_people_and_the_ledger_closes_with_it(
    app_database: Database, books: Books, fake_clock: FakeClock
) -> None:
    day = await next_day(app_database, fake_clock)
    paid_in, paid_out = f"dep-{push_id()}", f"pay-{push_id()}"
    await books.post(day, 500_000, paid_in)
    await books.post(day, -200_000, paid_out)
    await books.statement(day, line(day, 500_000, paid_in), line(day, 200_000, paid_out, out=True))

    first, second = staff(), staff()
    prepared = await books.prepare(day, first)
    assert prepared.status is CloseStatus.PREPARED
    (position,) = prepared.positions
    assert (position.statement_closing, position.ledger_closing) == (ugx(300_000), ugx(300_000))
    assert (position.open_exceptions_count, position.unexplained) == (0, ugx(0))

    with pytest.raises(CloseError) as same_person:
        await books.approve(day, first)
    assert same_person.value.code == "close_needs_second_person"
    async with app_database.actor_transaction(first) as session:
        assert await ledger_closing.closed_through(session) != day

    closed = await books.approve(day, second)
    assert (closed.status, closed.prepared_by, closed.decided_by) == (
        CloseStatus.CLOSED,
        first.id,
        second.id,
    )
    async with app_database.actor_transaction(first) as session:
        assert await ledger_closing.closed_through(session) == day
        assert await day_close.last_closed(session) == day
        assert await day_close.get_close(session, day) == closed
        actions = (
            await session.execute(
                text("SELECT action FROM audit_events WHERE resource_id = :id ORDER BY id"),
                {"id": closed.id},
            )
        ).scalars()
        assert list(actions) == [
            "reconciliation.day_close_prepared",
            "reconciliation.day_closed",
        ]
    with pytest.raises(DayClosed):
        await books.post(day, 1)
    with pytest.raises(CloseError) as again:
        await books.prepare(day)
    assert again.value.code == "close_not_open"


async def test_what_is_unpaired_is_an_open_exception_until_a_person_pairs_it(
    app_database: Database, books: Books, fake_clock: FakeClock
) -> None:
    day = await next_day(app_database, fake_clock)
    entry_id = await books.post(day, 70_000)  # no reference, so nothing to pair it by
    await books.post(day, 5_000, "in-the-books-only")
    await books.statement(day, line(day, 70_000), line(day, 9_000, "on-the-statement-only"))
    checker = staff()

    async with app_database.actor_transaction(checker) as session:
        await day_close.refresh(session, checker, day, fake_clock, pots=[books.pot])
        (position,) = await day_close.positions(session, day, fake_clock, pots=[books.pot])
        open_now = await day_close.open_exceptions(session, books.pot, day)
    # The statement is 4,000 above the books, and the four open items say exactly why.
    assert position.statement_closing == ugx(79_000)
    assert position.ledger_closing == ugx(75_000)
    assert (position.open_exceptions_total, position.unexplained) == (ugx(4_000), ugx(0))
    assert sorted(item.kind for item in open_now) == sorted(
        [ExceptionKind.MISSING_IN_LEDGER] * 2 + [ExceptionKind.MISSING_IN_STATEMENT] * 2
    )
    by_amount = {
        item.statement_lines[0].amount.minor: item.statement_lines[0].fingerprint
        for item in open_now
        if item.statement_lines
    }

    async def pair(line_id: str, reason: str = "The bank left the reference off") -> None:
        async with app_database.actor_transaction(checker) as session:
            await day_close.match_by_hand(
                session,
                checker,
                statement_line_id=line_id,
                entry_id=entry_id,
                line_no=1,
                reason=reason,
                clock=fake_clock,
            )

    with pytest.raises(day_close.MatchRefused) as wrong_amount:
        await pair(by_amount[9_000])
    assert wrong_amount.value.code == "match_not_same_money"
    with pytest.raises(ApplicationError):
        await pair(by_amount[70_000], reason="  ")
    await pair(by_amount[70_000])
    with pytest.raises(day_close.MatchRefused) as twice:
        await pair(by_amount[70_000])
    assert twice.value.code == "line_already_matched"

    prepared = await books.prepare(day)
    assert prepared.positions[0].open_exceptions_count == 2
    await books.approve(day)


async def test_a_day_without_its_statement_cannot_be_prepared(
    app_database: Database, books: Books, fake_clock: FakeClock
) -> None:
    day = await next_day(app_database, fake_clock)
    await books.post(day, 1_000, "dep-1")

    with pytest.raises(CloseError) as missing:
        await books.prepare(day)
    assert missing.value.code == "statement_missing"
    with pytest.raises(CloseError) as today:
        await books.prepare(date(2026, 11, 20))
    assert today.value.code == "day_not_ended"
    with pytest.raises(CloseError) as nothing:
        await books.approve(day)
    assert nothing.value.code == "close_not_prepared"
    outsider = client()
    async with app_database.actor_transaction(outsider) as session:
        with pytest.raises(ApplicationError):
            await day_close.prepare(session, outsider, day, fake_clock, pots=[books.pot])


async def test_a_close_whose_figures_changed_is_refused_and_leaves_the_ledger_open(
    app_database: Database, books: Books, fake_clock: FakeClock
) -> None:
    day = await next_day(app_database, fake_clock)
    await books.post(day, 40_000, "dep-1")
    await books.statement(day, line(day, 40_000, "dep-1"))
    first, second = staff(), staff()
    await books.prepare(day, first)
    await books.post(day, 2_500, "late")  # posted into the day after the first signature

    with pytest.raises(CloseError) as changed:
        await books.approve(day, second)
    assert changed.value.code == "close_changed_since_prepared"
    async with app_database.actor_transaction(second) as session:
        assert await ledger_closing.closed_through(session) != day
        standing = await day_close.get_close(session, day)
        assert standing is not None
        assert standing.status is CloseStatus.PREPARED
        with pytest.raises(ApplicationError):
            await day_close.send_back(session, second, day, " ", fake_clock)
        await day_close.send_back(session, second, day, "A late entry arrived", fake_clock)
        assert await day_close.get_close(session, day) is None

    again = await books.prepare(day, second)
    assert again.positions[0].open_exceptions_total == ugx(-2_500)
    await books.approve(day, first)


async def test_an_approval_that_fails_inside_a_larger_transaction_undoes_the_ledger_close(
    app_database: Database, books: Books, fake_clock: FakeClock
) -> None:
    day = await next_day(app_database, fake_clock)
    await books.statement(day)
    await books.prepare(day)
    await books.post(day, 300, "late")
    second = staff()

    # The caller swallows the refusal and commits. The ledger must still be open for the day.
    async with app_database.actor_transaction(second) as session:
        with pytest.raises(CloseError):
            await day_close.approve(session, second, day, fake_clock, pots=[books.pot])
    async with app_database.actor_transaction(second) as session:
        assert await ledger_closing.closed_through(session) != day
        await day_close.send_back(session, second, day, "Figures changed", fake_clock)
    await books.close(day)


async def test_two_approvals_at_once_close_the_day_once(
    app_database: Database, books: Books, fake_clock: FakeClock
) -> None:
    day = await next_day(app_database, fake_clock)
    await books.statement(day)
    await books.prepare(day)

    async def attempt() -> str:
        try:
            await books.approve(day)
        except CloseError as refused:
            return refused.code
        return "closed"

    assert sorted(await asyncio.gather(attempt(), attempt())) == ["close_not_prepared", "closed"]


async def test_the_database_refuses_one_person_twice_a_skipped_day_and_any_change(
    app_database: Database, worker_database: Database, books: Books, fake_clock: FakeClock
) -> None:
    day = await next_day(app_database, fake_clock)
    await books.statement(day)
    first, second = staff(), staff()
    prepared = await books.prepare(day, first)
    decide = text(
        "UPDATE reconciliation_day_closes SET status = 'closed', decided_by = :by, "
        "decided_at = now() WHERE id = :id"
    )

    # The person who prepared it cannot close it, and nobody closes it in another's name.
    for actor, named in ((first, first.id), (second, push_id())):
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(actor) as session:
                await session.execute(decide, {"id": prepared.id, "by": named})
    # A position cannot be added after the close was prepared.
    add_position = text(
        "INSERT INTO reconciliation_close_positions (close_id, source, account_reference, "
        "currency, statement_closing_minor, ledger_closing_minor, open_total_minor, open_count) "
        "VALUES (:id, 'bank', 'another', 'UGX', :statement, 0, 0, 0)"
    )
    with pytest.raises(DBAPIError, match="only while their close is being prepared"):
        async with app_database.actor_transaction(second) as session:
            await session.execute(add_position, {"id": prepared.id, "statement": 0})
    insert_close = text(
        "INSERT INTO reconciliation_day_closes (id, business_date, status, prepared_by, "
        "prepared_at) VALUES (:id, :day, 'prepared', :by, now())"
    )
    await books.approve(day, second)

    # Once a day is closed the next close is for the next day, and a closed day stays closed.
    with pytest.raises(DBAPIError, match="one after another"):
        async with app_database.actor_transaction(second) as session:
            await session.execute(
                insert_close, {"id": push_id(), "day": day + timedelta(days=2), "by": second.id}
            )
    # A position that leaves a shilling unexplained cannot be saved.
    with pytest.raises(DBAPIError, match="ck_reconciliation_close_positions_explained"):
        async with app_database.actor_transaction(second) as session:
            close_id = push_id()
            await session.execute(
                insert_close,
                {"id": close_id, "day": day + timedelta(days=1), "by": second.id},
            )
            await session.execute(add_position, {"id": close_id, "statement": 1})
    for change in (
        "UPDATE reconciliation_day_closes SET status = 'sent_back', reason = 'x' WHERE id = :id",
        "DELETE FROM reconciliation_day_closes WHERE id = :id",
        "DELETE FROM reconciliation_close_positions WHERE close_id = :id",
        "UPDATE reconciliation_close_positions SET open_count = 9 WHERE close_id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(second) as session:
                await session.execute(text(change), {"id": prepared.id})
    # Clients see nothing, and the worker reads but does not sign.
    read = text("SELECT count(*) FROM reconciliation_day_closes WHERE id = :id")
    async with app_database.actor_transaction(client()) as session:
        assert await session.scalar(read, {"id": prepared.id}) == 0
    async with worker_database.transaction() as session:
        assert await session.scalar(read, {"id": prepared.id}) == 1
    with pytest.raises(DBAPIError):
        async with worker_database.transaction() as session:
            await session.execute(
                insert_close, {"id": push_id(), "day": day + timedelta(days=1), "by": first.id}
            )


async def test_the_database_refuses_a_match_by_reference_that_is_not_one(
    app_database: Database, books: Books, fake_clock: FakeClock
) -> None:
    day = await next_day(app_database, fake_clock)
    entry_id = await books.post(day, 8_000, "ref-a")
    await books.statement(day, line(day, 8_000, "ref-b"))
    checker = staff()
    async with app_database.actor_transaction(checker) as session:
        await day_close.refresh(session, checker, day, fake_clock, pots=[books.pot])
        (missing_in_ledger,) = [
            item
            for item in await day_close.open_exceptions(session, books.pot, day)
            if item.statement_lines
        ]
    line_id = missing_in_ledger.statement_lines[0].fingerprint
    insert = text(
        "INSERT INTO reconciliation_matches (statement_line_id, entry_id, line_no, how, "
        "matched_by, reason, matched_at) VALUES (:line, :entry, 1, :how, :by, :reason, now())"
    )
    values = {"line": line_id, "entry": entry_id, "by": checker.id}
    for how, reason, refusal in (
        ("reference", None, "the same reference on both sides"),
        ("staff", None, "ck_reconciliation_matches_reason"),
        # A match from what another module recorded says what that was.
        ("recorded", None, "ck_reconciliation_matches_reason"),
        ("recorded", " ", "ck_reconciliation_matches_reason"),
        ("guessed", "Looks right", "ck_reconciliation_matches_how"),
    ):
        with pytest.raises(DBAPIError, match=refusal):
            async with app_database.actor_transaction(checker) as session:
                await session.execute(insert, {**values, "how": how, "reason": reason})
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(checker) as session:
            await session.execute(
                insert, {**values, "how": "staff", "reason": "Mine", "by": push_id()}
            )
    await books.close(day)


async def test_the_person_who_imported_the_day_cannot_close_it(
    app_database: Database, worker_database: Database, books: Books, fake_clock: FakeClock
) -> None:
    day = await next_day(app_database, fake_clock)
    importer = staff()
    await books.statement(day, importer=importer)
    prepared = await books.prepare(day)

    with pytest.raises(CloseError) as refused:
        await books.approve(day, importer)
    assert refused.value.code == "close_needs_someone_who_did_not_import"
    with pytest.raises(DBAPIError, match="imported a statement for the day"):
        async with app_database.actor_transaction(importer) as session:
            await session.execute(
                text(
                    "UPDATE reconciliation_day_closes SET status = 'closed', decided_by = :by, "
                    "decided_at = now() WHERE id = :id"
                ),
                {"id": prepared.id, "by": importer.id},
            )
    await books.approve(day)


async def test_a_first_statement_that_opens_away_from_the_books_cannot_be_closed(
    app_database: Database, books: Books, fake_clock: FakeClock
) -> None:
    """A pot's first statement sets its own opening balance. The close compares the closing
    balance with the ledger, so an opening that is not the books' shows as unexplained."""
    day = await next_day(app_database, fake_clock)
    await books.post(day, 10_000, "dep-1")
    await books.statement(day, line(day, 10_000, "dep-1"), opening=50_000)

    with pytest.raises(CloseError) as refused:
        await books.prepare(day)
    assert refused.value.code == "difference_unexplained"

    # The pot's real opening was zero; the right statement for the day ties.
    honest = Books(app_database, fake_clock, books.worker)
    await honest.post(day, 10_000, "dep-1")
    await honest.statement(day, line(day, 10_000, "dep-1"))
    checker = staff()
    async with app_database.actor_transaction(checker) as session:
        await day_close.refresh(session, checker, day, fake_clock, pots=[honest.pot])
        (position,) = await day_close.positions(session, day, fake_clock, pots=[honest.pot])
    assert position.unexplained == ugx(0)
