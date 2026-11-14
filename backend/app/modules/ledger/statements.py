"""Reading the books: an account's history for reconciliation, the day close and client
statements, and each account code's totals for the Finance screen and the accounting export.

Read-only.

Row policies apply as everywhere in the ledger: staff and the worker see every account, and a
client transaction sees only that client's own accounts.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ledger.domain.errors import MixedAccountKinds, UnknownAccount
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
_SELECT_TOTAL = text(
    "SELECT count(DISTINCT account.kind) AS kinds, "
    "coalesce(sum(balance.balance_minor), 0)::bigint AS total_minor "
    "FROM ledger_accounts AS account "
    "JOIN ledger_balances AS balance ON balance.account_id = account.id "
    "WHERE account.code = :code AND account.currency = :currency"
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


@dataclass(frozen=True, slots=True)
class ClientLine:
    """One line on one of a client's own accounts, with the entry it belongs to."""

    entry_id: str
    effective_date: date
    entry_type: str
    source_type: str | None
    source_id: str | None
    account_code: str
    account_reference: str | None
    # The change to what Finvest owes the client: positive when the client is owed more.
    # A credit to a client liability is positive; a debit to a client receivable is negative.
    owed: Money


_SELECT_CLIENT_LINES = text(
    "SELECT line.entry_id, entry.effective_date, entry.entry_type, entry.source_type, "
    "entry.source_id, account.code, account.reference, line.amount_minor, line.currency "
    "FROM ledger_lines AS line "
    "JOIN ledger_journal_entries AS entry ON entry.id = line.entry_id "
    "JOIN ledger_accounts AS account ON account.id = line.account_id "
    "WHERE account.owner_client_id = :client_id "
    "AND entry.effective_date BETWEEN :date_from AND :date_to "
    "ORDER BY entry.effective_date, entry.posted_at, line.entry_id, line.line_no"
)
_SELECT_CLIENT_OWED_AS_OF = text(
    "SELECT account.code, account.reference, line.currency, "
    "coalesce(sum(line.amount_minor), 0) AS debit_minor "
    "FROM ledger_lines AS line "
    "JOIN ledger_journal_entries AS entry ON entry.id = line.entry_id "
    "JOIN ledger_accounts AS account ON account.id = line.account_id "
    "WHERE account.owner_client_id = :client_id AND entry.effective_date <= :on "
    "GROUP BY account.code, account.reference, line.currency "
    "ORDER BY account.code, account.reference, line.currency"
)
_SELECT_CLIENTS_WITH_ACCOUNTS = text(
    "SELECT DISTINCT owner_client_id FROM ledger_accounts "
    "WHERE owner_client_id IS NOT NULL AND owner_client_id > :after "
    "ORDER BY owner_client_id LIMIT :limit"
)


@dataclass(frozen=True, slots=True)
class ClientOwed:
    """What Finvest owed a client on one account code and reference at the end of a day."""

    account_code: str
    account_reference: str | None
    owed: Money


async def client_lines(
    session: AsyncSession, client_id: str, *, date_from: date, date_to: date
) -> list[ClientLine]:
    """Every line on the client's own accounts dated from `date_from` to `date_to`, inclusive.

    Lines on Finvest's own accounts are left out, so a client transaction and a staff one see
    the same thing. Ordered as the entries were posted.
    """
    rows = await session.execute(
        _SELECT_CLIENT_LINES, {"client_id": client_id, "date_from": date_from, "date_to": date_to}
    )
    return [
        ClientLine(
            entry_id=row.entry_id,
            effective_date=row.effective_date,
            entry_type=row.entry_type,
            source_type=row.source_type,
            source_id=row.source_id,
            account_code=row.code,
            account_reference=row.reference,
            owed=Money(-int(row.amount_minor), Currency(row.currency)),
        )
        for row in rows.all()
    ]


async def client_owed_as_of(session: AsyncSession, client_id: str, on: date) -> list[ClientOwed]:
    """What Finvest owed the client at the end of `on`, per account code and reference.

    Counts only entries dated on or before `on`, so once the ledger is closed through `on` the
    figures cannot change. Accounts that net to zero are included, so a caller can tell an
    investment that was paid out from one that never existed.
    """
    rows = await session.execute(_SELECT_CLIENT_OWED_AS_OF, {"client_id": client_id, "on": on})
    return [
        ClientOwed(row.code, row.reference, Money(-int(row.debit_minor), Currency(row.currency)))
        for row in rows.all()
    ]


