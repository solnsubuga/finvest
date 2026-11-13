"""Investment fixtures: a client who passed the identity check, and a payment for an investment."""

from datetime import UTC, date, datetime, timedelta

from app.infrastructure.database import Database
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity
from app.modules.investments.holdings import AWAITING_START
from app.modules.kyc import service as kyc
from app.modules.kyc.providers import ManualProvider
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.money import Money
from tests.support.clock import FakeClock
from tests.support.kyc import SEALER, decide, ready_draft
from tests.support.ledger import client as new_client
from tests.support.ledger import key, ugx


class _Present(FakeClock):
    """The real time. The database refuses an investment that is not created now, so tests
    create them now and move later clocks forward from there."""

    def now(self) -> datetime:
        return datetime.now(UTC)


PRESENT: FakeClock = _Present()


def today() -> date:
    return business_date(datetime.now(UTC))


def after(**duration: float) -> FakeClock:
    """A clock that far ahead of the real time."""
    return FakeClock(datetime.now(UTC) + timedelta(**duration))


def at(day: date, hour: int = 9) -> FakeClock:
    """A clock at `hour` UTC on `day`, which is three hours later in Kampala."""
    return FakeClock(datetime(day.year, day.month, day.day, hour, tzinfo=UTC))


async def approved_client(database: Database, clock: Clock) -> Actor:
    """A client whose identity check a member of staff approved."""
    client = new_client()
    async with database.actor_transaction(client) as session:
        await ready_draft(session, client, clock)
        check = await kyc.submit(session, SEALER, client, ManualProvider(), clock)
    await decide(database, check.id, "approve", clock)
    return client


async def request(
    database: Database,
    client: Actor,
    clock: Clock,
    *,
    amount: Money | None = None,
    product_code: str = "TREA-91",
    at_maturity: AtMaturity | None = None,
    idempotency_key: str | None = None,
) -> investments.Investment:
    async with database.actor_transaction(client) as session:
        return await investments.request_investment(
            session,
            client,
            idempotency_key=idempotency_key or key(),
            product_code=product_code,
            amount=amount or ugx(1_000_000),
            at_maturity=at_maturity,
            clock=clock,
        )


async def pay(
    worker_database: Database, investment: investments.Investment, amount: Money, clock: Clock
) -> None:
    """What the payments module does when a deposit is confirmed: credit the awaiting-start
    account for this investment."""
    async with worker_database.actor_transaction(SYSTEM) as session:
        cash = await ledger.open_account(
            session,
            AccountSpec("test.collections", AccountKind.ASSET, amount.currency, "Test collections"),
            clock,
        )
        awaiting = await ledger.open_account(
            session,
            AccountSpec(
                AWAITING_START,
                AccountKind.LIABILITY,
                amount.currency,
                "Paid in, awaiting start",
                owner_client_id=investment.client_id,
                reference=investment.id,
            ),
            clock,
        )
        await ledger.post_entry(
            session,
            EntryDraft(
                entry_type="test.deposit_confirmed",
                effective_date=business_date(clock.now()),
                lines=(debit(cash.id, amount), credit(awaiting.id, amount)),
                idempotency_key=key(),
            ),
            clock,
        )


async def started(
    app_database: Database,
    worker_database: Database,
    clock: Clock,
    *,
    amount: Money | None = None,
    product_code: str = "TREA-91",
    confirmed_on: date | None = None,
) -> investments.Investment:
    """A running investment: requested by an approved client, paid for and started."""
    client = await approved_client(app_database, clock)
    investment = await request(
        app_database, client, clock, amount=amount, product_code=product_code
    )
    await pay(worker_database, investment, investment.amount, clock)
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.start_investment(
            session,
            investment.id,
            confirmed_on=confirmed_on or today(),
            clock=clock,
        )


async def account_balance(database: Database, investment: investments.Investment, code: str) -> int:
    async with database.actor_transaction(SYSTEM) as session:
        account = await ledger.open_account(
            session,
            AccountSpec(
                code,
                AccountKind.LIABILITY,
                investment.amount.currency,
                "Paid in, awaiting start" if code == AWAITING_START else "Client holding",
                owner_client_id=investment.client_id,
                reference=investment.id,
            ),
            FakeClock(),
        )
        return (await ledger.get_balance(session, account.id)).minor
