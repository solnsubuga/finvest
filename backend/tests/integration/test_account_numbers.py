"""Account numbers are issued once and never again, proved against real PostgreSQL."""

import asyncio
import secrets
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.account_numbers.domain.numbers import (
    RANDOM_SPACE,
    AccountNumber,
    AccountType,
    luhn_check_digit,
)
from app.modules.account_numbers.service import (
    MAX_ATTEMPTS,
    AccountNumbersExhausted,
    is_issued,
    issue_account_number,
)
from app.shared.actors import SYSTEM, Actor
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.ledger import operator

pytestmark = pytest.mark.integration


def scripted(*random_parts: int) -> Callable[[int], int]:
    """A random source that returns the given values in turn."""
    remaining = list(random_parts)

    def randbelow(limit: int) -> int:
        assert limit == RANDOM_SPACE
        return remaining.pop(0)

    return randbelow


def unused_random_part() -> int:
    return secrets.randbelow(RANDOM_SPACE)


@pytest.mark.parametrize("account_type", AccountType)
async def test_an_issued_number_is_valid_and_recorded(
    application_database: Database, fake_clock: FakeClock, account_type: AccountType
) -> None:
    async with application_database.actor_transaction(operator(application_database)) as session:
        number = await issue_account_number(session, account_type, fake_clock)
        assert await is_issued(session, number)
    assert number.account_type is account_type
    assert AccountNumber(number.value) == number
    async with application_database.actor_transaction(operator(application_database)) as session:
        assert await is_issued(session, number)
        issued_at = (
            await session.execute(
                text("SELECT issued_at FROM account_numbers WHERE number = :number"),
                {"number": number.value},
            )
        ).scalar_one()
    assert issued_at == fake_clock.now()


async def test_a_number_is_not_kept_if_the_approval_rolls_back(
    app_database: Database, fake_clock: FakeClock
) -> None:
    with pytest.raises(RuntimeError, match="approval failed"):
        async with app_database.actor_transaction(operator(app_database)) as session:
            number = await issue_account_number(session, AccountType.PERSONAL, fake_clock)
            raise RuntimeError("approval failed")
    async with app_database.actor_transaction(operator(app_database)) as session:
        assert not await is_issued(session, number)


async def test_a_taken_number_is_skipped(app_database: Database, fake_clock: FakeClock) -> None:
    taken, free = unused_random_part(), unused_random_part()
    async with app_database.actor_transaction(operator(app_database)) as session:
        first = await issue_account_number(
            session, AccountType.PERSONAL, fake_clock, randbelow=scripted(taken)
        )
    async with app_database.actor_transaction(operator(app_database)) as session:
        second = await issue_account_number(
            session, AccountType.PERSONAL, fake_clock, randbelow=scripted(taken, taken, free)
        )
    assert first == AccountNumber.compose(AccountType.PERSONAL, taken)
    assert second == AccountNumber.compose(AccountType.PERSONAL, free)


async def test_the_same_digits_can_serve_another_type(
    app_database: Database, fake_clock: FakeClock
) -> None:
    digits = unused_random_part()
    async with app_database.actor_transaction(operator(app_database)) as session:
        personal = await issue_account_number(
            session, AccountType.PERSONAL, fake_clock, randbelow=scripted(digits)
        )
        company = await issue_account_number(
            session, AccountType.COMPANY, fake_clock, randbelow=scripted(digits)
        )
    assert personal.value[2:9] == company.value[2:9]
    assert personal != company


async def test_issuing_gives_up_when_no_number_is_free(
    app_database: Database, fake_clock: FakeClock
) -> None:
    taken = unused_random_part()
    async with app_database.actor_transaction(operator(app_database)) as session:
        await issue_account_number(
            session, AccountType.JOINT, fake_clock, randbelow=scripted(taken)
        )
    async with app_database.actor_transaction(operator(app_database)) as session:
        with pytest.raises(AccountNumbersExhausted):
            await issue_account_number(
                session, AccountType.JOINT, fake_clock, randbelow=scripted(*[taken] * MAX_ATTEMPTS)
            )
        # The transaction is still usable.
        await issue_account_number(session, AccountType.JOINT, fake_clock)