async def clients_with_accounts(
    session: AsyncSession, *, after: str = "", limit: int = 500
) -> list[str]:
    """Client IDs that own at least one ledger account, in ID order, a page at a time."""
    rows = await session.execute(_SELECT_CLIENTS_WITH_ACCOUNTS, {"after": after, "limit": limit})
    return [row.owner_client_id for row in rows.all()]


async def total_balance(session: AsyncSession, code: str, currency: Currency) -> Money:
    """The balances of every account with this code in `currency`, added up, each in its normal
    direction. Zero when there is none.

    For staff and the worker, who see every account. A client transaction sees only that
    client's own accounts, so the total would be theirs alone. Refuses a code whose accounts
    are of more than one kind, since their balances could not be added.
    """
    row = (await session.execute(_SELECT_TOTAL, {"code": code, "currency": currency.value})).one()
    if int(row.kinds) > 1:
        raise MixedAccountKinds("Accounts with this code are of more than one kind")
    return Money(int(row.total_minor), currency)


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


@dataclass(frozen=True, slots=True)
class SystemTotal:
    """What moved through one of Finvest's own accounts in one calendar month, from entries of one
    source type, and from one source when asked for by source."""

    month: date  # the first day of the month
    account_code: str
    source_id: str | None
    amount: Money  # signed: debits less credits


_SELECT_SYSTEM_TOTALS = text(
    "SELECT date_trunc('month', entry.effective_date)::date AS month, account.code, "
    "CASE WHEN CAST(:by_source AS boolean) THEN entry.source_id END AS source_id, "
    "sum(line.amount_minor)::bigint AS debit_minor "
    "FROM ledger_lines AS line "
    "JOIN ledger_accounts AS account ON account.id = line.account_id "
    "JOIN ledger_journal_entries AS entry ON entry.id = line.entry_id "
    "WHERE account.owner_client_id IS NULL AND account.reference IS NULL "
    "AND account.code = ANY(CAST(:codes AS text[])) AND account.currency = :currency "
    "AND entry.source_type = :source_type "
    "AND (CAST(:source_ids AS text[]) IS NULL "
    "OR entry.source_id = ANY(CAST(:source_ids AS text[]))) "
    "AND entry.effective_date BETWEEN :date_from AND :date_to "
    "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
)


async def system_totals(
    session: AsyncSession,
    *,
    codes: list[str],
    currency: Currency,
    source_type: str,
    date_from: date,
    date_to: date,
    by_source: bool,
    source_ids: list[str] | None = None,
) -> list[SystemTotal]:
    """Monthly totals on Finvest's own accounts with these codes, from entries of `source_type`
    dated from `date_from` to `date_to`, both included, by the entries' effective dates.

    With `by_source`, one total per source as well, such as per investment. `source_ids` narrows
    it to those sources. Reversals and corrections of the same source net out, since the sums are
    signed. Only combinations with a line are returned. For staff and the worker: a client
    transaction sees none of Finvest's own accounts.
    """
    rows = await session.execute(
        _SELECT_SYSTEM_TOTALS,
        {
            "codes": list(codes),
            "currency": currency.value,
            "source_type": source_type,
            "source_ids": None if source_ids is None else list(source_ids),
            "date_from": date_from,
            "date_to": date_to,
            "by_source": by_source,
        },
    )
    return [
        SystemTotal(
            month=row.month,
            account_code=row.code,
            source_id=row.source_id,
            amount=Money(int(row.debit_minor), currency),
        )
        for row in rows
    ]


__all__ = [
    "ClientLine",
    "ClientOwed",
    "MonthTotal",
    "Movement",
    "SystemTotal",
    "account_movements",
    "account_owner",
    "balance_as_of",
    "client_lines",
    "client_month_totals",
    "client_owed_as_of",
    "clients_with_accounts",
    "system_totals",
    "total_balance",
]


# The books by account code, for the Finance screen and the accounting export ----------------


@dataclass(frozen=True, slots=True)
class CodeTotals:
    """Every account with one code, added together, over a range of days.

    A client's accounts are folded into their code, so no client is named. `opening` and
    `closing` are in the code's normal direction; `debits` and `credits` are each zero or more.
    """

    code: str
    kind: str
    name: str
    opening: Money
    debits: Money
    credits: Money
    closing: Money


