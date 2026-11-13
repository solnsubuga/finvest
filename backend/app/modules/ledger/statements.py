"""Reading an account's history, for reconciliation and the day close. Read-only.

Row policies apply as everywhere in the ledger: staff and the worker see every account, and a
client transaction sees only that client's own accounts.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ledger.domain.errors import UnknownAccount
from app.shared.money import Currency, Money


@dataclass(frozen=True, slots=True)
class Movement:
    """One line on an account, with what its journal entry says about where it came from."""

    entry_id: str
    line_no: int
    effective_date: date
    posted_at: datetime
    amount: Money  # signed: a debit is positive and a credit is negative
    entry_type: str
    source_type: str | None
    source_id: str | None


_SELECT_ACCOUNT = text(
    "SELECT kind, currency, owner_client_id FROM ledger_accounts WHERE id = :account_id"
)
_SELECT_MOVEMENTS = text(
    "SELECT line.entry_id, line.line_no, entry.effective_date, entry.posted_at, "
    "line.amount_minor, line.currency, entry.entry_type, entry.source_type, entry.source_id "
    "FROM ledger_lines AS line JOIN ledger_journal_entries AS entry ON entry.id = line.entry_id "
    "WHERE line.account_id = :account_id "
    "AND entry.effective_date BETWEEN :date_from AND :date_to "
    "ORDER BY entry.effective_date, line.entry_id, line.line_no"
)
_SELECT_BALANCE_AS_OF = text(
    "SELECT coalesce(sum(line.amount_minor), 0) AS debit_minor "
    "FROM ledger_lines AS line JOIN ledger_journal_entries AS entry ON entry.id = line.entry_id "
    "WHERE line.account_id = :account_id AND entry.effective_date <= :on"
)
_DEBIT_NORMAL = frozenset({"asset", "expense"})


async def account_movements(
    session: AsyncSession, account_id: str, *, date_from: date, date_to: date
) -> list[Movement]:
    """Every line on the account dated from `date_from` to `date_to`, both included.

    Ordered by date, then by entry and line, which is the order the entries were posted in
    within a day. Match a statement line to a movement on `source_id`.
    """
    await _account(session, account_id)
    rows = (
        await session.execute(
            _SELECT_MOVEMENTS,
            {"account_id": account_id, "date_from": date_from, "date_to": date_to},
        )
    ).all()
    return [
        Movement(
            entry_id=row.entry_id,
            line_no=int(row.line_no),
            effective_date=row.effective_date,
            posted_at=row.posted_at,
            amount=Money(int(row.amount_minor), Currency(row.currency)),
            entry_type=row.entry_type,
            source_type=row.source_type,
            source_id=row.source_id,
        )
        for row in rows
    ]


async def balance_as_of(session: AsyncSession, account_id: str, on: date) -> Money:
    """The account's balance counting only entries dated on or before `on`, in the account's
    normal direction. Once the ledger is closed through `on`, this figure cannot change."""
    kind, currency, _ = await _account(session, account_id)
    debit_minor = int(
        (await session.execute(_SELECT_BALANCE_AS_OF, {"account_id": account_id, "on": on}))
        .one()
        .debit_minor
    )
    return Money(debit_minor if kind in _DEBIT_NORMAL else -debit_minor, currency)


async def account_owner(session: AsyncSession, account_id: str) -> str | None:
    """The client the account belongs to, or None for one of Finvest's own accounts."""
    _, _, owner = await _account(session, account_id)
    return owner


async def _account(session: AsyncSession, account_id: str) -> tuple[str, Currency, str | None]:
    row = (await session.execute(_SELECT_ACCOUNT, {"account_id": account_id})).one_or_none()
    if row is None:
        raise UnknownAccount("No such ledger account")
    return row.kind, Currency(row.currency), row.owner_client_id


@dataclass(frozen=True, slots=True)
class MonthTotal:
    """What moved through one kind of a client's accounts in one calendar month.

    Both figures are zero or more: `debits` is the sum of debit lines and `credits` the sum of
    credit lines, so a caller reads the direction it needs."""

    month: date  # the first day of the month
    debits: Money
    credits: Money


_SELECT_MONTH_TOTALS = text(
    "SELECT date_trunc('month', entry.effective_date)::date AS month, "
    "coalesce(sum(line.amount_minor) FILTER (WHERE line.amount_minor > 0), 0) AS debit_minor, "
    "coalesce(-sum(line.amount_minor) FILTER (WHERE line.amount_minor < 0), 0) AS credit_minor "
    "FROM ledger_lines AS line "
    "JOIN ledger_accounts AS account ON account.id = line.account_id "
    "JOIN ledger_journal_entries AS entry ON entry.id = line.entry_id "
    "WHERE account.owner_client_id = :client_id AND account.code = :code "
    "AND account.currency = :currency "
    "AND (CAST(:entry_type AS text) IS NULL OR entry.entry_type = :entry_type "
    # A reversal of an entry of this type counts against it, in the reversal's own month.
    "OR EXISTS (SELECT 1 FROM ledger_journal_entries AS original "
    "WHERE original.id = entry.reverses_entry_id AND original.entry_type = :entry_type)) "
    "AND entry.effective_date BETWEEN :date_from AND :date_to "
    "GROUP BY 1 ORDER BY 1"
)


async def client_month_totals(
    session: AsyncSession,
    *,
    client_id: str,
    code: str,
    currency: Currency,
    date_from: date,
    date_to: date,
    entry_type: str | None = None,
) -> list[MonthTotal]:
    """Monthly totals across every account of one client with this code, such as all their
    holdings, by the entries' effective dates from `date_from` to `date_to`, both included.

    Only months with a movement are returned. `entry_type` narrows it to one kind of entry and the
    reversals of entries of that kind, so a reversed entry nets to nothing, as on a statement. A
    reversal falls in the month of its own effective date. A client transaction sees only its own
    accounts, so asking for another client returns none.
    """
    rows = await session.execute(
        _SELECT_MONTH_TOTALS,
        {
            "client_id": client_id,
            "code": code,
            "currency": currency.value,
            "entry_type": entry_type,
            "date_from": date_from,
            "date_to": date_to,
        },
    )
    return [
        MonthTotal(
            month=row.month,
            debits=Money(int(row.debit_minor), currency),
            credits=Money(int(row.credit_minor), currency),
        )
        for row in rows
    ]


__all__ = [
    "MonthTotal",
    "Movement",
    "account_movements",
    "account_owner",
    "balance_as_of",
    "client_month_totals",
]
