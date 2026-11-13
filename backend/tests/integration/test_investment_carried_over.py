"""Investments carried over from Finvest 1.0 at the switch, against real PostgreSQL: the
opening entry, the interest to date by the new formula, the daily run carrying on, the
reference opening each one once, and the fence that only the worker opens one."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.investments import accrual
from app.modules.investments import service as investments
from app.modules.investments.domain.interest import Terms, accrued, day_accrual, days_earned
from app.modules.investments.domain.investments import AtMaturity, InvestmentStatus
from app.modules.investments.holdings import HOLDING
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec
from app.shared.actors import SYSTEM, Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.investments import PRESENT, account_balance, at, today
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration

DAY = today()
STARTED = DAY - timedelta(days=30)
AS_OF = DAY - timedelta(days=1)
PRINCIPAL = ugx(1_000_000)
TERMS = Terms(PRINCIPAL, annual_rate_bps=1300, tax_bps=1500)


def opening_balances(currency: Currency = Currency.UGX) -> AccountSpec:
    return AccountSpec(
        "migration.opening_balances",
        AccountKind.ASSET,
        currency,
        "Finvest 1.0 balances carried over",
    )


def reference() -> str:
    return f"FV1-INV-{push_id()[-8:].replace('-', 'x').replace('_', 'y')}"


async def carry_over(
    worker_database: Database,
    legacy_reference: str,
    *,
    client_id: str | None = None,
    principal: Money = PRINCIPAL,
    annual_rate_bps: int = 1300,
    interest_through: object = AS_OF,
    product_code: str = "TREA-91",
    actor: Actor = SYSTEM,
    clock: Clock = PRESENT,
) -> investments.Investment:
    async with worker_database.actor_transaction(actor) as session:
        return await investments.open_existing_investment(
            session,
            actor,
            client_id=client_id or push_id(),
            product_code=product_code,
            principal=principal,
            start_date=STARTED,
            annual_rate_bps=annual_rate_bps,
            tax_bps=1500,
            interest_through=interest_through,  # type: ignore[arg-type]
            opened_on=DAY,
            legacy_reference=legacy_reference,
            opening_account=opening_balances(),
            clock=clock,
        )


async def balance(worker_database: Database, spec: AccountSpec) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(session, spec, FakeClock())
        return (await ledger.get_balance(session, account.id)).minor


async def entries_for(worker_database: Database, investment_id: str) -> int:
    async with worker_database.actor_transaction(SYSTEM) as session:
        return int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM ledger_journal_entries "
                        "WHERE source_type = 'investment' AND source_id = :id "
                        "AND entry_type = 'investment.carried_over'"
                    ),
                    {"id": investment_id},
                )
            ).scalar_one()
        )


async def test_an_investment_is_carried_over_running_with_its_interest_to_date(
    worker_database: Database,
) -> None:
    before = await balance(worker_database, opening_balances())
    investment = await carry_over(worker_database, reference())

    # The new system's own formula, from the start to the "as of" day.
    earned = accrued(TERMS, days_earned(STARTED, AS_OF, 91))
    assert earned.net.is_positive
    assert investment.status is InvestmentStatus.ACTIVE
    assert (investment.start_date, investment.maturity_date) == (
        STARTED,
        STARTED + timedelta(days=91),
    )
    assert investment.at_maturity is AtMaturity.ROLL_OVER
    assert (investment.terms.annual_rate_bps, investment.terms.tax_bps) == (1300, 1500)
    assert (investment.accrued_through, investment.accrued_gross, investment.accrued_tax) == (
        AS_OF,
        earned.gross,
        earned.tax,
    )
    assert await account_balance(worker_database, investment, HOLDING) == 1_000_000
    assert await balance(worker_database, accrual.interest_account(investment)) == earned.net.minor
    # The one debit is everything owed plus the tax held for the client.
    moved = abs(await balance(worker_database, opening_balances()) - before)
    assert moved == 1_000_000 + earned.gross.minor
    assert await entries_for(worker_database, investment.id) == 1


async def test_the_daily_run_carries_on_from_the_next_day(worker_database: Database) -> None:
    investment = await carry_over(worker_database, reference())
    days = days_earned(STARTED, AS_OF, 91)

    async with worker_database.actor_transaction(SYSTEM) as session:
        posted = await accrual.accrue_investment(session, investment.id, DAY, at(DAY, 12))
        again = await accrual.accrue_investment(session, investment.id, DAY, at(DAY, 12))
        after = await investments.get_investment(session, investment.id)

    # One more day, never the days already carried over.
    assert posted == day_accrual(TERMS, days + 1)
    assert again is None
    assert after.accrued_gross == accrued(TERMS, days + 1).gross


async def test_the_same_reference_opens_one_investment(worker_database: Database) -> None:
    legacy = reference()
    client_id = push_id()
    first = await carry_over(worker_database, legacy, client_id=client_id)
    again = await carry_over(worker_database, legacy, client_id=client_id)

    assert again.id == first.id
    assert await entries_for(worker_database, first.id) == 1
    # The same reference with other terms is a mistake, not a second investment.
    with pytest.raises(ApplicationError) as refused:
        await carry_over(worker_database, legacy, client_id=client_id, annual_rate_bps=1200)
    assert refused.value.code == "conflict"
    # So is another "as of" day: the first call does not silently win.
    with pytest.raises(ApplicationError) as refused:
        await carry_over(
            worker_database, legacy, client_id=client_id, interest_through=AS_OF - timedelta(days=1)
        )
    assert refused.value.code == "conflict"


async def test_two_openings_at_once_open_once(worker_database: Database) -> None:
    legacy = reference()
    client_id = push_id()
    barrier = AsyncBarrier(2)

    async def one() -> str:
        async with worker_database.actor_transaction(SYSTEM) as session:
            await barrier.wait()
            investment = await investments.open_existing_investment(
                session,
                SYSTEM,
                client_id=client_id,
                product_code="TREA-91",
                principal=PRINCIPAL,
                start_date=STARTED,
                annual_rate_bps=1300,
                tax_bps=1500,
                interest_through=AS_OF,
                opened_on=DAY,
                legacy_reference=legacy,
                opening_account=opening_balances(),
                clock=PRESENT,
            )
            return investment.id

    first, second = await asyncio.gather(one(), one())
    assert first == second
    assert await entries_for(worker_database, first) == 1


@pytest.mark.parametrize(
    ("interest_through", "code"),
    [
        (DAY, "invalid"),
        (STARTED - timedelta(days=1), "invalid"),
    ],
)
async def test_interest_is_carried_over_only_for_days_already_earned(
    worker_database: Database, interest_through: object, code: str
) -> None:
    with pytest.raises(ApplicationError) as refused:
        await carry_over(worker_database, reference(), interest_through=interest_through)
    assert refused.value.code == "validation_failed"
    assert refused.value.fields[0].code == code


async def test_a_term_that_has_ended_is_not_carried_over(worker_database: Database) -> None:
    with pytest.raises(ApplicationError) as refused:
        await carry_over(
            worker_database,
            reference(),
            product_code="TREA-91",
            interest_through=STARTED + timedelta(days=91),
            clock=FakeClock(PRESENT.now() + timedelta(days=90)),
        )
    assert refused.value.code == "validation_failed"


async def test_only_the_worker_carries_an_investment_over(
    app_database: Database, worker_database: Database
) -> None:
    staff = Actor("staff", push_id())
    with pytest.raises(ApplicationError) as refused:
        async with app_database.actor_transaction(staff) as session:
            await investments.open_existing_investment(
                session,
                staff,
                client_id=push_id(),
                product_code="TREA-91",
                principal=PRINCIPAL,
                start_date=STARTED,
                annual_rate_bps=1300,
                tax_bps=1500,
                interest_through=AS_OF,
                opened_on=DAY,
                legacy_reference=reference(),
                opening_account=opening_balances(),
                clock=PRESENT,
            )
    assert refused.value.code == "forbidden"

    # The database holds the API to it too, whatever the code says.
    investment = await carry_over(worker_database, reference())
    client = Actor("client", investment.client_id)
    with pytest.raises(DBAPIError, match="only the switch carries over"):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text(
                    "INSERT INTO investments (id, client_id, product_id, currency, "
                    "principal_minor, annual_rate_bps, tax_bps, term_days, at_maturity, status, "
                    "created_at, pay_by, start_date, maturity_date, started_at, start_entry_id, "
                    "accrued_through, accrued_gross_minor, accrued_tax_minor, legacy_reference, "
                    "updated_at) SELECT :id, client_id, product_id, currency, principal_minor, "
                    "annual_rate_bps, tax_bps, term_days, at_maturity, status, created_at, "
                    "pay_by, start_date, maturity_date, started_at, :entry, accrued_through, "
                    "accrued_gross_minor, accrued_tax_minor, :reference, updated_at "
                    "FROM investments WHERE id = :from"
                ),
                {
                    "id": push_id(),
                    "entry": push_id(),
                    "reference": reference(),
                    "from": investment.id,
                },
            )
    # Nor does the reference ever change afterwards.
    with pytest.raises(DBAPIError, match=r"never change|permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE investments SET legacy_reference = :reference WHERE id = :id"),
                {"reference": reference(), "id": investment.id},
            )
