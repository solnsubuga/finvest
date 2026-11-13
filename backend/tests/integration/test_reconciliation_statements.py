"""Importing statements: what is saved, who may do it, and what the database refuses."""

import asyncio
import secrets
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.reconciliation import statements as service
from app.modules.reconciliation.domain.statements import (
    CsvFormat,
    Direction,
    Statement,
    StatementSource,
    parse_csv,
)
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock

pytestmark = pytest.mark.integration

FORMAT = CsvFormat(
    value_date="Date",
    date_format="%Y-%m-%d",
    narrative="Details",
    reference="Reference",
    money_in="In",
    money_out="Out",
)
DAY = date(2026, 11, 10)
DIGEST = service.file_digest(b"the uploaded file")


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 20, 9, 0, tzinfo=UTC))


def staff() -> Actor:
    return Actor("staff", push_id())


def pot() -> str:
    # Each test has its own pot, because a pot takes one statement a day, one day after another.
    return f"pot-{secrets.token_hex(6)}"


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def statement(
    account: str,
    day: date = DAY,
    *,
    opening: int = 1_000_000,
    rows: tuple[tuple[str, int, int], ...] = (("PAY-1", 500_000, 0), ("OUT-1", 0, 200_000)),
    source: StatementSource = StatementSource.BANK,
) -> Statement:
    lines = "".join(
        f"{day.isoformat()},Transfer {number},{reference},{money_in or ''},{money_out or ''}\n"
        for number, (reference, money_in, money_out) in enumerate(rows)
    )
    closing = opening + sum(money_in - money_out for _, money_in, money_out in rows)
    return parse_csv(
        "Date,Details,Reference,In,Out\n" + lines,
        FORMAT,
        source=source,
        account_reference=account,
        statement_date=day,
        opening_balance=ugx(opening),
        closing_balance=ugx(closing),
    )


async def imported(
    database: Database, parsed: Statement, clock: FakeClock
) -> service.ImportedStatement:
    importer = staff()
    async with database.actor_transaction(importer) as session:
        return await service.import_statement(session, importer, parsed, DIGEST, clock)


async def test_a_statement_is_saved_with_its_lines_and_can_be_read_back(
    app_database: Database, fake_clock: FakeClock
) -> None:
    account, reader = pot(), staff()
    saved = await imported(app_database, statement(account), fake_clock)

    assert saved.line_count == 2
    assert saved.closing_balance == ugx(1_300_000)
    async with app_database.actor_transaction(reader) as session:
        assert await service.get_statement(session, StatementSource.BANK, account, DAY) == saved
        assert await service.get_statement(session, StatementSource.IOTEC, account, DAY) is None
        lines = await service.find_statement_lines(
            session, source=StatementSource.BANK, date_from=DAY, date_to=DAY, reference="PAY-1"
        )
        events = (
            await session.execute(
                text("SELECT action FROM audit_events WHERE resource_id = :id"), {"id": saved.id}
            )
        ).scalars()
        assert list(events) == ["reconciliation.statement_imported"]
    mine = [line for line in lines if line.account_reference == account]
    assert [(line.amount, line.direction) for line in mine] == [(ugx(500_000), Direction.IN)]


async def test_lines_are_found_by_amount_and_date_range(
    app_database: Database, fake_clock: FakeClock
) -> None:
    account, reader = pot(), staff()
    amount = ugx(secrets.randbelow(10**9) + 10**9)
    await imported(
        app_database, statement(account, rows=(("", amount.minor, 0), ("X", 7, 0))), fake_clock
    )

    async with app_database.actor_transaction(reader) as session:
        found = await service.find_statement_lines(
            session, source=StatementSource.BANK, date_from=DAY, date_to=DAY, amount=amount
        )
        too_early = await service.find_statement_lines(
            session,
            source=StatementSource.BANK,
            date_from=DAY - timedelta(days=5),
            date_to=DAY - timedelta(days=1),
            amount=amount,
        )
        other_source = await service.find_statement_lines(
            session, source=StatementSource.IOTEC, date_from=DAY, date_to=DAY, amount=amount
        )
    assert [line.account_reference for line in found] == [account]
    assert too_early == ()
    assert other_source == ()


async def test_only_staff_import_and_only_a_day_that_is_over(
    app_database: Database, fake_clock: FakeClock
) -> None:
    account, client = pot(), Actor("client", push_id())
    async with app_database.actor_transaction(client) as session:
        with pytest.raises(ApplicationError):
            await service.import_statement(session, client, statement(account), DIGEST, fake_clock)

    today = date(2026, 11, 20)
    with pytest.raises(service.StatementRefused) as refused:
        await imported(app_database, statement(account, today), fake_clock)
    assert refused.value.code == "statement_day_not_over"


