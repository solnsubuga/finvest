"""The books by account code, and the accounting export, against real PostgreSQL."""

import secrets
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.finance import books
from app.modules.finance.domain.journal import ExportRefused
from app.modules.ledger import closing, statements
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.ledger.domain.errors import MixedAccountKinds
from app.shared.actors import Actor
from app.shared.clock import SystemClock
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.database import TEST_SOURCE
from tests.support.investments import today
from tests.support.ledger import client, staff

pytestmark = pytest.mark.integration

CLOCK = SystemClock()
UGX = Currency.UGX


def ugx(minor: int) -> Money:
    return Money(minor, UGX)


def fresh_code(prefix: str) -> str:
    return f"test.{prefix}_{secrets.token_hex(6)}"


async def account(
    session: AsyncSession, code: str, kind: AccountKind, *, owner: str | None = None
) -> str:
    opened = await ledger.open_account(
        session,
        AccountSpec(
            code, kind, UGX, code.split(".")[1], owner_client_id=owner, reference=push_id()
        ),
        CLOCK,
    )
    return opened.id


async def post(session: AsyncSession, day: date, debit_to: str, credit_to: str, minor: int) -> None:
    await ledger.post_entry(
        session,
        EntryDraft(
            entry_type="test.finance",
            effective_date=day,
            lines=(debit(debit_to, ugx(minor)), credit(credit_to, ugx(minor))),
            idempotency_key=f"test-{push_id()}",
            source_type=TEST_SOURCE,
            source_id=push_id(),
        ),
        CLOCK,
    )


async def test_books_add_each_code_up_across_clients_with_opening_and_closing(
    app_database: Database,
) -> None:
    cash_code, owed_code, income_code = (
        fresh_code("cash"),
        fresh_code("owed"),
        fresh_code("income"),
    )
    day = today()
    earlier = day - timedelta(days=3)
    async with app_database.actor_transaction(staff()) as session:
        cash = await account(session, cash_code, AccountKind.ASSET)
        income = await account(session, income_code, AccountKind.INCOME)
        first = await account(session, owed_code, AccountKind.LIABILITY, owner=client().id)
        second = await account(session, owed_code, AccountKind.LIABILITY, owner=client().id)
        await post(session, earlier, cash, first, 100_000)
        await post(session, day, cash, second, 40_000)
        await post(session, day, first, cash, 25_000)
        await post(session, day, cash, income, 1_200)
        found = {
            t.code: t
            for t in await statements.totals_by_code(session, UGX, date_from=day, date_to=day)
        }
        journal = [
            t
            for t in await statements.day_totals(session, UGX, date_from=earlier, date_to=day)
            if t.code in (cash_code, owed_code)
        ]
    owed = found[owed_code]
    assert (owed.kind, owed.opening, owed.debits, owed.credits, owed.closing) == (
        "liability",
        ugx(100_000),
        ugx(25_000),
        ugx(40_000),
        ugx(115_000),
    )
    the_cash = found[cash_code]
    assert (the_cash.opening, the_cash.debits, the_cash.credits, the_cash.closing) == (
        ugx(100_000),
        ugx(41_200),
        ugx(25_000),
        ugx(116_200),
    )
    assert found[income_code].closing == ugx(1_200)
    assert [(t.effective_date, t.code, t.debits, t.credits, t.entries) for t in journal] == [
        (earlier, cash_code, ugx(100_000), ugx(0), 1),
        (earlier, owed_code, ugx(0), ugx(100_000), 1),
        (day, cash_code, ugx(41_200), ugx(25_000), 3),
        (day, owed_code, ugx(25_000), ugx(40_000), 2),
    ]


async def test_books_before_a_code_moved_leave_it_out(app_database: Database) -> None:
    code = fresh_code("later")
    async with app_database.actor_transaction(staff()) as session:
        cash = await account(session, code, AccountKind.ASSET)
        other = await account(session, fresh_code("other"), AccountKind.LIABILITY)
        await post(session, today(), cash, other, 10)
        before = today() - timedelta(days=1)
        found = await statements.totals_by_code(session, UGX, date_from=before, date_to=before)
    assert code not in {t.code for t in found}


