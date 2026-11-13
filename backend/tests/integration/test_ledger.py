"""The ledger's rules, proved against real PostgreSQL as the roles the application runs under."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import (
    AccountKind,
    AccountSpec,
    EntryDraft,
    credit,
    debit,
)
from app.modules.ledger.domain.errors import (
    AccountMismatch,
    AlreadyReversed,
    IdempotencyKeyReused,
    InsufficientFunds,
    UnknownAccount,
    UnknownEntry,
)
from app.shared.actors import SYSTEM
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.ledger import (
    DAY,
    Books,
    balances,
    client,
    deposit,
    funded_books,
    key,
    open_books,
    operator,
    payout,
    staff,
    ugx,
)

pytestmark = pytest.mark.integration

INSERT_ENTRY = text(
    "INSERT INTO ledger_journal_entries (id, entry_type, currency, effective_date, posted_at, "
    "idempotency_key, request_digest, reverses_entry_id) VALUES (:id, 'test.raw', 'UGX', "
    "DATE '2026-11-13', now(), :key, repeat('0', 64), :reverses)"
)
INSERT_LINE = text(
    "INSERT INTO ledger_lines (entry_id, line_no, account_id, currency, amount_minor) "
    "VALUES (:entry, :line_no, :account, 'UGX', :amount)"
)


async def raw_entry(
    database: Database,
    lines: list[tuple[str, int]],
    *,
    reverses: str | None = None,
) -> str:
    """Insert an entry with plain SQL, as a buggy or hostile caller with the role's grants could."""
    entry = push_id()
    async with database.actor_transaction(operator(database)) as session:
        await session.execute(INSERT_ENTRY, {"id": entry, "key": key(), "reverses": reverses})
        for line_no, (account, amount) in enumerate(lines, start=1):
            await session.execute(
                INSERT_LINE,
                {"entry": entry, "line_no": line_no, "account": account, "amount": amount},
            )
    return entry


async def entry_exists(database: Database, idempotency_key: str) -> bool:
    async with database.actor_transaction(operator(database)) as session:
        return bool(
            (
                await session.execute(
                    text(
                        "SELECT EXISTS (SELECT FROM ledger_journal_entries "
                        "WHERE idempotency_key = :key)"
                    ),
                    {"key": idempotency_key},
                )
            ).scalar_one()
        )


# Posting


