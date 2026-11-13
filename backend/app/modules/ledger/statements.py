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


__all__ = ["Movement", "account_movements", "account_owner", "balance_as_of"]
