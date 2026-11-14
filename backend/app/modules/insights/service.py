"""What the insights module offers: a client's own figures, after tax. Read-only.

Every figure comes from the ledger or from the investments module's client views, so insights
always agree with the books. Nothing here shows a tax rate, a tax amount or a rate before tax:
interest is read from the client's interest account, which holds interest after tax.

Other modules import from here. A client-facing route may call only `client_insights`.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.autoinvest import service as autoinvest
from app.modules.goals import saving_rules
from app.modules.goals import service as goals
from app.modules.insights.domain.months import DEFAULT_MONTHS, month_end, months_ending
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import AtMaturity, InvestmentStatus
from app.modules.ledger import statements
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money

# The ledger accounts and entries each figure is read from. See docs/insights.md.
INVESTED = ("client.holding", "investment.started")
EARNED = ("client.interest_accrued", "investment.interest_accrued")
PAID_OUT = ("client.payout_in_flight", "payout.settle")
# A paid payout that came back, owed to the client again. It comes off paid out in its month.
RETURNED = ("client.payout_pending", "payout.returned")
UPCOMING_LIMIT = 5


class UpcomingKind(StrEnum):
    # An investment matures: `amount` is what comes back, after tax.
    MATURITY = "maturity"
    # An automatic investing plan asks for its next payment: `amount` is what it asks for.
    AUTOINVEST_RUN = "autoinvest_run"
    # A goal's saving rule asks for its next monthly saving: `amount` is what it asks for.
    GOAL_SAVING = "goal_saving"
    # A goal's date: `amount` is its target.
    GOAL_DATE = "goal_date"


@dataclass(frozen=True, slots=True)
class Month:
    """One calendar month of a client's money, in one currency."""

    month: date  # its first day
    # New money that started earning: payments that started an investment. Rollovers are not
    # new money, so they are not counted.
    invested: Money
    # Interest earned after tax.
    earned: Money
    # Money that reached the client's mobile money or bank account, less any that came back.
    paid_out: Money


@dataclass(frozen=True, slots=True)
class Upcoming:
    """Something that will happen to the client's money, soonest first."""

    kind: UpcomingKind
    on: date
    # See `UpcomingKind` for what the amount is, for each kind. Never a figure before tax.
    amount: Money
    # A maturity names its investment, a run its plan, and a goal saving or date its goal.
    investment_id: str | None = None
    plan_id: str | None = None
    goal_id: str | None = None
    # The product, for a maturity or a run. None for a goal's date.
    product_name: str | None = None
    # The goal's name, the client's own words, for a goal saving or date.
    goal_name: str | None = None
    at_maturity: AtMaturity | None = None


@dataclass(frozen=True, slots=True)
class ClientInsights:
    currency: Currency
    months: tuple[Month, ...]
    # What is invested and earning now, and the interest earned on it so far, after tax.
    invested_now: Money
    earned_so_far: Money
    upcoming: tuple[Upcoming, ...]

    @property
    def this_month(self) -> Month:
        return self.months[-1]


