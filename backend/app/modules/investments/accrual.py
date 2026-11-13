"""The daily interest run. Every function works inside the worker's transaction.

Each run is for one business day. For every running investment it works out everything earned
by that day from the formula, subtracts what earlier runs posted, and posts the difference in
one ledger entry:

    debit   interest.expense           interest before tax
    credit  client.interest_accrued    what the client earns, for this investment
    credit  tax.withholding_payable    the tax held back

The entry and the investment's own running totals change in the same transaction, so the two
always agree. Because the amount is "earned so far minus posted so far", a run that is retried,
run twice, run late or run after a missed day never pays a day twice and never skips one.

See docs/investing.md.
"""

from dataclasses import dataclass
from datetime import date, datetime

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.investments.domain.interest import Accrual, posting_due
from app.modules.investments.holdings import (
    MAX_PAGE_SIZE,
    Investment,
    investment_from_row,
    select_investments,
)
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money

INTEREST_EXPENSE = "interest.expense"
INTEREST_ACCRUED = "client.interest_accrued"
TAX_PAYABLE = "tax.withholding_payable"
ENTRY_TYPE = "investment.interest_accrued"
BATCH = 200
logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class Batch:
    """What one step of a run did. `last_id` is None when there was nothing left to look at."""

    last_id: str | None
    posted: int
    failed: int


@dataclass(frozen=True, slots=True)
class InterestRun:
    """One day's run in one currency, for staff. It shows the tax, so never for a client."""

    run_date: date
    currency: Currency
    investments: int
    gross: Money
    tax: Money
    # Investments that should have earned interest for this day and had not when the run
    # ended. Anything above zero needs a person to look.
    behind: int
    started_at: datetime
    finished_at: datetime | None

    @property
    def net(self) -> Money:
        return self.gross - self.tax


# An investment is due on a run date if it started before that day and has not been paid up to
# it, or up to its maturity if that comes first.
_IS_DUE = (
    "i.status = 'active' AND i.start_date < :run_date AND (i.accrued_through IS NULL OR "
    "(i.accrued_through < :run_date "
    "AND (i.maturity_date IS NULL OR i.accrued_through < i.maturity_date)))"
)
# In ID order and past the last one seen, so an investment that fails is not met again in the
# same run. One that another worker holds is skipped: that worker is paying it.
_DUE = select_investments(
    f"WHERE {_IS_DUE} AND i.id > :after ORDER BY i.id LIMIT :limit FOR UPDATE OF i SKIP LOCKED"
)
_BEHIND = text(
    f"SELECT i.currency, count(*) AS behind FROM investments i WHERE {_IS_DUE} "  # noqa: S608
    "GROUP BY i.currency"
)
_ACCRUE = text(
    "UPDATE investments SET accrued_through = :through, accrued_gross_minor = :gross, "
    "accrued_tax_minor = :tax, updated_at = :now WHERE id = :id"
)
_ADD_TO_RUN = text(
    "INSERT INTO investment_interest_runs AS r (run_date, currency, investments, gross_minor, "
    "tax_minor, started_at) VALUES (:run_date, :currency, :investments, :gross, :tax, :now) "
    "ON CONFLICT (run_date, currency) DO UPDATE SET "
    "investments = r.investments + EXCLUDED.investments, "
    "gross_minor = r.gross_minor + EXCLUDED.gross_minor, "
    "tax_minor = r.tax_minor + EXCLUDED.tax_minor"
)
_FINISH_RUN = text(
    "INSERT INTO investment_interest_runs AS r (run_date, currency, started_at, finished_at, "
    "behind) VALUES (:run_date, :currency, :now, :now, :behind) "
    "ON CONFLICT (run_date, currency) DO UPDATE SET finished_at = :now, behind = :behind"
)
_RUNS = text(
    "SELECT run_date, currency, investments, gross_minor, tax_minor, behind, started_at, "
    "finished_at FROM investment_interest_runs ORDER BY run_date DESC, currency LIMIT :limit"
)


async def accrue_batch(
    session: AsyncSession, run_date: date, clock: Clock, *, after: str = "", limit: int = BATCH
) -> Batch:
    """Pay the next investments due for `run_date`, in ID order after `after`. Worker only.

    Each investment is paid inside its own savepoint. If one cannot be paid it is left exactly
    as it was, logged, and stays due: one bad record does not hold up everyone else's
    interest, and the end of the run counts it as behind.
    """
    _check_run_date(run_date, clock)
    rows = (
        await session.execute(_DUE, {"run_date": run_date, "after": after, "limit": limit})
    ).all()
    posted: dict[Currency, tuple[int, Accrual]] = {}
    failed = 0
    for row in rows:
        investment = investment_from_row(row)
        try:
            async with session.begin_nested():
                amount = await _accrue(session, investment, run_date, clock)
        except Exception as error:
            failed += 1
            logger.error(
                "interest_not_posted",
                investment_id=investment.id,
                error_type=type(error).__name__,
            )
            continue
        if amount is not None:
            currency = investment.amount.currency
            count, so_far = posted.get(currency, (0, _nothing(currency)))
            posted[currency] = (
                count + 1,
                Accrual(gross=so_far.gross + amount.gross, tax=so_far.tax + amount.tax),
            )
    now = clock.now()
    for currency, (count, total) in posted.items():
        await session.execute(
            _ADD_TO_RUN,
            {
                "run_date": run_date,
                "currency": currency.value,
                "investments": count,
                "gross": total.gross.minor,
                "tax": total.tax.minor,
                "now": now,
            },
        )
    return Batch(
        last_id=rows[-1].id if rows else None,
        posted=sum(count for count, _ in posted.values()),
        failed=failed,
    )


