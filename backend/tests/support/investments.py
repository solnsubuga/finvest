"""Investment fixtures: a client who passed the identity check, and a payment for an investment."""

import secrets
from datetime import UTC, date, datetime, timedelta

from app.infrastructure.database import Database
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity
from app.modules.investments.domain.products import InterestPaid, ProductKind
from app.modules.investments.holdings import AWAITING_START
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.payouts import destinations
from app.modules.payouts.domain.destinations import MobileMoneyNumber
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.money import Currency, Money
from tests.support.clock import FakeClock
from tests.support.kyc import verify
from tests.support.ledger import client as new_client
from tests.support.ledger import key, ugx
from tests.support.ledger import staff as new_staff
from tests.support.payouts import approve_by_two, name_from_network


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


def days_on(days: int, hour: int = 9) -> FakeClock:
    """A clock that many days after today in Kampala, at `hour` UTC. Today is read when this is
    called, not when a test module is imported, so a run that crosses midnight stays right."""
    return at(today() + timedelta(days=days), hour)


def at(day: date, hour: int = 9) -> FakeClock:
    """A clock at `hour` UTC on `day`, which is three hours later in Kampala."""
    return FakeClock(datetime(day.year, day.month, day.day, hour, tzinfo=UTC))


async def approved_client(database: Database, clock: Clock) -> Actor:
    """A client whose identity check a member of staff approved."""
    client = new_client()
    await verify(database, client, clock)
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
    destination_id: str | None = None,
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
            destination_id=destination_id,
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


async def approved_destination(
    app_database: Database, client: Actor, clock: Clock, *, number: str | None = None
) -> str:
    """A mobile money number of the client's that staff approved. Returns its ID.

    Staff approve by the real time, so pass a clock at or near now. A number may belong to
    one client only, so each call takes a fresh one unless told otherwise.
    """
    number = number or f"0772{secrets.randbelow(1_000_000):06d}"
    async with app_database.actor_transaction(client) as session:
        view = await destinations.add_destination(
            session, client, MobileMoneyNumber.parse(number), "Sarah Nakato", clock
        )
        await destinations.confirm_code(session, client, view.id, clock)
    await name_from_network(app_database, view.id, clock)
    await approve_by_two(app_database, view.id, clock)
    return view.id


# Earlier than any clock a test sets, so the product and its first rate are in force for all.
_LONG_AGO = FakeClock(datetime(2026, 1, 1, 9, tzinfo=UTC))


async def draft_bill(database: Database, *, term_days: int = 182) -> str:
    """A draft Treasury bill of its own for a dealing test, made by two people. Its code.

    Tests share one database and never remove what they buy, so anything read per product,
    such as the latest buy or the bills free to allocate, is a test's own only on a product of
    its own. A draft takes no client investments and is not in the live catalogue.
    """
    code = f"DEAL-{secrets.token_hex(4).upper()}"
    product = investments.NewProduct(
        code=code,
        name=f"{term_days} Day Treasury Bill",
        kind=ProductKind.TREASURY_BILL,
        currency=Currency.UGX,
        term_days=term_days,
        minimum_minor=500_000,
        interest_paid=InterestPaid.AT_MATURITY,
        annual_rate_bps=1500,
        tax_bps=1500,
    )
    maker, checker = new_staff(), new_staff()
    async with database.actor_transaction(maker) as session:
        change = await investments.propose_new_product(
            session, maker, product, "A bill for dealing tests", _LONG_AGO
        )
    async with database.actor_transaction(checker) as session:
        await investments.approve_product_change(session, checker, change.id, _LONG_AGO)
    return code
