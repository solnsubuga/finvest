"""The maturity run as staff see it: what fell due, what each term became, and what needs a
person. Read only, for the console's Maturities screen.

Everything here is worked out from the investments themselves, so it is right however many
times the run went round and whichever worker closed what:

* **A day.** For every maturity date in the window, in each currency: how many terms end, the
  principal, and what became of them (rolled over, paid out, held) or, for those not closed
  yet, what the clients chose.
* **Not closed.** Running investments whose maturity date has passed. The run closes each one
  on the morning after its last day, so anything here means the run failed it or has not run.
* **Held.** Matured, and the money waits in payouts because the client's choice could not be
  carried out. The client sends it on from the app; the reason says why it waits.

See docs/investing.md, "Maturity".
"""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.investments.holdings import (
    MAX_PAGE_SIZE,
    Investment,
    investment_from_row,
    select_investments,
)
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money

# Days shown before and after today. A week back covers a long weekend of missed runs; two
# weeks ahead is what the dealing desk plans its cash around.
DAYS_BACK = 7
DAYS_AHEAD = 14


@dataclass(frozen=True, slots=True)
class MaturityDay:
    """Every term in one currency whose maturity date is `day`."""

    day: date
    currency: Currency
    investments: int
    principal: Money
    # Closed by the run.
    rolled_over: int
    paid_out: int
    held: int
    # Not closed yet, by what the client chose. A term with no choice rolls over.
    open_roll_over: int
    open_pay_out: int

    @property
    def still_open(self) -> int:
        return self.open_roll_over + self.open_pay_out


@dataclass(frozen=True, slots=True)
class Maturities:
    today: date
    days: list[MaturityDay]
    # Past their maturity date and still running, oldest first.
    not_closed: list[Investment]
    # Matured, with the money waiting for the client, oldest first.
    held: list[Investment]


# Early withdrawals are left out: those close through the dealing desk, not the run.
_DAYS = text(
    "SELECT i.maturity_date AS day, i.currency, count(*) AS investments, "
    "CAST(sum(i.principal_minor) AS bigint) AS principal_minor, "
    "count(*) FILTER (WHERE i.status = 'matured' AND i.rolled_into_id IS NOT NULL) "
    "AS rolled_over, "
    "count(*) FILTER (WHERE i.status = 'matured' AND i.payout_id IS NOT NULL) AS paid_out, "
    "count(*) FILTER (WHERE i.status = 'matured' AND i.rolled_into_id IS NULL "
    "AND i.payout_id IS NULL) AS held, "
    "count(*) FILTER (WHERE i.status = 'active' AND i.at_maturity IS DISTINCT FROM 'pay_out') "
    "AS open_roll_over, "
    "count(*) FILTER (WHERE i.status = 'active' AND i.at_maturity = 'pay_out') AS open_pay_out "
    "FROM investments i "
    "WHERE i.maturity_date BETWEEN :date_from AND :date_to "
    "AND i.status IN ('active', 'matured') AND i.withdrawal_requested_at IS NULL "
    "GROUP BY i.maturity_date, i.currency ORDER BY i.maturity_date, i.currency"
)
_NOT_CLOSED = select_investments(
    "WHERE i.status = 'active' AND i.withdrawal_requested_at IS NULL "
    "AND i.maturity_date < :today ORDER BY i.maturity_date, i.id LIMIT :limit"
)
_HELD = select_investments(
    "WHERE i.status = 'matured' AND i.rolled_into_id IS NULL AND i.payout_id IS NULL "
    "ORDER BY i.maturity_date, i.id LIMIT :limit"
)


async def maturities(session: AsyncSession, staff: Actor, clock: Clock) -> Maturities:
    """The run around today, and every term that needs a person. For the console."""
    if staff.kind != "staff":
        raise ApplicationError("forbidden")
    today = business_date(clock.now())
    window = {
        "date_from": today - timedelta(days=DAYS_BACK),
        "date_to": today + timedelta(days=DAYS_AHEAD),
    }
    days = [
        MaturityDay(
            day=row.day,
            currency=Currency(row.currency),
            investments=row.investments,
            principal=Money(row.principal_minor, Currency(row.currency)),
            rolled_over=row.rolled_over,
            paid_out=row.paid_out,
            held=row.held,
            open_roll_over=row.open_roll_over,
            open_pay_out=row.open_pay_out,
        )
        for row in (await session.execute(_DAYS, window)).all()
    ]
    not_closed = await session.execute(_NOT_CLOSED, {"today": today, "limit": MAX_PAGE_SIZE})
    held = await session.execute(_HELD, {"limit": MAX_PAGE_SIZE})
    return Maturities(
        today=today,
        days=days,
        not_closed=[investment_from_row(row) for row in not_closed.all()],
        held=[investment_from_row(row) for row in held.all()],
    )