@dataclass(frozen=True, slots=True)
class DayCodeTotals:
    """What one day's entries did to one account code: the lines of a daily journal."""

    effective_date: date
    code: str
    kind: str
    name: str
    debits: Money
    credits: Money
    entries: int


_SELECT_CODE_TOTALS = text(
    "SELECT account.code, account.kind, min(account.name) AS name, "
    "count(DISTINCT account.kind) AS kinds, "
    "coalesce(sum(line.amount_minor) FILTER (WHERE entry.effective_date < :date_from), 0) "
    "AS before_minor, "
    "coalesce(sum(line.amount_minor) FILTER ("
    "WHERE entry.effective_date >= :date_from AND line.amount_minor > 0), 0) AS debit_minor, "
    "coalesce(-sum(line.amount_minor) FILTER ("
    "WHERE entry.effective_date >= :date_from AND line.amount_minor < 0), 0) AS credit_minor "
    "FROM ledger_lines AS line "
    "JOIN ledger_journal_entries AS entry ON entry.id = line.entry_id "
    "JOIN ledger_accounts AS account ON account.id = line.account_id "
    "WHERE line.currency = :currency AND entry.effective_date <= :date_to "
    "GROUP BY account.code, account.kind "
    "ORDER BY account.code"
)
_SELECT_DAY_TOTALS = text(
    "SELECT entry.effective_date, account.code, account.kind, min(account.name) AS name, "
    "coalesce(sum(line.amount_minor) FILTER (WHERE line.amount_minor > 0), 0) AS debit_minor, "
    "coalesce(-sum(line.amount_minor) FILTER (WHERE line.amount_minor < 0), 0) AS credit_minor, "
    "count(DISTINCT line.entry_id) AS entries "
    "FROM ledger_lines AS line "
    "JOIN ledger_journal_entries AS entry ON entry.id = line.entry_id "
    "JOIN ledger_accounts AS account ON account.id = line.account_id "
    "WHERE line.currency = :currency "
    "AND entry.effective_date BETWEEN :date_from AND :date_to "
    "GROUP BY entry.effective_date, account.code, account.kind "
    "ORDER BY entry.effective_date, account.code"
)


async def totals_by_code(
    session: AsyncSession, currency: Currency, *, date_from: date, date_to: date
) -> list[CodeTotals]:
    """Each account code's opening balance at the start of `date_from`, what was debited and
    credited to it from `date_from` to `date_to` inclusive, and its closing balance.

    Only codes with at least one line dated up to `date_to` are listed, in code order. Once the
    ledger is closed through `date_to`, these figures cannot change. Raises MixedAccountKinds if
    accounts of one code have different kinds, since their balances could not be added.
    """
    rows = (
        await session.execute(
            _SELECT_CODE_TOTALS,
            {"currency": currency.value, "date_from": date_from, "date_to": date_to},
        )
    ).all()
    codes = [row.code for row in rows]
    if len(codes) != len(set(codes)):
        raise MixedAccountKinds("Accounts of one code have different kinds")
    found: list[CodeTotals] = []
    for row in rows:
        before, debits, credits = int(row.before_minor), int(row.debit_minor), int(row.credit_minor)
        sign = 1 if row.kind in _DEBIT_NORMAL else -1
        found.append(
            CodeTotals(
                code=row.code,
                kind=row.kind,
                name=row.name,
                opening=Money(sign * before, currency),
                debits=Money(debits, currency),
                credits=Money(credits, currency),
                closing=Money(sign * (before + debits - credits), currency),
            )
        )
    return found


async def day_totals(
    session: AsyncSession, currency: Currency, *, date_from: date, date_to: date
) -> list[DayCodeTotals]:
    """For each day from `date_from` to `date_to` inclusive, what its entries debited and
    credited to each account code. Days without entries are left out. Each day's debits equal
    its credits, because every entry balances."""
    rows = await session.execute(
        _SELECT_DAY_TOTALS,
        {"currency": currency.value, "date_from": date_from, "date_to": date_to},
    )
    return [
        DayCodeTotals(
            effective_date=row.effective_date,
            code=row.code,
            kind=row.kind,
            name=row.name,
            debits=Money(int(row.debit_minor), currency),
            credits=Money(int(row.credit_minor), currency),
            entries=int(row.entries),
        )
        for row in rows.all()
    ]