async def test_a_posted_entry_moves_both_balances(
    application_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(application_database, fake_clock, 0)
    async with application_database.actor_transaction(operator(application_database)) as session:
        posted = await ledger.post_entry(
            session, deposit(books, 50_000), fake_clock, correlation_id="req-1"
        )
        assert not posted.replayed
        assert posted.posted_at == fake_clock.now()
    assert await balances(application_database, books) == (50_000, 50_000)

    async with application_database.actor_transaction(operator(application_database)) as session:
        await ledger.post_entry(session, payout(books, 20_000), fake_clock)
        saved = await ledger.get_entry(session, posted.id)
    assert await balances(application_database, books) == (30_000, 30_000)
    assert saved.lines == posted.lines
    assert saved.effective_date == DAY
    assert saved.idempotency_key == posted.idempotency_key


async def test_an_entry_rolls_back_with_its_transaction(
    app_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(app_database, fake_clock, 1_000)
    draft = deposit(books, 500)
    with pytest.raises(RuntimeError, match="business change failed"):
        async with app_database.actor_transaction(operator(app_database)) as session:
            await ledger.post_entry(session, draft, fake_clock)
            raise RuntimeError("business change failed")
    assert await balances(app_database, books) == (1_000, 1_000)
    assert not await entry_exists(app_database, draft.idempotency_key)


async def test_a_many_line_entry_keeps_its_line_order(
    app_database: Database, fake_clock: FakeClock
) -> None:
    async with app_database.actor_transaction(operator(app_database)) as session:
        first = await open_books(session, fake_clock)
        second = await open_books(session, fake_clock)
        draft = EntryDraft(
            entry_type="test.split_deposit",
            effective_date=DAY,
            lines=(
                credit(second.holding, ugx(300)),
                debit(first.cash, ugx(1_000)),
                credit(first.holding, ugx(700)),
            ),
            idempotency_key=key(),
            source_type="test.payment",
            source_id=push_id(),
        )
        posted = await ledger.post_entry(session, draft, fake_clock)
    async with app_database.actor_transaction(operator(app_database)) as session:
        saved = await ledger.get_entry(session, posted.id)
        assert saved.lines == draft.lines
        assert (saved.source_type, saved.source_id) == (draft.source_type, draft.source_id)
        assert (await ledger.get_balance(session, second.holding)).minor == 300
        assert (await ledger.get_balance(session, first.holding)).minor == 700
        assert (await ledger.get_balance(session, first.cash)).minor == 1_000


async def test_dollars_and_shillings_never_share_an_entry(
    app_database: Database, fake_clock: FakeClock
) -> None:
    async with app_database.actor_transaction(operator(app_database)) as session:
        shillings = await open_books(session, fake_clock)
        dollars = await open_books(session, fake_clock, currency=Currency.USD)
        # The domain refuses a mixed entry, so claim the dollar account holds shillings.
        mislabelled = EntryDraft(
            entry_type="test.deposit",
            effective_date=DAY,
            lines=(debit(shillings.cash, ugx(100)), credit(dollars.holding, ugx(100))),
            idempotency_key=key(),
        )
        with pytest.raises(UnknownAccount):
            await ledger.post_entry(session, mislabelled, fake_clock)
        usd = Money(1_250, Currency.USD)
        await ledger.post_entry(
            session,
            EntryDraft(
                entry_type="test.deposit",
                effective_date=DAY,
                lines=(debit(dollars.cash, usd), credit(dollars.holding, usd)),
                idempotency_key=key(),
            ),
            fake_clock,
        )
        assert await ledger.get_balance(session, dollars.holding) == usd
        assert (await ledger.get_balance(session, shillings.cash)).is_zero


async def test_a_line_for_a_missing_account_is_refused(
    app_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(app_database, fake_clock, 0)
    draft = EntryDraft(
        entry_type="test.deposit",
        effective_date=DAY,
        lines=(debit(books.cash, ugx(100)), credit(push_id(), ugx(100))),
        idempotency_key=key(),
    )
    async with app_database.actor_transaction(operator(app_database)) as session:
        with pytest.raises(UnknownAccount):
            await ledger.post_entry(session, draft, fake_clock)
        with pytest.raises(UnknownAccount):
            await ledger.get_balance(session, push_id())
        with pytest.raises(UnknownEntry):
            await ledger.get_entry(session, push_id())
    assert await balances(app_database, books) == (0, 0)


# Balances never go negative


async def test_an_overdraw_is_refused_and_the_transaction_stays_usable(
    application_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(application_database, fake_clock, 1_000)
    too_much = payout(books, 1_001)
    exactly = payout(books, 1_000)
    async with application_database.actor_transaction(operator(application_database)) as session:
        with pytest.raises(InsufficientFunds):
            await ledger.post_entry(session, too_much, fake_clock)
        # The failed entry left nothing behind, and the same transaction can carry on.
        assert (await ledger.get_balance(session, books.holding)).minor == 1_000
        await ledger.post_entry(session, exactly, fake_clock)
    assert await balances(application_database, books) == (0, 0)
    assert not await entry_exists(application_database, too_much.idempotency_key)
    assert await entry_exists(application_database, exactly.idempotency_key)


async def test_the_database_refuses_a_negative_balance_without_the_service(
    application_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(application_database, fake_clock, 100)
    with pytest.raises(DBAPIError, match="ck_ledger_balances_non_negative"):
        await raw_entry(application_database, [(books.holding, 101), (books.cash, -101)])
    assert await balances(application_database, books) == (100, 100)


async def test_two_payouts_cannot_both_spend_the_same_money(
    app_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(app_database, fake_clock, 1_000)
    barrier = AsyncBarrier(2)

    async def pay() -> bool:
        async with app_database.actor_transaction(operator(app_database)) as session:
            await barrier.wait()
            try:
                await ledger.post_entry(session, payout(books, 700), fake_clock)
            except InsufficientFunds:
                return False
            return True

    results = await asyncio.gather(pay(), pay())
    assert sorted(results) == [False, True]
    assert await balances(app_database, books) == (300, 300)


async def test_many_concurrent_payouts_never_overdraw(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(app_database, fake_clock, 1_000)

    async def pay(database: Database) -> bool:
        async with database.actor_transaction(operator(database)) as session:
            try:
                await ledger.post_entry(session, payout(books, 300), fake_clock)
            except InsufficientFunds:
                return False
            return True

    results = await asyncio.gather(
        *(pay(database) for database in [app_database, worker_database] * 6)
    )
    assert results.count(True) == 3
    assert await balances(app_database, books) == (100, 100)
    async with worker_database.actor_transaction(operator(worker_database)) as session:
        assert await ledger.find_balance_drift(session) == []


async def test_entries_over_the_same_accounts_in_opposite_order_do_not_deadlock(
    app_database: Database, fake_clock: FakeClock
) -> None:
    async with app_database.actor_transaction(operator(app_database)) as session:
        books = await open_books(session, fake_clock)
        other = await open_books(session, fake_clock)
        await ledger.post_entry(session, deposit(books, 10_000), fake_clock)
        await ledger.post_entry(session, deposit(other, 10_000), fake_clock)

    def transfer(source: Books, target: Books, lines_reversed: bool) -> EntryDraft:
        lines = (debit(source.holding, ugx(10)), credit(target.holding, ugx(10)))
        return EntryDraft(
            entry_type="test.transfer",
            effective_date=DAY,
            lines=lines[::-1] if lines_reversed else lines,
            idempotency_key=key(),
        )

    async def post(index: int) -> None:
        source, target = (books, other) if index % 2 else (other, books)
        async with app_database.actor_transaction(operator(app_database)) as session:
            await ledger.post_entry(session, transfer(source, target, index % 4 < 2), fake_clock)

    await asyncio.gather(*(post(index) for index in range(40)))
    async with app_database.actor_transaction(operator(app_database)) as session:
        assert (await ledger.get_balance(session, books.holding)).minor == 10_000
        assert (await ledger.get_balance(session, other.holding)).minor == 10_000
        assert await ledger.find_balance_drift(session) == []


# Idempotency


async def test_a_retry_with_the_same_key_posts_once(
    application_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(application_database, fake_clock, 0)
    draft = deposit(books, 5_000)
    async with application_database.actor_transaction(operator(application_database)) as session:
        first = await ledger.post_entry(session, draft, fake_clock)
    fake_clock.advance(minutes=5)
    async with application_database.actor_transaction(operator(application_database)) as session:
        again = await ledger.post_entry(session, draft, fake_clock)
    assert again.replayed and not first.replayed
    assert again.id == first.id
    assert again.posted_at == first.posted_at
    assert again.lines == first.lines
    assert await balances(application_database, books) == (5_000, 5_000)


async def test_a_key_cannot_be_reused_for_a_different_entry(
    app_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(app_database, fake_clock, 0)
    draft = deposit(books, 5_000)
    async with app_database.actor_transaction(operator(app_database)) as session:
        await ledger.post_entry(session, draft, fake_clock)
    async with app_database.actor_transaction(operator(app_database)) as session:
        with pytest.raises(IdempotencyKeyReused):
            await ledger.post_entry(
                session, deposit(books, 5_001, idempotency_key=draft.idempotency_key), fake_clock
            )
        # Still usable after the refusal.
        assert (await ledger.get_balance(session, books.cash)).minor == 5_000
    assert await balances(app_database, books) == (5_000, 5_000)


async def test_two_simultaneous_requests_with_one_key_post_once(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(app_database, fake_clock, 0)
    draft = deposit(books, 9_000)
    barrier = AsyncBarrier(2)

    async def post(database: Database) -> tuple[str, bool]:
        async with database.actor_transaction(operator(database)) as session:
            await barrier.wait()
            posted = await ledger.post_entry(session, draft, fake_clock)
            return posted.id, posted.replayed

    (first_id, first_replayed), (second_id, second_replayed) = await asyncio.gather(
        post(app_database), post(worker_database)
    )
    assert first_id == second_id
    assert sorted([first_replayed, second_replayed]) == [False, True]
    assert await balances(app_database, books) == (9_000, 9_000)


# The database enforces double entry by itself


async def test_an_unbalanced_entry_cannot_be_committed(
    application_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(application_database, fake_clock, 0)
    with pytest.raises(DBAPIError, match="lines must sum to zero"):
        await raw_entry(application_database, [(books.cash, 100), (books.holding, -99)])
    assert await balances(application_database, books) == (0, 0)


@pytest.mark.parametrize("line_count", [0, 1])
async def test_an_entry_with_fewer_than_two_lines_cannot_be_committed(
    application_database: Database, fake_clock: FakeClock, line_count: int
) -> None:
    books = await funded_books(application_database, fake_clock, 0)
    with pytest.raises(DBAPIError, match="at least two lines"):
        await raw_entry(application_database, [(books.cash, 100)][:line_count])
    assert await balances(application_database, books) == (0, 0)


async def test_an_early_check_does_not_let_a_later_line_through(
    application_database: Database, fake_clock: FakeClock
) -> None:
    # SET CONSTRAINTS ... IMMEDIATE runs the deferred check on the spot. A line added after it,
    # in the same transaction, must be checked again or money appears from nothing.
    books = await funded_books(application_database, fake_clock, 0)
    other = await funded_books(application_database, fake_clock, 0)
    entry = push_id()
    with pytest.raises(DBAPIError, match="lines must sum to zero"):
        async with application_database.actor_transaction(
            operator(application_database)
        ) as session:
            await session.execute(INSERT_ENTRY, {"id": entry, "key": key(), "reverses": None})
            for line_no, (account, amount) in enumerate(
                [(books.cash, 100), (books.holding, -100)], start=1
            ):
                await session.execute(
                    INSERT_LINE,
                    {"entry": entry, "line_no": line_no, "account": account, "amount": amount},
                )
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            await session.execute(
                INSERT_LINE,
                {"entry": entry, "line_no": 3, "account": other.cash, "amount": 5_000},
            )
    assert await balances(application_database, books) == (0, 0)
    assert await balances(application_database, other) == (0, 0)


async def test_an_early_check_does_not_let_a_reversal_stop_mirroring(
    application_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(application_database, fake_clock, 1_000)
    other = await funded_books(application_database, fake_clock, 1_000)
    original = await raw_entry(application_database, [(books.cash, 300), (books.holding, -300)])
    entry = push_id()
    with pytest.raises(DBAPIError, match="must mirror the entry it reverses"):
        async with application_database.actor_transaction(
            operator(application_database)
        ) as session:
            await session.execute(INSERT_ENTRY, {"id": entry, "key": key(), "reverses": original})
            lines = [(books.cash, -300), (books.holding, 300)]
            for line_no, (account, amount) in enumerate(lines, start=1):
                await session.execute(
                    INSERT_LINE,
                    {"entry": entry, "line_no": line_no, "account": account, "amount": amount},
                )
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            # A balanced pair on other accounts: only the mirror rule can refuse it. With checks
            # immediate, the first of the pair is refused as unbalanced, so defer them again.
            await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            for line_no, (account, amount) in enumerate(
                [(other.holding, 50), (other.cash, -50)], start=3
            ):
                await session.execute(
                    INSERT_LINE,
                    {"entry": entry, "line_no": line_no, "account": account, "amount": amount},
                )
    assert await balances(application_database, books) == (1_300, 1_300)
    assert await balances(application_database, other) == (1_000, 1_000)


async def test_a_zero_line_is_refused(
    application_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(application_database, fake_clock, 0)
    with pytest.raises(DBAPIError, match="ck_ledger_lines_amount_minor"):
        await raw_entry(application_database, [(books.cash, 0), (books.holding, 0)])


async def test_lines_cannot_be_added_to_a_committed_entry(
    application_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(application_database, fake_clock, 0)
    async with application_database.actor_transaction(operator(application_database)) as session:
        other = await open_books(session, fake_clock)
    entry = await raw_entry(application_database, [(books.cash, 100), (books.holding, -100)])
    with pytest.raises(DBAPIError, match="only in the transaction that created the entry"):
        async with application_database.actor_transaction(
            operator(application_database)
        ) as session:
            # A balanced pair, so only the closed-history rule can stop it.
            await session.execute(
                INSERT_LINE, {"entry": entry, "line_no": 3, "account": other.cash, "amount": 50}
            )
            await session.execute(
                INSERT_LINE, {"entry": entry, "line_no": 4, "account": other.holding, "amount": -50}
            )
    assert await balances(application_database, other) == (0, 0)


async def test_the_creating_transaction_cannot_be_forged(
    application_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(application_database, fake_clock, 0)
    entry = push_id()
    async with application_database.actor_transaction(operator(application_database)) as session:
        await session.execute(
            text(
                "INSERT INTO ledger_journal_entries (id, entry_type, currency, effective_date, "
                "posted_at, idempotency_key, request_digest, created_xact) VALUES (:id, "
                "'test.raw', 'UGX', DATE '2026-11-13', now(), :key, repeat('0', 64), '3'::xid8)"
            ),
            {"id": entry, "key": key()},
        )
        stamped, current = (
            await session.execute(
                text(
                    "SELECT created_xact::text, pg_current_xact_id()::text "
                    "FROM ledger_journal_entries WHERE id = :id"
                ),
                {"id": entry},
            )
        ).one()
        assert stamped == current
        await session.execute(
            INSERT_LINE, {"entry": entry, "line_no": 1, "account": books.cash, "amount": 5}
        )
        await session.execute(
            INSERT_LINE, {"entry": entry, "line_no": 2, "account": books.holding, "amount": -5}
        )
    assert await balances(application_database, books) == (5, 5)


# Insert only


MUTATIONS = [
    "UPDATE ledger_lines SET amount_minor = amount_minor + 1",
    "DELETE FROM ledger_lines",
    "UPDATE ledger_journal_entries SET effective_date = effective_date + 1",
    "DELETE FROM ledger_journal_entries",
    "UPDATE ledger_accounts SET name = 'renamed'",
    "DELETE FROM ledger_accounts",
    "UPDATE ledger_balances SET balance_minor = balance_minor + 1000000",
    "DELETE FROM ledger_balances",
    "INSERT INTO ledger_balances (account_id, balance_minor) VALUES ('-Nabc0000000000000A1', 5)",
    "TRUNCATE ledger_lines",
    "TRUNCATE ledger_lines, ledger_journal_entries, ledger_balances, ledger_accounts, "
    "ledger_correction_proposals, ledger_correction_decisions",
    "ALTER TABLE ledger_lines DISABLE TRIGGER ALL",
    "DROP TABLE ledger_lines",
]


@pytest.mark.parametrize("statement", MUTATIONS)
async def test_application_roles_cannot_change_history(
    application_database: Database, fake_clock: FakeClock, statement: str
) -> None:
    books = await funded_books(application_database, fake_clock, 1_000)
    with pytest.raises(DBAPIError, match=r"permission denied|must be owner"):
        async with application_database.actor_transaction(
            operator(application_database)
        ) as session:
            await session.execute(text(statement))
    assert await balances(application_database, books) == (1_000, 1_000)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ledger_lines SET amount_minor = amount_minor + 1",
        "DELETE FROM ledger_lines",
        "UPDATE ledger_journal_entries SET effective_date = effective_date + 1",
        "DELETE FROM ledger_journal_entries",
        "UPDATE ledger_accounts SET name = 'renamed'",
        "DELETE FROM ledger_accounts",
        "DELETE FROM ledger_balances",
        "TRUNCATE ledger_lines",
        "TRUNCATE ledger_balances",
        "TRUNCATE ledger_lines, ledger_journal_entries, ledger_balances, ledger_accounts, "
        "ledger_correction_proposals, ledger_correction_decisions",
    ],
)
async def test_even_the_table_owner_cannot_change_history(
    app_database: Database, migrator_database: Database, fake_clock: FakeClock, statement: str
) -> None:
    books = await funded_books(app_database, fake_clock, 1_000)
    with pytest.raises(DBAPIError, match="is insert-only"):
        async with migrator_database.transaction() as session:
            await session.execute(text(statement))
    assert await balances(app_database, books) == (1_000, 1_000)


async def test_application_roles_hold_only_the_documented_privileges(
    migrator_database: Database,
) -> None:
    async with migrator_database.transaction() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT table_name, grantee, string_agg(privilege_type, ',' ORDER BY "
                    "privilege_type) FROM information_schema.role_table_grants "
                    "WHERE table_name LIKE 'ledger_%' AND grantee LIKE 'finvest_%' "
                    "AND grantee <> 'finvest_migrator' GROUP BY table_name, grantee "
                    "ORDER BY table_name, grantee"
                )
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("ledger_accounts", "finvest_app", "INSERT,SELECT"),
        ("ledger_accounts", "finvest_worker", "INSERT,SELECT"),
        ("ledger_balances", "finvest_app", "SELECT"),
        ("ledger_balances", "finvest_worker", "SELECT"),
        ("ledger_closed_days", "finvest_app", "INSERT,SELECT"),
        ("ledger_closed_days", "finvest_worker", "SELECT"),
        ("ledger_correction_decisions", "finvest_app", "INSERT,SELECT"),
        ("ledger_correction_decisions", "finvest_worker", "SELECT"),
        ("ledger_correction_proposals", "finvest_app", "INSERT,SELECT"),
        ("ledger_correction_proposals", "finvest_worker", "SELECT"),
        ("ledger_journal_entries", "finvest_app", "INSERT,SELECT"),
        ("ledger_journal_entries", "finvest_worker", "INSERT,SELECT"),
        ("ledger_lines", "finvest_app", "INSERT,SELECT"),
        ("ledger_lines", "finvest_worker", "INSERT,SELECT"),
    ]


# Reversals


async def test_a_reversal_undoes_an_entry_and_leaves_it_in_place(
    application_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(application_database, fake_clock, 1_000)
    async with application_database.actor_transaction(operator(application_database)) as session:
        mistake = await ledger.post_entry(session, deposit(books, 250), fake_clock)
    assert await balances(application_database, books) == (1_250, 1_250)

    async with application_database.actor_transaction(operator(application_database)) as session:
        undo = await ledger.reverse_entry(
            session, mistake.id, fake_clock, effective_date=DAY, idempotency_key=key()
        )
        original = await ledger.get_entry(session, mistake.id)
    assert undo.reverses_entry_id == mistake.id
    assert [line.amount.minor for line in undo.lines] == [-250, 250]
    assert original.lines == mistake.lines
    assert await balances(application_database, books) == (1_000, 1_000)


async def test_an_entry_is_reversed_at_most_once(
    app_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(app_database, fake_clock, 0)
    async with app_database.actor_transaction(operator(app_database)) as session:
        mistake = await ledger.post_entry(session, deposit(books, 250), fake_clock)
    undo_key = key()
    async with app_database.actor_transaction(operator(app_database)) as session:
        undo = await ledger.reverse_entry(
            session, mistake.id, fake_clock, effective_date=DAY, idempotency_key=undo_key
        )
    async with app_database.actor_transaction(operator(app_database)) as session:
        # The same request again is a replay, not a second reversal.
        again = await ledger.reverse_entry(
            session, mistake.id, fake_clock, effective_date=DAY, idempotency_key=undo_key
        )
        assert again.replayed and again.id == undo.id
        with pytest.raises(AlreadyReversed):
            await ledger.reverse_entry(
                session, mistake.id, fake_clock, effective_date=DAY, idempotency_key=key()
            )
        with pytest.raises(AlreadyReversed):
            await ledger.reverse_entry(
                session, undo.id, fake_clock, effective_date=DAY, idempotency_key=key()
            )
        with pytest.raises(UnknownEntry):
            await ledger.reverse_entry(
                session, push_id(), fake_clock, effective_date=DAY, idempotency_key=key()
            )
    assert await balances(app_database, books) == (0, 0)


async def test_a_reversal_that_would_overdraw_is_refused(
    app_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(app_database, fake_clock, 0)
    async with app_database.actor_transaction(operator(app_database)) as session:
        paid_in = await ledger.post_entry(session, deposit(books, 500), fake_clock)
        await ledger.post_entry(session, payout(books, 400), fake_clock)
    async with app_database.actor_transaction(operator(app_database)) as session:
        with pytest.raises(InsufficientFunds):
            await ledger.reverse_entry(
                session, paid_in.id, fake_clock, effective_date=DAY, idempotency_key=key()
            )
    assert await balances(app_database, books) == (100, 100)


async def test_the_database_refuses_a_reversal_that_does_not_mirror(
    application_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(application_database, fake_clock, 1_000)
    original = await raw_entry(application_database, [(books.cash, 300), (books.holding, -300)])
    with pytest.raises(DBAPIError, match="must mirror the entry it reverses"):
        await raw_entry(
            application_database, [(books.cash, -200), (books.holding, 200)], reverses=original
        )
    async with application_database.actor_transaction(operator(application_database)) as session:
        other = await open_books(session, fake_clock)
        await ledger.post_entry(session, deposit(other, 1_000), fake_clock)
    with pytest.raises(DBAPIError, match="must mirror the entry it reverses"):
        await raw_entry(
            application_database, [(other.cash, -300), (other.holding, 300)], reverses=original
        )
    undo = await raw_entry(
        application_database, [(books.holding, 300), (books.cash, -300)], reverses=original
    )
    with pytest.raises(DBAPIError, match="cannot itself be reversed"):
        await raw_entry(
            application_database, [(books.cash, 300), (books.holding, -300)], reverses=undo
        )
    with pytest.raises(DBAPIError, match="uq_ledger_journal_entries_reverses_entry_id"):
        await raw_entry(
            application_database, [(books.holding, 300), (books.cash, -300)], reverses=original
        )
    assert await balances(application_database, books) == (1_000, 1_000)


# Accounts


async def test_opening_an_account_twice_gives_the_same_account(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    spec = AccountSpec(
        "test.payout_pending",
        AccountKind.LIABILITY,
        Currency.UGX,
        "Payout pending",
        owner_client_id=push_id(),
    )
    barrier = AsyncBarrier(2)

    async def open_it(database: Database) -> str:
        async with database.actor_transaction(operator(database)) as session:
            await barrier.wait()
            return (await ledger.open_account(session, spec, fake_clock)).id

    first, second = await asyncio.gather(open_it(app_database), open_it(worker_database))
    assert first == second
    async with app_database.actor_transaction(operator(app_database)) as session:
        account = await ledger.open_account(session, spec, fake_clock)
        assert account.id == first
        assert account.kind is AccountKind.LIABILITY
        assert (await ledger.get_balance(session, first)).is_zero
        clash = AccountSpec(
            spec.code, AccountKind.ASSET, spec.currency, spec.name, spec.owner_client_id
        )
        with pytest.raises(AccountMismatch):
            await ledger.open_account(session, clash, fake_clock)


async def test_one_code_serves_many_owners_currencies_and_references(
    app_database: Database, fake_clock: FakeClock
) -> None:
    owner, reference = push_id(), push_id()
    specs = [
        AccountSpec("test.holding", AccountKind.LIABILITY, Currency.UGX, "Holding", owner),
        AccountSpec("test.holding", AccountKind.LIABILITY, Currency.USD, "Holding", owner),
        AccountSpec("test.holding", AccountKind.LIABILITY, Currency.UGX, "Holding", push_id()),
        AccountSpec(
            "test.holding", AccountKind.LIABILITY, Currency.UGX, "Holding", owner, reference
        ),
    ]
    async with app_database.actor_transaction(operator(app_database)) as session:
        ids = [(await ledger.open_account(session, spec, fake_clock)).id for spec in specs]
    assert len(set(ids)) == 4


# Clients are fenced


async def test_a_client_sees_only_their_own_money(
    app_database: Database, fake_clock: FakeClock
) -> None:
    alice, bob = client(), client()
    async with app_database.actor_transaction(operator(app_database)) as session:
        hers = await open_books(session, fake_clock, alice)
        his = await open_books(session, fake_clock, bob)
        her_entry = await ledger.post_entry(session, deposit(hers, 40_000), fake_clock)
        his_entry = await ledger.post_entry(session, deposit(his, 70_000), fake_clock)

    async with app_database.actor_transaction(alice) as session:
        assert (await ledger.get_balance(session, hers.holding)).minor == 40_000
        with pytest.raises(UnknownAccount):
            await ledger.get_balance(session, his.holding)
        # The collections account is Finvest's, not hers.
        with pytest.raises(UnknownAccount):
            await ledger.get_balance(session, hers.cash)
        with pytest.raises(UnknownEntry):
            await ledger.get_entry(session, his_entry.id)
        # She sees her entry, and only her side of it.
        seen = await ledger.get_entry(session, her_entry.id)
        assert [(line.account_id, line.amount.minor) for line in seen.lines] == [
            (hers.holding, -40_000)
        ]
        for table, expected in (
            ("ledger_accounts", 1),
            ("ledger_balances", 1),
            ("ledger_lines", 1),
            ("ledger_journal_entries", 1),
        ):
            count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
            assert count == expected, table


async def test_a_transaction_with_no_actor_sees_and_posts_nothing(
    app_database: Database, fake_clock: FakeClock
) -> None:
    books = await funded_books(app_database, fake_clock, 1_000)
    async with app_database.transaction() as session:
        for table in (
            "ledger_accounts",
            "ledger_balances",
            "ledger_lines",
            "ledger_journal_entries",
        ):
            count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
            assert count == 0, table
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.transaction() as session:
            await session.execute(INSERT_ENTRY, {"id": push_id(), "key": key(), "reverses": None})
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.transaction() as session:
            await ledger.open_account(
                session,
                AccountSpec("test.orphan", AccountKind.ASSET, Currency.UGX, "Orphan"),
                fake_clock,
            )
    assert await balances(app_database, books) == (1_000, 1_000)


async def test_the_api_is_never_trusted_as_the_system(
    app_database: Database, fake_clock: FakeClock
) -> None:
    # The actor is set by the connection itself, so the API could claim to be the system.
    # Claiming it must gain nothing.
    books = await funded_books(app_database, fake_clock, 1_000)
    async with app_database.actor_transaction(SYSTEM) as session:
        with pytest.raises(UnknownAccount):
            await ledger.get_balance(session, books.cash)
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_database.actor_transaction(SYSTEM) as session:
            await ledger.post_entry(session, payout(books, 1_000), fake_clock)
    assert await balances(app_database, books) == (1_000, 1_000)


async def test_staff_see_the_whole_ledger(app_database: Database, fake_clock: FakeClock) -> None:
    books = await funded_books(app_database, fake_clock, 1_000)
    async with app_database.actor_transaction(staff()) as session:
        assert (await ledger.get_balance(session, books.cash)).minor == 1_000
        assert (await ledger.get_balance(session, books.holding)).minor == 1_000
        assert await ledger.find_balance_drift(session) == []


async def test_a_client_cannot_open_an_account_for_someone_else(
    app_database: Database, fake_clock: FakeClock
) -> None:
    alice, bob = client(), client()
    async with app_database.actor_transaction(alice) as session:
        own = await ledger.open_account(
            session,
            AccountSpec("test.holding", AccountKind.LIABILITY, Currency.UGX, "Holding", alice.id),
            fake_clock,
        )
        assert own.owner_client_id == alice.id
    for owner in (bob.id, None):
        with pytest.raises(DBAPIError, match="row-level security"):
            async with app_database.actor_transaction(alice) as session:
                await ledger.open_account(
                    session,
                    AccountSpec(
                        "test.holding",
                        AccountKind.LIABILITY,
                        Currency.UGX,
                        "Holding",
                        owner,
                        push_id(),
                    ),
                    fake_clock,
                )


async def test_a_client_request_can_post_and_replay_its_own_entry(
    app_database: Database, fake_clock: FakeClock
) -> None:
    alice = client()
    async with app_database.actor_transaction(operator(app_database)) as session:
        books = await open_books(session, fake_clock, alice)
        await ledger.post_entry(session, deposit(books, 10_000), fake_clock)
    draft = payout(books, 4_000)
    async with app_database.actor_transaction(alice) as session:
        posted = await ledger.post_entry(session, draft, fake_clock)
    async with app_database.actor_transaction(alice) as session:
        again = await ledger.post_entry(session, draft, fake_clock)
        assert again.replayed and again.id == posted.id
        assert (await ledger.get_balance(session, books.holding)).minor == 6_000
    # Another client cannot learn anything from her key: it is simply refused.
    async with app_database.actor_transaction(client()) as session:
        with pytest.raises(IdempotencyKeyReused):
            await ledger.post_entry(session, draft, fake_clock)
    assert await balances(app_database, books) == (6_000, 6_000)


async def test_a_client_command_can_post_against_a_system_account_it_cannot_read(
    app_database: Database, fake_clock: FakeClock
) -> None:
    code = f"test.payouts_in_transit_{push_id().lower().replace('-', 'x').replace('_', 'y')}"
    alice = client()
    async with app_database.actor_transaction(staff()) as session:
        system = await ledger.open_account(
            session,
            AccountSpec(code, AccountKind.LIABILITY, Currency.UGX, "Payouts in transit"),
            fake_clock,
        )
        # Same code, but a client's account and a referenced one: neither is the system account.
        await ledger.open_account(
            session,
            AccountSpec(code, AccountKind.LIABILITY, Currency.UGX, "Hers", alice.id),
            fake_clock,
        )
        await ledger.open_account(
            session,
            AccountSpec(code, AccountKind.LIABILITY, Currency.UGX, "Other", reference=push_id()),
            fake_clock,
        )
        books = await open_books(session, fake_clock, alice)
        await ledger.post_entry(session, deposit(books, 10_000), fake_clock)

    async with app_database.actor_transaction(alice) as session:
        found = await ledger.system_account_id(session, code, Currency.UGX)
        assert found == system.id
        with pytest.raises(UnknownAccount):
            await ledger.get_balance(session, found)
        with pytest.raises(UnknownAccount):
            await ledger.system_account_id(session, code, Currency.USD)
        with pytest.raises(UnknownAccount):
            await ledger.system_account_id(session, "test.no_such_account", Currency.UGX)
        await ledger.post_entry(
            session,
            EntryDraft(
                entry_type="test.payout_requested",
                effective_date=DAY,
                lines=(debit(books.holding, ugx(4_000)), credit(found, ugx(4_000))),
                idempotency_key=key(),
            ),
            fake_clock,
        )
    async with app_database.actor_transaction(staff()) as session:
        assert (await ledger.get_balance(session, system.id)).minor == 4_000
        assert (await ledger.get_balance(session, books.holding)).minor == 6_000
    # With no actor at all the lookup still gives only an ID.
    async with app_database.transaction() as session:
        assert await ledger.system_account_id(session, code, Currency.UGX) == system.id


# The whole ledger


async def test_the_ledger_always_sums_to_zero_and_matches_its_balances(
    worker_database: Database, fake_clock: FakeClock
) -> None:
    # Runs against everything every other test in this session has committed.
    await funded_books(worker_database, fake_clock, 123)
    async with worker_database.actor_transaction(operator(worker_database)) as session:
        totals = (
            await session.execute(
                text("SELECT currency, sum(amount_minor) FROM ledger_lines GROUP BY currency")
            )
        ).all()
        assert totals and all(total == 0 for _, total in totals)
        unbalanced = (
            await session.execute(
                text(
                    "SELECT count(*) FROM (SELECT entry_id FROM ledger_lines GROUP BY entry_id "
                    "HAVING sum(amount_minor) <> 0 OR count(*) < 2) AS broken"
                )
            )
        ).scalar_one()
        assert unbalanced == 0
        negative = (
            await session.execute(
                text("SELECT count(*) FROM ledger_balances WHERE balance_minor < 0")
            )
        ).scalar_one()
        assert negative == 0
        assert await ledger.find_balance_drift(session) == []
