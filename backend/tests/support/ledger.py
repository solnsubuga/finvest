"""Ledger fixtures for integration tests: throwaway accounts and funded balances.

Ledger rows cannot be deleted, by design, so tests never clean up. Every account made here has a
fresh reference, which keeps tests independent of each other and of earlier runs.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.shared.actors import SYSTEM, Actor
from app.shared.clock import Clock
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.database import TEST_SOURCE

DAY = date(2026, 11, 13)


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


def key() -> str:
    return f"test-{push_id()}"


def staff() -> Actor:
    return Actor("staff", push_id())


def client() -> Actor:
    return Actor("client", push_id())


def operator(database: Database) -> Actor:
    """Who a test acts as when it needs the whole ledger: the system in the worker, and a
    staff member in the API, where no policy trusts the system actor."""
    return SYSTEM if database.engine.url.username == "finvest_worker" else staff()


@dataclass(frozen=True, slots=True)
class Books:
    """A collections account (an asset) and one client's holding (a liability)."""

    cash: str
    holding: str
    owner: Actor


async def open_books(
    session: AsyncSession,
    clock: Clock,
    owner: Actor | None = None,
    *,
    currency: Currency = Currency.UGX,
) -> Books:
    owner = owner or client()
    cash = await ledger.open_account(
        session,
        AccountSpec(
            "test.collections", AccountKind.ASSET, currency, "Test collections", reference=push_id()
        ),
        clock,
    )
    holding = await ledger.open_account(
        session,
        AccountSpec(
            "test.client_holding",
            AccountKind.LIABILITY,
            currency,
            "Test client holding",
            owner_client_id=owner.id,
            reference=push_id(),
        ),
        clock,
    )
    return Books(cash=cash.id, holding=holding.id, owner=owner)


def deposit(books: Books, amount: int, *, idempotency_key: str | None = None) -> EntryDraft:
    """Money arrives: collections go up and so does what Finvest owes the client."""
    return EntryDraft(
        entry_type="test.deposit",
        effective_date=DAY,
        lines=(debit(books.cash, ugx(amount)), credit(books.holding, ugx(amount))),
        idempotency_key=idempotency_key or key(),
        source_type=TEST_SOURCE,
        source_id=push_id(),
    )


def payout(books: Books, amount: int, *, idempotency_key: str | None = None) -> EntryDraft:
    """Money leaves: both balances go down."""
    return EntryDraft(
        entry_type="test.payout",
        effective_date=DAY,
        lines=(debit(books.holding, ugx(amount)), credit(books.cash, ugx(amount))),
        idempotency_key=idempotency_key or key(),
        source_type=TEST_SOURCE,
        source_id=push_id(),
    )


async def funded_books(database: Database, clock: Clock, amount: int) -> Books:
    """Committed books holding `amount`."""
    async with database.actor_transaction(operator(database)) as session:
        books = await open_books(session, clock)
        if amount:
            await ledger.post_entry(session, deposit(books, amount), clock)
    return books


async def balances(database: Database, books: Books) -> tuple[int, int]:
    async with database.actor_transaction(operator(database)) as session:
        cash = await ledger.get_balance(session, books.cash)
        holding = await ledger.get_balance(session, books.holding)
    return cash.minor, holding.minor
