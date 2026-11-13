"""A closed day stays closed, and PostgreSQL holds every role to it.

Closing is one fact for the whole ledger and cannot be undone, and these tests share a database
with every other integration test. So they only ever close days in 2001, long before any date
another test posts on, and each test works on the first day that is still open.
"""

import asyncio
from dataclasses import replace
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.database.engine import apply_actor
from app.modules.audit import service as audit
from app.modules.ledger import closing, corrections, statements
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import EntryDraft, InvalidEntry
from app.modules.ledger.domain.errors import DayClosed, DayNotEnded
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from tests.support.clock import FakeClock
from tests.support.ledger import (
    Books,
    client,
    deposit,
    funded_books,
    key,
    operator,
    payout,
    staff,
    ugx,
)

pytestmark = pytest.mark.integration

FIRST_TEST_DAY = date(2001, 1, 1)
RAW_CLOSE = text(
    "INSERT INTO ledger_closed_days (closed_through, closed_by, closed_at) "
    "VALUES (:day, :me, now())"
)


async def first_open_day(database: Database) -> date:
    async with database.actor_transaction(operator(database)) as session:
        closed = await closing.closed_through(session)
    return FIRST_TEST_DAY if closed is None else closed + timedelta(days=1)


def on(day: date, draft: EntryDraft) -> EntryDraft:
    return replace(draft, effective_date=day)


async def close(database: Database, day: date, clock: FakeClock) -> Actor:
    closer = staff()
    async with database.actor_transaction(closer) as session:
        await closing.close_through(session, closer, day, clock)
    return closer


