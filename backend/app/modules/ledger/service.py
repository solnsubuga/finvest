"""Posting to and reading from the ledger, inside the caller's transaction.

Every function takes the caller's open session, so a journal entry commits or rolls back with
the business change that caused it. Nothing here commits, and nothing here calls a provider.

The database is the authority: it refuses an unbalanced entry, a negative balance, a change to
history and a second use of an idempotency key (migration 0010). This module turns those
refusals into typed errors and keeps the caller's transaction usable after one.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ledger.domain.entries import (
    AccountKind,
    AccountSpec,
    EntryDraft,
    Line,
    reversal,
    sorted_lines,
)
from app.modules.ledger.domain.errors import (
    AccountMismatch,
    AlreadyReversed,
    DayClosed,
    IdempotencyKeyReused,
    InsufficientFunds,
    StaffSourceNotAllowed,
    UnknownAccount,
    UnknownEntry,
)
from app.shared.clock import Clock
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    code: str
    kind: AccountKind
    currency: Currency
    name: str
    owner_client_id: str | None
    reference: str | None


@dataclass(frozen=True, slots=True)
class PostedEntry:
    id: str
    entry_type: str
    currency: Currency
    effective_date: date
    posted_at: datetime
    idempotency_key: str
    source_type: str | None
    source_id: str | None
    reverses_entry_id: str | None
    lines: tuple[Line, ...]
    # True when the idempotency key had already been used for this same entry: nothing was
    # posted by this call, and the saved entry is returned.
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class BalanceDrift:
    """An account whose cached balance disagrees with the sum of its lines. Must never exist."""

    account_id: str
    cached_minor: int
    computed_minor: int


_ACCOUNT_COLUMNS = "id, code, kind, currency, name, owner_client_id, reference"
_INSERT_ACCOUNT = text(
    "INSERT INTO ledger_accounts "
    "(id, code, kind, currency, name, owner_client_id, reference, created_at) "
    "VALUES (:id, :code, :kind, :currency, :name, :owner_client_id, :reference, :created_at) "
    "ON CONFLICT ON CONSTRAINT uq_ledger_accounts_identity DO NOTHING"
)
_SELECT_ACCOUNT = text(
    f"SELECT {_ACCOUNT_COLUMNS} FROM ledger_accounts "  # noqa: S608 - constant column list
    "WHERE code = :code AND currency = :currency "
    "AND owner_client_id IS NOT DISTINCT FROM :owner_client_id "
    "AND reference IS NOT DISTINCT FROM :reference"
)
_SELECT_SYSTEM_ACCOUNT = text("SELECT finvest_ledger_system_account(:code, :currency)")
_INSERT_ENTRY = text(
    "INSERT INTO ledger_journal_entries (id, entry_type, currency, effective_date, posted_at, "
    "idempotency_key, request_digest, source_type, source_id, reverses_entry_id, correlation_id) "
    "VALUES (:id, :entry_type, :currency, :effective_date, :posted_at, :idempotency_key, "
    ":request_digest, :source_type, :source_id, :reverses_entry_id, :correlation_id)"
)
_INSERT_LINE = text(
    "INSERT INTO ledger_lines (entry_id, line_no, account_id, currency, amount_minor) "
    "VALUES (:entry_id, :line_no, :account_id, :currency, :amount_minor)"
)
_ENTRY_COLUMNS = (
    "id, entry_type, currency, effective_date, posted_at, idempotency_key, request_digest, "
    "source_type, source_id, reverses_entry_id"
)
_SELECT_ENTRY_BY_KEY = text(
    f"SELECT {_ENTRY_COLUMNS} FROM ledger_journal_entries "  # noqa: S608 - constant column list
    "WHERE idempotency_key = :idempotency_key"
)
_SELECT_ENTRY_BY_ID = text(
    f"SELECT {_ENTRY_COLUMNS} FROM ledger_journal_entries WHERE id = :id"  # noqa: S608
)
_SELECT_LINES = text(
    "SELECT account_id, amount_minor FROM ledger_lines WHERE entry_id = :entry_id ORDER BY line_no"
)
_SELECT_BALANCE = text(
    "SELECT balance.balance_minor, account.currency FROM ledger_balances AS balance "
    "JOIN ledger_accounts AS account ON account.id = balance.account_id "
    "WHERE balance.account_id = :account_id"
)
_SELECT_DRIFT = text(
    "SELECT account.id, balance.balance_minor AS cached, "
    "coalesce(sum(CASE WHEN account.kind IN ('asset', 'expense') "
    "THEN line.amount_minor ELSE -line.amount_minor END), 0) AS computed "
    "FROM ledger_accounts AS account "
    "LEFT JOIN ledger_balances AS balance ON balance.account_id = account.id "
    "LEFT JOIN ledger_lines AS line ON line.account_id = account.id "
    "GROUP BY account.id, balance.balance_minor "
    "HAVING balance.balance_minor IS DISTINCT FROM "
    "coalesce(sum(CASE WHEN account.kind IN ('asset', 'expense') "
    "THEN line.amount_minor ELSE -line.amount_minor END), 0) "
    "ORDER BY account.id"
)


async def open_account(session: AsyncSession, spec: AccountSpec, clock: Clock) -> Account:
    """Return the account `spec` identifies, creating it with a zero balance if it is new.

    Safe to call again, and from two transactions at once: an account is identified by its
    code, currency, owner and reference, and there is only ever one. The kind and name of an
    existing account are not changed.
    """
    identity = {
        "code": spec.code,
        "currency": spec.currency.value,
        "owner_client_id": spec.owner_client_id,
        "reference": spec.reference,
    }
    await session.execute(
        _INSERT_ACCOUNT,
        {
            **identity,
            "id": push_id(),
            "kind": spec.kind.value,
            "name": spec.name,
            "created_at": clock.now(),
        },
    )
    row = (await session.execute(_SELECT_ACCOUNT, identity)).one()
    account = _account(row)
    if account.kind is not spec.kind:
        raise AccountMismatch("The account already exists with another kind")
    return account


async def system_account_id(session: AsyncSession, code: str, currency: Currency) -> str:
    """The ID of one of Finvest's own accounts: no owner and no reference.

    Works in any transaction, a client's included, so a client command can post against a
    system account it is not allowed to read. It gives the ID and nothing else. The account
    must already exist: system accounts are opened by staff or by the worker, never by a
    client request.
    """
    account_id = (
        await session.execute(_SELECT_SYSTEM_ACCOUNT, {"code": code, "currency": currency.value})
    ).scalar_one_or_none()
    if account_id is None:
        raise UnknownAccount("No such system account")
    return str(account_id)


async def post_entry(
    session: AsyncSession,
    draft: EntryDraft,
    clock: Clock,
    *,
    correlation_id: str | None = None,
) -> PostedEntry:
    """Post `draft` in the caller's transaction and return the saved entry.

    Posting the same draft again with the same idempotency key returns the first entry with
    `replayed=True` and posts nothing. The same key with a different draft raises
    `IdempotencyKeyReused`. An entry that would overdraw an account raises `InsufficientFunds`,
    one dated in a closed day raises `DayClosed`, and a staff transaction
    posting a source type it may not raises `StaffSourceNotAllowed`.
    After any of these errors the caller's transaction is still usable and holds no part of
    the entry.

    The entry is checked once more when the transaction commits; the caller must commit for it
    to exist.
    """
    entry_id = push_id()
    posted_at = clock.now()
    currency = draft.currency.value
    line_numbers = {line.account_id: number for number, line in enumerate(draft.lines, start=1)}
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT_ENTRY,
                {
                    "id": entry_id,
                    "entry_type": draft.entry_type,
                    "currency": currency,
                    "effective_date": draft.effective_date,
                    "posted_at": posted_at,
                    "idempotency_key": draft.idempotency_key,
                    "request_digest": draft.digest(),
                    "source_type": draft.source_type,
                    "source_id": draft.source_id,
                    "reverses_entry_id": draft.reverses_entry_id,
                    "correlation_id": correlation_id,
                },
            )
            # One statement per line, in account order: every transaction then locks balance
            # rows in the same order, so entries over the same accounts cannot deadlock.
            for line in sorted_lines(draft.lines):
                await session.execute(
                    _INSERT_LINE,
                    {
                        "entry_id": entry_id,
                        "line_no": line_numbers[line.account_id],
                        "account_id": line.account_id,
                        "currency": currency,
                        "amount_minor": line.amount.minor,
                    },
                )
    except IntegrityError as error:
        # The savepoint has rolled back, so nothing of this attempt remains.
        constraint = _constraint(error)
        if constraint in (_IDEMPOTENCY_KEY_TAKEN, _ALREADY_REVERSED):
            saved = await _replay(session, draft)
            if saved is not None:
                return saved
            if constraint == _IDEMPOTENCY_KEY_TAKEN:
                # Taken by an entry this transaction may not see: still a different request.
                raise IdempotencyKeyReused(
                    "The idempotency key was used for a different entry"
                ) from None
        raise _translate(constraint, error) from None
    return PostedEntry(
        id=entry_id,
        entry_type=draft.entry_type,
        currency=draft.currency,
        effective_date=draft.effective_date,
        posted_at=posted_at,
        idempotency_key=draft.idempotency_key,
        source_type=draft.source_type,
        source_id=draft.source_id,
        reverses_entry_id=draft.reverses_entry_id,
        lines=draft.lines,
    )


async def reverse_entry(
    session: AsyncSession,
    entry_id: str,
    clock: Clock,
    *,
    effective_date: date,
    idempotency_key: str,
    correlation_id: str | None = None,
) -> PostedEntry:
    """Post the entry that undoes `entry_id`. The original is never changed.

    An entry can be reversed once, and a reversal cannot be reversed: to redo the work, post a
    new entry.
    """
    original = await get_entry(session, entry_id)
    if original.reverses_entry_id is not None:
        raise AlreadyReversed("A reversal cannot itself be reversed")
    draft = reversal(
        original.id,
        original.lines,
        effective_date=effective_date,
        idempotency_key=idempotency_key,
    )
    return await post_entry(session, draft, clock, correlation_id=correlation_id)


async def get_entry(session: AsyncSession, entry_id: str) -> PostedEntry:
    """The entry as this transaction may see it.

    Staff and the worker get every line. A client transaction gets only the lines on that
    client's own accounts, so there `lines` is not the whole entry and does not sum to zero.
    The same is true of a replayed entry returned by `post_entry`.
    """
    row = (await session.execute(_SELECT_ENTRY_BY_ID, {"id": entry_id})).one_or_none()
    if row is None:
        raise UnknownEntry("No such journal entry")
    return await _entry(session, row)


async def get_balance(session: AsyncSession, account_id: str) -> Money:
    """The account's balance in its normal direction. Never negative."""
    row = (await session.execute(_SELECT_BALANCE, {"account_id": account_id})).one_or_none()
    if row is None:
        raise UnknownAccount("No such ledger account")
    return Money(int(row.balance_minor), Currency(row.currency))