async def accrue_investment(
    session: AsyncSession, investment_id: str, run_date: date, clock: Clock
) -> Accrual | None:
    """Pay one investment up to `run_date`. Worker only. Returns what was posted, if anything.

    For putting right an investment a run left behind. It does not add to the run's totals.
    """
    _check_run_date(run_date, clock)
    row = (
        await session.execute(
            select_investments("WHERE i.id = :id FOR UPDATE OF i"), {"id": investment_id}
        )
    ).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return await _accrue(session, investment_from_row(row), run_date, clock)


async def finish_run(session: AsyncSession, run_date: date, clock: Clock) -> dict[str, int]:
    """Close the run for `run_date` and count what it left behind. Worker only."""
    counts = {
        Currency(row.currency): int(row.behind)
        for row in (await session.execute(_BEHIND, {"run_date": run_date})).all()
    }
    now = clock.now()
    for currency in Currency:
        await session.execute(
            _FINISH_RUN,
            {
                "run_date": run_date,
                "currency": currency.value,
                "now": now,
                "behind": counts.get(currency, 0),
            },
        )
    return {currency.value: behind for currency, behind in counts.items()}


async def interest_runs(
    session: AsyncSession, staff: Actor, *, limit: int = 60
) -> list[InterestRun]:
    """The latest daily runs, newest first, one row per currency. For the console."""
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    rows = await session.execute(_RUNS, {"limit": max(1, min(limit, MAX_PAGE_SIZE))})
    return [
        InterestRun(
            run_date=row.run_date,
            currency=Currency(row.currency),
            investments=row.investments,
            gross=Money(row.gross_minor, Currency(row.currency)),
            tax=Money(row.tax_minor, Currency(row.currency)),
            behind=row.behind,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )
        for row in rows.all()
    ]


def interest_account(investment: Investment) -> AccountSpec:
    """Interest the client has earned on this investment and has not been paid."""
    return AccountSpec(
        INTEREST_ACCRUED,
        AccountKind.LIABILITY,
        investment.amount.currency,
        "Interest earned, not yet paid",
        owner_client_id=investment.client_id,
        reference=investment.id,
    )


# Internals --------------------------------------------------------------------------------------


def _check_run_date(run_date: date, clock: Clock) -> None:
    if run_date > business_date(clock.now()):
        # Interest is never paid for a day that has not come.
        raise ApplicationError("conflict")


def _nothing(currency: Currency) -> Accrual:
    return Accrual(gross=Money.zero(currency), tax=Money.zero(currency))


async def _accrue(
    session: AsyncSession, investment: Investment, run_date: date, clock: Clock
) -> Accrual | None:
    """Post what `investment`, already locked, is due for `run_date`."""
    if investment.start_date is None or investment.status.value != "active":
        return None
    posting = posting_due(
        investment.terms,
        start=investment.start_date,
        term_days=investment.term_days,
        posted=Accrual(gross=investment.accrued_gross, tax=investment.accrued_tax),
        posted_through=investment.accrued_through,
        run_date=run_date,
    )
    if posting is None:
        return None
    now = clock.now()
    currency = investment.amount.currency
    amount = posting.amount
    if amount.gross.is_positive:
        expense = await ledger.open_account(
            session,
            AccountSpec(
                INTEREST_EXPENSE, AccountKind.EXPENSE, currency, "Interest paid to clients"
            ),
            clock,
        )
        lines = [debit(expense.id, amount.gross)]
        if amount.net.is_positive:
            earned = await ledger.open_account(session, interest_account(investment), clock)
            lines.append(credit(earned.id, amount.net))
        if amount.tax.is_positive:
            payable = await ledger.open_account(
                session,
                AccountSpec(
                    TAX_PAYABLE, AccountKind.LIABILITY, currency, "Tax withheld from interest"
                ),
                clock,
            )
            lines.append(credit(payable.id, amount.tax))
        await ledger.post_entry(
            session,
            EntryDraft(
                entry_type=ENTRY_TYPE,
                # Dated the day it is posted. That is the run date unless the run is late, and
                # a late run must not write into a day the books have already closed.
                effective_date=business_date(now),
                lines=tuple(lines),
                idempotency_key=f"investment-interest:{investment.id}:{posting.through}",
                source_type="investment",
                source_id=investment.id,
            ),
            clock,
        )
    # A small investment can earn nothing on a given day. The day is still marked as paid.
    await session.execute(
        _ACCRUE,
        {
            "id": investment.id,
            "through": posting.through,
            "gross": posting.total.gross.minor,
            "tax": posting.total.tax.minor,
            "now": now,
        },
    )
    return amount