async def test_a_closed_day_takes_no_more_entries(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    day = await first_open_day(app_database)
    next_day = day + timedelta(days=1)
    books = await funded_books(app_database, fake_clock, 0)
    async with app_database.actor_transaction(staff()) as session:
        await ledger.post_entry(session, on(day, deposit(books, 1_000)), fake_clock)

    closer = await close(app_database, day, fake_clock)

    for database, actor in ((app_database, staff()), (worker_database, SYSTEM)):
        async with database.actor_transaction(actor) as session:
            assert await closing.closed_through(session) == day
            for closed_day in (day, day - timedelta(days=1), date(1999, 12, 31)):
                with pytest.raises(DayClosed):
                    await ledger.post_entry(session, on(closed_day, payout(books, 100)), fake_clock)
            # The refusal left the transaction usable, and the next day is open.
            await ledger.post_entry(session, on(next_day, payout(books, 100)), fake_clock)
    async with app_database.actor_transaction(staff()) as session:
        assert await statements.balance_as_of(session, books.cash, day) == ugx(1_000)
        assert await statements.balance_as_of(session, books.cash, next_day) == ugx(800)
        latest = await closing.latest_close(session)
        events = await audit.events_for_resource(session, "ledger_closed_day", day.isoformat())
    assert latest is not None
    assert (latest.closed_through, latest.closed_by) == (day, closer.id)
    assert latest.closed_at == fake_clock.now()
    assert [(event.action, event.actor_id) for event in events] == [
        ("ledger.day_closed", closer.id)
    ]


async def test_a_reversal_or_correction_cannot_be_dated_in_a_closed_day(
    app_database: Database, fake_clock: FakeClock
) -> None:
    day = await first_open_day(app_database)
    next_day = day + timedelta(days=1)
    books = await funded_books(app_database, fake_clock, 0)
    maker, checker = staff(), staff()
    async with app_database.actor_transaction(maker) as session:
        wrong = await ledger.post_entry(session, on(day, deposit(books, 500)), fake_clock)
        stale = await corrections.propose_correction(
            session,
            maker,
            fake_clock,
            corrects_entry_id=wrong.id,
            effective_date=day,
            reason="Posted twice",
            reverse=True,
        )
    await close(app_database, day, fake_clock)

    async with app_database.actor_transaction(checker) as session:
        with pytest.raises(DayClosed):
            await ledger.reverse_entry(
                session, wrong.id, fake_clock, effective_date=day, idempotency_key=key()
            )
        # Approved before the close, it could have posted. After it, it cannot, and it stays
        # undecided so it can be rejected and proposed again with an open date.
        with pytest.raises(DayClosed):
            await corrections.approve_correction(session, checker, stale.id, fake_clock)
        assert await corrections.get_decision(session, stale.id) is None
        await corrections.reject_correction(
            session, checker, stale.id, "The day is closed", fake_clock
        )
    async with app_database.actor_transaction(maker) as session:
        fresh = await corrections.propose_correction(
            session,
            maker,
            fake_clock,
            corrects_entry_id=wrong.id,
            effective_date=next_day,
            reason="Posted twice",
            reverse=True,
        )
    async with app_database.actor_transaction(checker) as session:
        entry = await corrections.approve_correction(session, checker, fresh.id, fake_clock)
        assert entry.effective_date == next_day
        # The closed day still shows what was agreed. The fix shows on the day it was made.
        assert await statements.balance_as_of(session, books.cash, day) == ugx(500)
        assert await statements.balance_as_of(session, books.cash, next_day) == ugx(0)


async def test_closed_days_only_move_forward(app_database: Database, fake_clock: FakeClock) -> None:
    day = await first_open_day(app_database) + timedelta(days=2)
    first = await close(app_database, day, fake_clock)
    again = staff()
    async with app_database.actor_transaction(again) as session:
        # Closing the same day or an earlier one changes nothing and says who closed it.
        for earlier in (day, day - timedelta(days=1), date(1999, 1, 1)):
            standing = await closing.close_through(session, again, earlier, fake_clock)
            assert (standing.closed_through, standing.closed_by) == (day, first.id)
        assert await closing.closed_through(session) == day
    for earlier in (day, day - timedelta(days=1)):
        with pytest.raises(DBAPIError, match="already closed through"):
            async with app_database.actor_transaction(again) as session:
                await session.execute(RAW_CLOSE, {"day": earlier, "me": again.id})


async def test_today_and_future_days_cannot_be_closed(
    app_database: Database, fake_clock: FakeClock
) -> None:
    closer = staff()
    today = business_date(fake_clock.now())
    before = await first_open_day(app_database)
    async with app_database.actor_transaction(closer) as session:
        for day in (today, today + timedelta(days=1)):
            with pytest.raises(DayNotEnded):
                await closing.close_through(session, closer, day, fake_clock)
    # The database has its own clock and does not take the caller's word for it.
    for day in (date(2999, 1, 1), date(2100, 6, 30)):
        with pytest.raises(DBAPIError, match="only after it has ended"):
            async with app_database.actor_transaction(closer) as session:
                await session.execute(RAW_CLOSE, {"day": day, "me": closer.id})
    # A caller whose clock runs ahead of the database is refused the same way.
    fake_clock.advance(days=366 * 900)
    async with app_database.actor_transaction(closer) as session:
        with pytest.raises(DayNotEnded):
            await closing.close_through(session, closer, date(2999, 1, 1), fake_clock)
    assert await first_open_day(app_database) == before


async def test_only_staff_close_a_day_and_only_as_themselves(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    day = await first_open_day(app_database)
    someone = client()
    async with app_database.actor_transaction(someone) as session:
        with pytest.raises(InvalidEntry):
            await closing.close_through(session, someone, day, fake_clock)
        # A client cannot see whether anything is closed.
        assert await closing.closed_through(session) is None
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(someone) as session:
            await session.execute(RAW_CLOSE, {"day": day, "me": someone.id})
    async with worker_database.actor_transaction(SYSTEM) as session:
        with pytest.raises(InvalidEntry):
            await closing.close_through(session, SYSTEM, day, fake_clock)
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(RAW_CLOSE, {"day": day, "me": staff().id})
    # One staff member cannot sign as another.
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(staff()) as session:
            await session.execute(RAW_CLOSE, {"day": day, "me": staff().id})
    assert await first_open_day(app_database) == day


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ledger_closed_days SET closed_through = DATE '1990-01-01'",
        "DELETE FROM ledger_closed_days",
        "TRUNCATE ledger_closed_days",
    ],
)
async def test_a_closed_day_is_never_reopened(
    app_database: Database, migrator_database: Database, fake_clock: FakeClock, statement: str
) -> None:
    day = await first_open_day(app_database)
    await close(app_database, day, fake_clock)
    with pytest.raises(DBAPIError, match="is insert-only"):
        async with migrator_database.transaction() as session:
            await session.execute(text(statement))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(staff()) as session:
            await session.execute(text(statement))
    assert await first_open_day(app_database) == day + timedelta(days=1)


