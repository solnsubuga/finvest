"""Reading an account's history for reconciliation."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.infrastructure.database import Database
from app.modules.ledger import service as ledger
from app.modules.ledger import statements
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.ledger.domain.errors import MixedAccountKinds, UnknownAccount
from app.shared.actors import SYSTEM
from app.shared.money import Currency
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TEST_SOURCE
from tests.support.ledger import (
    DAY,
    client,
    deposit,
    key,
    open_books,
    operator,
    payout,
    staff,
    ugx,
)

pytestmark = pytest.mark.integration

NEXT_DAY = DAY + timedelta(days=1)
LATER = DAY + timedelta(days=2)


async def test_movements_list_each_line_in_the_range_with_its_source(
    application_database: Database, fake_clock: FakeClock
) -> None:
    actor = operator(application_database)
    payment, payout_id = push_id(), push_id()
    async with application_database.actor_transaction(actor) as session:
        books = await open_books(session, fake_clock)
        first = await ledger.post_entry(
            session,
            replace(deposit(books, 1_000), source_id=payment),
            fake_clock,
        )
        second = await ledger.post_entry(
            session,
            replace(
                payout(books, 300),
                effective_date=NEXT_DAY,
                source_type="payout",  # one of the types staff may post
                source_id=payout_id,
            ),
            fake_clock,
        )
        await ledger.post_entry(
            session, replace(deposit(books, 50), effective_date=LATER), fake_clock
        )
    async with application_database.actor_transaction(actor) as session:
        cash = await statements.account_movements(
            session, books.cash, date_from=DAY, date_to=NEXT_DAY
        )
        holding = await statements.account_movements(
            session, books.holding, date_from=NEXT_DAY, date_to=NEXT_DAY
        )
        nothing = await statements.account_movements(
            session, books.cash, date_from=date(2020, 1, 1), date_to=date(2020, 12, 31)
        )
    assert [
        (m.entry_id, m.effective_date, m.amount, m.entry_type, m.source_type, m.source_id)
        for m in cash
    ] == [
        (first.id, DAY, ugx(1_000), "test.deposit", TEST_SOURCE, payment),
        (second.id, NEXT_DAY, ugx(-300), "test.payout", "payout", payout_id),
    ]
    assert all(m.posted_at == fake_clock.now() and m.line_no in (1, 2) for m in cash)
    # The client's holding is a liability: paying out debits it, so the line is positive.
    assert [(m.entry_id, m.amount) for m in holding] == [(second.id, ugx(300))]
    assert nothing == []


async def test_the_balance_as_of_a_day_counts_only_entries_dated_up_to_it(
    application_database: Database, fake_clock: FakeClock
) -> None:
    actor = operator(application_database)
    async with application_database.actor_transaction(actor) as session:
        books = await open_books(session, fake_clock)
        await ledger.post_entry(session, deposit(books, 1_000), fake_clock)
        # Posted now, but dated two days on.
        await ledger.post_entry(
            session, replace(deposit(books, 50), effective_date=LATER), fake_clock
        )
        await ledger.post_entry(
            session, replace(payout(books, 300), effective_date=NEXT_DAY), fake_clock
        )
    async with application_database.actor_transaction(actor) as session:
        figures = [
            (
                (await statements.balance_as_of(session, books.cash, on)).minor,
                (await statements.balance_as_of(session, books.holding, on)).minor,
            )
            for on in (DAY - timedelta(days=1), DAY, NEXT_DAY, LATER, LATER + timedelta(days=30))
        ]
        today = await ledger.get_balance(session, books.cash)
    assert figures == [(0, 0), (1_000, 1_000), (700, 700), (750, 750), (750, 750)]
    assert today == ugx(750)


async def test_the_owner_of_an_account_is_the_client_or_nobody(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    owner = client()
    async with app_database.actor_transaction(staff()) as session:
        books = await open_books(session, fake_clock, owner)
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await statements.account_owner(session, books.holding) == owner.id
        assert await statements.account_owner(session, books.cash) is None
        with pytest.raises(UnknownAccount):
            await statements.account_owner(session, push_id())


async def test_a_client_reads_only_their_own_account(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner, stranger = client(), client()
    async with app_database.actor_transaction(staff()) as session:
        books = await open_books(session, fake_clock, owner)
        await ledger.post_entry(session, deposit(books, 400), fake_clock)
    async with app_database.actor_transaction(owner) as session:
        assert await statements.balance_as_of(session, books.holding, DAY) == ugx(400)
        assert (
            len(
                await statements.account_movements(
                    session, books.holding, date_from=DAY, date_to=DAY
                )
            )
            == 1
        )
        assert await statements.account_owner(session, books.holding) == owner.id
        with pytest.raises(UnknownAccount):
            await statements.balance_as_of(session, books.cash, DAY)
    async with app_database.actor_transaction(stranger) as session:
        for account in (books.holding, books.cash):
            with pytest.raises(UnknownAccount):
                await statements.account_movements(session, account, date_from=DAY, date_to=DAY)
            with pytest.raises(UnknownAccount):
                await statements.balance_as_of(session, account, DAY)
            with pytest.raises(UnknownAccount):
                await statements.account_owner(session, account)


def fresh_code() -> str:
    """A code no other test uses, since the test database keeps every account."""
    return "test.total_" + push_id().lower().replace("-", "x").replace("_", "y")


async def test_the_total_balance_adds_every_account_with_the_code(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    code, owner = fresh_code(), client()
    async with app_database.actor_transaction(staff()) as session:
        books = await open_books(session, fake_clock, owner)
        for amount, holder in ((400, owner), (250, client())):
            account = await ledger.open_account(
                session,
                AccountSpec(
                    code,
                    AccountKind.LIABILITY,
                    Currency.UGX,
                    "Test pending",
                    owner_client_id=holder.id,
                    reference=push_id(),
                ),
                fake_clock,
            )
            await ledger.post_entry(
                session,
                EntryDraft(
                    entry_type="test.deposit",
                    effective_date=DAY,
                    lines=(debit(books.cash, ugx(amount)), credit(account.id, ugx(amount))),
                    idempotency_key=key(),
                    source_type=TEST_SOURCE,
                    source_id=push_id(),
                ),
                fake_clock,
            )
        staff_total = await statements.total_balance(session, code, Currency.UGX)
        dollars = await statements.total_balance(session, code, Currency.USD)
        nothing = await statements.total_balance(session, fresh_code(), Currency.UGX)
    async with worker_database.actor_transaction(SYSTEM) as session:
        worker_total = await statements.total_balance(session, code, Currency.UGX)
    async with app_database.actor_transaction(owner) as session:
        own_total = await statements.total_balance(session, code, Currency.UGX)
    assert (staff_total, worker_total, own_total) == (ugx(650), ugx(650), ugx(400))
    assert dollars.minor == nothing.minor == 0
    assert dollars.currency is Currency.USD


async def test_the_total_balance_refuses_a_code_of_two_kinds(
    app_database: Database, fake_clock: FakeClock
) -> None:
    code = fresh_code()
    async with app_database.actor_transaction(staff()) as session:
        for kind in (AccountKind.ASSET, AccountKind.LIABILITY):
            await ledger.open_account(
                session,
                AccountSpec(code, kind, Currency.UGX, "Test mixed", reference=push_id()),
                fake_clock,
            )
        with pytest.raises(MixedAccountKinds):
            await statements.total_balance(session, code, Currency.UGX)