async def find_balance_drift(session: AsyncSession) -> list[BalanceDrift]:
    """Accounts whose cached balance is not the sum of their lines. An empty list is correct.

    For the daily close and for tests. Run it in a staff transaction or in the worker, which
    see the whole ledger.
    """
    rows = (await session.execute(_SELECT_DRIFT)).all()
    return [
        BalanceDrift(
            account_id=row.id,
            cached_minor=int(row.cached) if row.cached is not None else 0,
            computed_minor=int(row.computed),
        )
        for row in rows
    ]


async def _replay(session: AsyncSession, draft: EntryDraft) -> PostedEntry | None:
    """The entry already saved under this key, if this is the same request again."""
    row = (
        await session.execute(_SELECT_ENTRY_BY_KEY, {"idempotency_key": draft.idempotency_key})
    ).one_or_none()
    if row is None:
        return None
    if row.request_digest != draft.digest():
        raise IdempotencyKeyReused("The idempotency key was used for a different entry")
    return await _entry(session, row, replayed=True)


async def _entry(session: AsyncSession, row: Row[Any], *, replayed: bool = False) -> PostedEntry:
    currency = Currency(row.currency)
    lines = (await session.execute(_SELECT_LINES, {"entry_id": row.id})).all()
    return PostedEntry(
        id=row.id,
        entry_type=row.entry_type,
        currency=currency,
        effective_date=row.effective_date,
        posted_at=row.posted_at,
        idempotency_key=row.idempotency_key,
        source_type=row.source_type,
        source_id=row.source_id,
        reverses_entry_id=row.reverses_entry_id,
        lines=tuple(
            Line(line.account_id, Money(int(line.amount_minor), currency)) for line in lines
        ),
        replayed=replayed,
    )