async def _post(database: Database, books: Books, day: date, clock: FakeClock) -> str:
    try:
        async with database.actor_transaction(staff()) as session:
            await ledger.post_entry(session, on(day, deposit(books, 10)), clock)
    except DayClosed:
        return "refused"
    return "posted"


async def test_an_entry_posted_while_a_day_is_being_closed_waits_and_is_refused(
    app_database: Database, fake_clock: FakeClock
) -> None:
    day = await first_open_day(app_database)
    books = await funded_books(app_database, fake_clock, 0)
    closer = staff()
    async with app_database.actor_transaction(closer) as session:
        await closing.close_through(session, closer, day, fake_clock)
        # The close has not committed. An entry dated in that day must not get in ahead of it.
        late = asyncio.create_task(_post(app_database, books, day, fake_clock))
        done, _ = await asyncio.wait({late}, timeout=0.5)
        assert not done, "the entry did not wait for the close"
        # What the closer reads now is final.
        agreed = await statements.balance_as_of(session, books.cash, day)
    assert await late == "refused"
    async with app_database.actor_transaction(staff()) as session:
        assert await statements.balance_as_of(session, books.cash, day) == agreed == ugx(0)
    assert await _post(app_database, books, day + timedelta(days=1), fake_clock) == "posted"


async def test_a_close_waits_for_an_entry_that_is_still_being_posted(
    app_database: Database, fake_clock: FakeClock
) -> None:
    day = await first_open_day(app_database)
    books = await funded_books(app_database, fake_clock, 0)

    async def close_and_read() -> int:
        closer = staff()
        async with app_database.actor_transaction(closer) as session:
            await closing.close_through(session, closer, day, fake_clock)
            return (await statements.balance_as_of(session, books.cash, day)).minor

    async with app_database.actor_transaction(staff()) as session:
        await ledger.post_entry(session, on(day, deposit(books, 250)), fake_clock)
        # The entry is not committed yet. The close must wait for it, so that the balance read
        # after closing includes it.
        closer_task = asyncio.create_task(close_and_read())
        done, _ = await asyncio.wait({closer_task}, timeout=0.5)
        assert not done, "the close did not wait for the entry"
    assert await closer_task == 250
    assert await _post(app_database, books, day, fake_clock) == "refused"


async def test_two_staff_closing_at_once_close_once(
    app_database: Database, fake_clock: FakeClock
) -> None:
    day = await first_open_day(app_database)

    async def attempt() -> str:
        closer = staff()
        async with app_database.actor_transaction(closer) as session:
            return (await closing.close_through(session, closer, day, fake_clock)).closed_by

    first, second = await asyncio.gather(attempt(), attempt())
    assert first == second
    async with app_database.actor_transaction(staff()) as session:
        latest = await closing.latest_close(session)
        events = await audit.events_for_resource(session, "ledger_closed_day", day.isoformat())
    assert latest is not None
    assert (latest.closed_through, latest.closed_by) == (day, first)
    assert len(events) == 1  # the one who lost the race closed nothing and left no trace


@pytest.mark.parametrize("level", ["REPEATABLE READ", "SERIALIZABLE"])
async def test_the_ledger_refuses_a_transaction_that_reads_an_old_snapshot(
    app_database: Database, fake_clock: FakeClock, level: str
) -> None:
    """In these isolation levels the closed-day check would not see a close that committed
    while it waited, so the database refuses to post or close in them at all."""
    day = await first_open_day(app_database)
    books = await funded_books(app_database, fake_clock, 0)
    actor = staff()
    for attempt in ("post", "close"):
        with pytest.raises(DBAPIError, match="read committed"):
            async with app_database.engine.connect() as plain:
                connection = await plain.execution_options(isolation_level=level)
                async with AsyncSession(bind=connection) as session, session.begin():
                    await apply_actor(session, actor)
                    if attempt == "post":
                        await ledger.post_entry(session, on(day, deposit(books, 10)), fake_clock)
                    else:
                        await session.execute(RAW_CLOSE, {"day": day, "me": actor.id})
    assert await first_open_day(app_database) == day
    async with app_database.actor_transaction(staff()) as session:
        assert await statements.balance_as_of(session, books.cash, day) == ugx(0)
    # The ordinary transaction still posts.
    assert await _post(app_database, books, day, fake_clock) == "posted"