async def test_a_day_is_imported_once_and_days_join_up(
    app_database: Database, fake_clock: FakeClock
) -> None:
    account = pot()
    await imported(app_database, statement(account), fake_clock)
    next_day = DAY + timedelta(days=1)

    cases = {
        "statement_already_imported": statement(account, rows=(("OTHER", 1, 0),)),
        "statement_day_out_of_order": statement(account, next_day + timedelta(days=1)),
        "statement_opening_balance_differs": statement(account, next_day, opening=1_299_999),
    }
    for code, parsed in cases.items():
        with pytest.raises(service.StatementRefused) as refused:
            await imported(app_database, parsed, fake_clock)
        assert refused.value.code == code

    following = await imported(
        app_database, statement(account, next_day, opening=1_300_000), fake_clock
    )
    assert following.business_date == next_day


async def test_a_refused_import_leaves_the_transaction_usable_and_saves_nothing(
    app_database: Database, fake_clock: FakeClock
) -> None:
    account, importer = pot(), staff()
    await imported(app_database, statement(account), fake_clock)

    async with app_database.actor_transaction(importer) as session:
        with pytest.raises(service.StatementRefused):
            await service.import_statement(
                session, importer, statement(account), DIGEST, fake_clock
            )
        count = await session.scalar(
            text(
                "SELECT count(*) FROM reconciliation_statements WHERE account_reference = :account"
            ),
            {"account": account},
        )
    assert count == 1


async def test_two_imports_of_the_same_day_at_once_save_one(
    app_database: Database, fake_clock: FakeClock
) -> None:
    account = pot()

    async def attempt() -> str:
        try:
            await imported(app_database, statement(account), fake_clock)
        except service.StatementRefused as refused:
            return refused.code
        return "imported"

    results = await asyncio.gather(attempt(), attempt())
    assert sorted(results) == ["imported", "statement_already_imported"]


INSERT_STATEMENT = text(
    "INSERT INTO reconciliation_statements (id, source, account_reference, business_date, "
    "currency, opening_minor, closing_minor, line_count, file_sha256, imported_by, imported_at) "
    "VALUES (:id, 'bank', :account, DATE '2026-11-10', 'UGX', 100, :closing, :line_count, "
    ":digest, :by, now())"
)
INSERT_LINE = text(
    "INSERT INTO reconciliation_statement_lines (id, statement_id, line_no, value_date, "
    "direction, amount_minor, reference, narrative, fingerprint) VALUES (:id, :statement, 1, "
    "DATE '2026-11-10', 'in', 50, '', '', :digest)"
)


async def test_the_database_refuses_a_statement_that_does_not_add_up(
    app_database: Database,
) -> None:
    importer = staff()
    for closing, line_count in ((151, 1), (150, 2)):
        with pytest.raises(DBAPIError, match="does not add up"):
            async with app_database.actor_transaction(importer) as session:
                statement_id = push_id()
                await session.execute(
                    INSERT_STATEMENT,
                    {
                        "id": statement_id,
                        "account": pot(),
                        "closing": closing,
                        "line_count": line_count,
                        "digest": DIGEST,
                        "by": importer.id,
                    },
                )
                await session.execute(
                    INSERT_LINE, {"id": push_id(), "statement": statement_id, "digest": DIGEST}
                )


async def test_the_database_refuses_changes_late_lines_and_other_people(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    account, importer, client = pot(), staff(), Actor("client", push_id())
    saved = await imported(app_database, statement(account), fake_clock)

    # A line cannot join a statement after the transaction that imported it.
    with pytest.raises(DBAPIError, match="only while their statement is being imported"):
        async with app_database.actor_transaction(importer) as session:
            await session.execute(
                INSERT_LINE,
                {"id": push_id(), "statement": saved.id, "digest": service.file_digest(b"late")},
            )
    for change in (
        "UPDATE reconciliation_statements SET closing_minor = 1 WHERE id = :id",
        "DELETE FROM reconciliation_statement_lines WHERE statement_id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with app_database.actor_transaction(importer) as session:
                await session.execute(text(change), {"id": saved.id})
    # Nobody imports in another person's name, and the worker does not import at all.
    values = {"id": push_id(), "account": pot(), "closing": 100, "line_count": 0, "digest": DIGEST}
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(importer) as session:
            await session.execute(INSERT_STATEMENT, {**values, "by": push_id()})
    with pytest.raises(DBAPIError):
        async with worker_database.transaction() as session:
            await session.execute(INSERT_STATEMENT, {**values, "by": importer.id})
    # A client sees nothing; the worker reads for the daily run.
    read = text("SELECT count(*) FROM reconciliation_statements WHERE id = :id")
    async with app_database.actor_transaction(client) as session:
        assert await session.scalar(read, {"id": saved.id}) == 0
    async with worker_database.transaction() as session:
        assert await session.scalar(read, {"id": saved.id}) == 1