def _account(row: Row[Any]) -> Account:
    return Account(
        id=row.id,
        code=row.code,
        kind=AccountKind(row.kind),
        currency=Currency(row.currency),
        name=row.name,
        owner_client_id=row.owner_client_id,
        reference=row.reference,
    )


_IDEMPOTENCY_KEY_TAKEN = "uq_ledger_journal_entries_idempotency_key"
_ALREADY_REVERSED = "uq_ledger_journal_entries_reverses_entry_id"


def _constraint(error: IntegrityError) -> str | None:
    """The name of the constraint PostgreSQL refused, as asyncpg reports it."""
    name = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
    return name if isinstance(name, str) else None


def _translate(constraint: str | None, error: IntegrityError) -> Exception:
    """Map a constraint the database refused onto a ledger error."""
    if constraint == "ledger_staff_source_type":
        return StaffSourceNotAllowed("Staff cannot post a ledger entry of this kind")
    if constraint == "ledger_closed_day":
        return DayClosed("The entry is dated in a day that is closed")
    if constraint == "ck_ledger_balances_non_negative":
        return InsufficientFunds("The entry would take a balance below zero")
    if constraint == "fk_ledger_lines_account":
        return UnknownAccount("A line names an account that does not exist in this currency")
    if constraint == _ALREADY_REVERSED:
        return AlreadyReversed("The entry has already been reversed")
    if constraint == "fk_ledger_journal_entries_reverses_entry_id":
        return UnknownEntry("The entry to reverse does not exist")
    return error


__all__ = [
    "Account",
    "BalanceDrift",
    "PostedEntry",
    "find_balance_drift",
    "get_balance",
    "get_entry",
    "open_account",
    "post_entry",
    "reverse_entry",
    "system_account_id",
]