async def test_codes_of_two_kinds_cannot_be_added(app_database: Database) -> None:
    code = fresh_code("mixed")

    class Undo(Exception):
        pass

    with pytest.raises(Undo):
        async with app_database.actor_transaction(staff()) as session:
            asset = await account(session, code, AccountKind.ASSET)
            liability = await account(session, code, AccountKind.LIABILITY)
            await post(session, today(), asset, liability, 10)
            with pytest.raises(MixedAccountKinds):
                await statements.totals_by_code(session, UGX, date_from=today(), date_to=today())
            raise Undo


async def test_an_export_reads_closed_days_and_is_recorded_with_its_audit_event(
    app_database: Database,
) -> None:
    """Closing yesterday would hold up every other test, so it happens in a transaction that is
    then rolled back."""
    cash_code, owed_code = fresh_code("cash"), fresh_code("owed")
    yesterday = today() - timedelta(days=1)
    exporter = staff()
    exporter_id = exporter.id
    assert exporter_id is not None

    class Undo(Exception):
        pass

    with pytest.raises(Undo):
        async with app_database.actor_transaction(exporter) as session:
            already = await closing.closed_through(session)
            if already is not None and already >= yesterday:
                pytest.skip("yesterday is already closed in this database")
            cash = await account(session, cash_code, AccountKind.ASSET)
            owed = await account(session, owed_code, AccountKind.LIABILITY, owner=client().id)
            await post(session, yesterday, cash, owed, 75_000)
            with pytest.raises(ExportRefused) as early:
                await books.export_journal(
                    session, exporter_id, UGX, date_from=yesterday, date_to=yesterday, clock=CLOCK
                )
            assert early.value.code == "day_not_closed"
            await closing.close_through(session, exporter, yesterday, CLOCK)
            journal = await books.export_journal(
                session, exporter_id, UGX, date_from=yesterday, date_to=yesterday, clock=CLOCK
            )
            reference = f"FINVEST-UGX-{yesterday.strftime('%Y%m%d')}"
            rows = journal.content.split("\r\n")
            assert (
                f"{yesterday.isoformat()},{reference},{cash_code},{cash_code[5:]},asset,UGX,75000,0"
                in rows
            )
            assert (
                f"{yesterday.isoformat()},{reference},{owed_code},{owed_code[5:]},liability,UGX,0,75000"
                in rows
            )
            assert journal.record.lines == len(rows) - 2
            assert journal.record.debit_total_minor >= 75_000
            assert await books.sent_through(session, UGX) == yesterday
            (latest, *_) = await books.recent_exports(session, UGX)
            assert latest.id == journal.record.id
            assert latest.exported_by == exporter.id
            event = (
                await session.execute(
                    text(
                        "SELECT action FROM audit_events WHERE resource_type = "
                        "'finance_journal_export' AND resource_id = :id"
                    ),
                    {"id": journal.record.id},
                )
            ).scalar_one()
            assert event == "finance.journal_exported"
            raise Undo


async def test_the_database_keeps_export_records_as_written_and_by_the_person_signed_in(
    app_database: Database, worker_database: Database
) -> None:
    person = staff()
    insert = text(
        "INSERT INTO finance_journal_exports (id, currency, date_from, date_to, lines, "
        "debit_total_minor, exported_by, exported_at) VALUES (:id, 'UGX', DATE '2001-01-01', "
        "DATE '2001-01-01', 0, 0, :by, now())"
    )
    record_id = push_id()
    async with app_database.actor_transaction(person) as session:
        await session.execute(insert, {"id": record_id, "by": person.id})
    with pytest.raises(DBAPIError, match="recorded by the person signed in"):
        async with app_database.actor_transaction(person) as session:
            await session.execute(insert, {"id": push_id(), "by": push_id()})
    with pytest.raises(DBAPIError, match=r"never changes|permission denied"):
        async with app_database.actor_transaction(person) as session:
            await session.execute(
                text("UPDATE finance_journal_exports SET lines = 9 WHERE id = :id"),
                {"id": record_id},
            )
    with pytest.raises(DBAPIError, match=r"never changes|permission denied"):
        async with app_database.actor_transaction(person) as session:
            await session.execute(
                text("DELETE FROM finance_journal_exports WHERE id = :id"), {"id": record_id}
            )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(Actor("staff", push_id())) as session:
            await session.execute(insert, {"id": push_id(), "by": push_id()})
    async with app_database.actor_transaction(client()) as session:
        seen = (
            await session.execute(
                text("SELECT count(*) FROM finance_journal_exports WHERE id = :id"),
                {"id": record_id},
            )
        ).scalar_one()
    assert seen == 0