async def test_two_approvals_drawing_the_same_number_get_different_ones(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    contested, first_spare, second_spare = (unused_random_part() for _ in range(3))
    barrier = AsyncBarrier(2)

    async def approve(database: Database, spare: int) -> AccountNumber:
        async with database.actor_transaction(operator(database)) as session:
            await barrier.wait()
            number = await issue_account_number(
                session, AccountType.GROUP, fake_clock, randbelow=scripted(contested, spare)
            )
            # Hold the transaction open so the other approval meets an uncommitted claim.
            await asyncio.sleep(0.2)
            return number

    first, second = await asyncio.gather(
        approve(app_database, first_spare), approve(worker_database, second_spare)
    )
    assert first != second
    winner = AccountNumber.compose(AccountType.GROUP, contested)
    assert [first, second].count(winner) == 1


async def test_many_concurrent_approvals_never_share_a_number(
    app_database: Database, fake_clock: FakeClock
) -> None:
    async def approve() -> AccountNumber:
        async with app_database.actor_transaction(operator(app_database)) as session:
            return await issue_account_number(session, AccountType.PERSONAL, fake_clock)

    numbers = await asyncio.gather(*(approve() for _ in range(40)))
    assert len(set(numbers)) == 40


@pytest.mark.parametrize(
    ("number", "constraint"),
    [
        ("1001004828", "ck_account_numbers_check_digit"),
        ("100100482", "ck_account_numbers_"),
        ("10010048270", "value too long"),
        ("9901004828", "ck_account_numbers_"),
        ("5000000017", "ck_account_numbers_format"),  # a good check digit, an unknown type
        ("10a1004827", "ck_account_numbers_"),
        ("1001 00482", "ck_account_numbers_"),
    ],
)
async def test_the_database_refuses_a_malformed_number(
    application_database: Database, number: str, constraint: str
) -> None:
    with pytest.raises(DBAPIError, match=constraint):
        async with application_database.actor_transaction(
            operator(application_database)
        ) as session:
            await session.execute(
                text("INSERT INTO account_numbers (number, issued_at) VALUES (:number, now())"),
                {"number": number},
            )


async def test_the_database_and_python_agree_on_check_digits(app_database: Database) -> None:
    payloads = [f"{secrets.randbelow(10**9):09d}" for _ in range(300)]
    good = [payload + luhn_check_digit(payload) for payload in payloads]
    bad = [
        value[:9] + str((int(value[9]) + 1 + index % 9) % 10) for index, value in enumerate(good)
    ]
    async with app_database.actor_transaction(operator(app_database)) as session:
        results = (
            await session.execute(
                text(
                    "SELECT value, finvest_luhn_valid(value) "
                    "FROM unnest(CAST(:values AS text[])) AS value"
                ),
                {"values": [*good, *bad, "", "12a4", "1234 5678"]},
            )
        ).all()
    verdicts = {row[0]: row[1] for row in results}
    assert all(verdicts[value] for value in good)
    assert not any(verdicts[value] for value in bad)
    assert verdicts[""] is False and verdicts["12a4"] is False and verdicts["1234 5678"] is False


REGISTRY_REFUSAL = "never changed or removed"


@pytest.mark.parametrize(
    ("statement", "refusal"),
    [
        ("UPDATE account_numbers SET issued_at = now()", REGISTRY_REFUSAL),
        ("DELETE FROM account_numbers", REGISTRY_REFUSAL),
        # Identity checks reference the registry, so PostgreSQL stops a plain TRUNCATE before
        # the trigger runs. With CASCADE it reaches the trigger.
        ("TRUNCATE account_numbers", "referenced in a foreign key constraint"),
        ("TRUNCATE account_numbers CASCADE", REGISTRY_REFUSAL),
    ],
)
async def test_a_number_is_never_changed_or_released(
    app_database: Database,
    worker_database: Database,
    migrator_database: Database,
    fake_clock: FakeClock,
    statement: str,
    refusal: str,
) -> None:
    async with app_database.actor_transaction(operator(app_database)) as session:
        number = await issue_account_number(session, AccountType.PERSONAL, fake_clock)
    for database in (app_database, worker_database):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with database.actor_transaction(operator(database)) as session:
                await session.execute(text(statement))
    # Closing an account must not free its number, whoever tries.
    with pytest.raises(DBAPIError, match=refusal):
        async with migrator_database.transaction() as session:
            await session.execute(text(statement))
    async with app_database.actor_transaction(operator(app_database)) as session:
        assert await is_issued(session, number)


async def test_only_staff_and_the_worker_can_issue_or_look_up_numbers(
    app_database: Database, fake_clock: FakeClock
) -> None:
    async with app_database.actor_transaction(operator(app_database)) as session:
        number = await issue_account_number(session, AccountType.PERSONAL, fake_clock)
    outsiders: tuple[Actor | None, ...] = (None, Actor("client", push_id()), SYSTEM)

    def as_outsider(actor: Actor | None) -> AbstractAsyncContextManager[AsyncSession]:
        return app_database.actor_transaction(actor) if actor else app_database.transaction()

    for outsider in outsiders:
        async with as_outsider(outsider) as session:
            assert not await is_issued(session, number)
            count = (await session.execute(text("SELECT count(*) FROM account_numbers"))).scalar()
            assert count == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            async with as_outsider(outsider) as session:
                await issue_account_number(session, AccountType.PERSONAL, fake_clock)