async def client_insights(
    session: AsyncSession,
    client: Actor,
    clock: Clock,
    *,
    months: int = DEFAULT_MONTHS,
    currency: Currency = Currency.UGX,
) -> ClientInsights:
    """A client's own insights for the last `months` months, this one included.

    `session` is the client's own transaction, so the ledger's row policies show only their
    accounts.
    """
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    today = business_date(clock.now())
    try:
        window = months_ending(today, months)
    except ValueError:
        raise ApplicationError("validation_failed") from None
    figures = {
        name: await _monthly(session, client.id, code, entry_type, currency, window, today)
        for name, (code, entry_type) in {
            "invested": INVESTED,
            "earned": EARNED,
            "paid_out": PAID_OUT,
            "returned": RETURNED,
        }.items()
    }
    zero = Money(0, currency)
    paid_out = {
        start: figures["paid_out"].get(start, zero) - figures["returned"].get(start, zero)
        for start in window
    }
    portfolio = [
        held
        for held in await investments.client_investments(session, client, clock)
        if held.status is InvestmentStatus.ACTIVE and held.amount.currency is currency
    ]
    upcoming = sorted(
        [
            *(
                Upcoming(
                    kind=UpcomingKind.MATURITY,
                    on=held.maturity_date,
                    amount=held.at_maturity_returns.total,
                    investment_id=held.id,
                    product_name=held.product_name,
                    at_maturity=held.at_maturity,
                )
                for held in portfolio
                if held.maturity_date is not None
                and held.maturity_date >= today
                and held.at_maturity_returns is not None
            ),
            *await _coming_savings(session, client, clock, currency, today),
        ],
        key=lambda item: (item.on, item.kind.value, item.investment_id or item.plan_id or ""),
    )[:UPCOMING_LIMIT]
    return ClientInsights(
        currency=currency,
        months=tuple(
            Month(
                month=start,
                invested=figures["invested"].get(start, zero),
                earned=figures["earned"].get(start, zero),
                paid_out=paid_out[start],
            )
            for start in window
        ),
        invested_now=sum((held.amount for held in portfolio), zero),
        earned_so_far=sum((held.interest_so_far for held in portfolio), zero),
        upcoming=tuple(upcoming),
    )


async def _coming_savings(
    session: AsyncSession, client: Actor, clock: Clock, currency: Currency, today: date
) -> list[Upcoming]:
    """The next payment of each active automatic plan, and each active goal's date.

    A plan that is a goal's saving rule shows as that goal's saving, named by the goal.
    """
    plans = [
        plan
        for plan in await autoinvest.client_plans(session, client)
        if plan.status is autoinvest.PlanStatus.ACTIVE
        and plan.next_run_on is not None
        and plan.next_run_on >= today
        and plan.amount.currency is currency
    ]
    open_goals = [
        g for g in await goals.active_goals(session, client) if g.target.currency is currency
    ]
    ruled = await saving_rules.goals_with_rules(session, [g.id for g in open_goals])
    goal_of_plan = {plan_id: goal_id for goal_id, plan_id in ruled.items()}
    names = {g.id: g.name for g in open_goals}
    products = (
        {p.code: p.name for p in await investments.client_products(session, clock)} if plans else {}
    )
    coming: list[Upcoming] = []
    for plan in plans:
        assert plan.next_run_on is not None
        goal_id = goal_of_plan.get(plan.id)
        coming.append(
            Upcoming(
                kind=UpcomingKind.GOAL_SAVING if goal_id else UpcomingKind.AUTOINVEST_RUN,
                on=plan.next_run_on,
                amount=plan.amount,
                plan_id=plan.id,
                goal_id=goal_id,
                product_name=products.get(plan.product_code, plan.product_code),
                goal_name=names.get(goal_id) if goal_id else None,
            )
        )
    coming.extend(
        Upcoming(
            kind=UpcomingKind.GOAL_DATE,
            on=goal.target_date,
            amount=goal.target,
            goal_id=goal.id,
            goal_name=goal.name,
        )
        for goal in open_goals
        if goal.target_date is not None and goal.target_date >= today
    )
    return coming


async def _monthly(
    session: AsyncSession,
    client_id: str,
    code: str,
    entry_type: str,
    currency: Currency,
    window: list[date],
    today: date,
) -> dict[date, Money]:
    totals = await statements.client_month_totals(
        session,
        client_id=client_id,
        code=code,
        currency=currency,
        date_from=window[0],
        date_to=min(month_end(window[-1]), today),
        entry_type=entry_type,
    )
    # Each of these accounts is a liability, so money arriving in it is a credit. The totals
    # include reversals of the same kind of entry, so each month is a net figure. A month whose
    # reversals outweigh what it added is negative, as it is on the statement.
    if code == PAID_OUT[0]:
        # Paid out is money leaving the in-flight account to the provider: a debit.
        return {total.month: total.debits - total.credits for total in totals}
    return {total.month: total.credits - total.debits for total in totals}
