"""What the insights module offers: a client's own figures, after tax. Read-only.

Every figure comes from the ledger or from the investments module's client views, so insights
always agree with the books. Nothing here shows a tax rate, a tax amount or a rate before tax:
interest is read from the client's interest account, which holds interest after tax.

Other modules import from here. A client-facing route may call only `client_insights` and
`client_portfolio`.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.goals import service as goals
from app.modules.insights.domain.months import DEFAULT_MONTHS, month_end, months_ending
from app.modules.insights.domain.shares import whole_percentages
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

    on: date
    investment_id: str
    product_name: str
    at_maturity: AtMaturity | None
    # What the client gets back at maturity, after tax.
    amount: Money


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
        (
            Upcoming(
                on=held.maturity_date,
                investment_id=held.id,
                product_name=held.product_name,
                at_maturity=held.at_maturity,
                amount=held.at_maturity_returns.total,
            )
            for held in portfolio
            if held.maturity_date is not None
            and held.maturity_date >= today
            and held.at_maturity_returns is not None
        ),
        key=lambda item: (item.on, item.investment_id),
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


@dataclass(frozen=True, slots=True)
class Holding:
    """A part of what is invested: the client's own investments, or those saved to goals."""

    worth: Money
    count: int


@dataclass(frozen=True, slots=True)
class ProductShare:
    """What is invested in one product, and its whole-number share of everything invested."""

    product_code: str
    product_name: str
    worth: Money
    count: int
    percent: int


@dataclass(frozen=True, slots=True)
class NextMaturity:
    """The running investment that matures soonest."""

    investment_id: str
    product_name: str
    on: date
    at_maturity: AtMaturity | None
    term_days: int
    days_earned: int
    days_left: int


@dataclass(frozen=True, slots=True)
class ClientPortfolio:
    """The totals on Home and Portfolio, in one currency, after tax.

    Only running investments count. One waiting for payment has earned nothing and may lapse,
    so it is only counted in `awaiting_payment`.
    """

    currency: Currency
    # The principal put in, what it is worth today, and the interest it has earned so far.
    invested: Money
    worth: Money
    interest_so_far: Money
    # What the running investments add in one more day of interest.
    earning_per_day: Money
    # Added by today's run, once it has run.
    interest_today: Money
    active_count: int
    awaiting_payment_count: int
    # `worth` split by where it sits: saved to an open goal, or not. The two add up to `worth`.
    outside_goals: Holding
    in_goals: Holding
    # Open goals, whether or not anything is saved to them yet.
    open_goals: int
    # Biggest first. The percentages add up to 100 when anything is invested.
    products: tuple[ProductShare, ...]
    next_maturity: NextMaturity | None


async def client_portfolio(
    session: AsyncSession, client: Actor, clock: Clock, *, currency: Currency = Currency.UGX
) -> ClientPortfolio:
    """A client's own totals for Home and Portfolio.

    `session` is the client's own transaction. Every figure is the sum of the investments
    module's client views, so it agrees to the shilling with the investments the client sees.
    """
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    today = business_date(clock.now())
    held = [
        item
        for item in await investments.client_investments(session, client, clock)
        if item.amount.currency is currency
    ]
    running = [item for item in held if item.status is InvestmentStatus.ACTIVE]
    saved = await goals.open_goal_savings(session, client.id)
    zero = Money(0, currency)

    def total(items: list[investments.ClientInvestment]) -> Money:
        return sum((item.value for item in items), zero)

    in_goals = [item for item in running if item.id in saved]
    outside = [item for item in running if item.id not in saved]

    by_product: dict[str, list[investments.ClientInvestment]] = {}
    for item in running:
        by_product.setdefault(item.product_code, []).append(item)
    # Biggest first; the code breaks a tie so the order never changes between reads.
    groups = sorted(
        by_product.values(), key=lambda items: (-total(items).minor, items[0].product_code)
    )
    shares = whole_percentages([total(items).minor for items in groups])

    maturing = sorted(
        (
            item
            for item in running
            if item.maturity_date is not None
            and item.term_days is not None
            and item.maturity_date >= today
            and item.withdrawal_requested_at is None
        ),
        key=lambda item: (item.maturity_date, item.id),
    )
    first = maturing[0] if maturing else None
    return ClientPortfolio(
        currency=currency,
        invested=sum((item.amount for item in running), zero),
        worth=total(running),
        interest_so_far=sum((item.interest_so_far for item in running), zero),
        earning_per_day=sum((item.earning_per_day for item in running), zero),
        interest_today=sum((item.interest_today for item in running), zero),
        active_count=len(running),
        awaiting_payment_count=sum(
            1 for item in held if item.status is InvestmentStatus.AWAITING_PAYMENT
        ),
        outside_goals=Holding(total(outside), len(outside)),
        in_goals=Holding(total(in_goals), len(in_goals)),
        open_goals=await goals.open_goal_count(session, client.id),
        products=tuple(
            ProductShare(
                product_code=items[0].product_code,
                product_name=items[0].product_name,
                worth=total(items),
                count=len(items),
                percent=percent,
            )
            for items, percent in zip(groups, shares, strict=True)
        ),
        next_maturity=None
        if first is None or first.maturity_date is None or first.term_days is None
        else NextMaturity(
            investment_id=first.id,
            product_name=first.product_name,
            on=first.maturity_date,
            at_maturity=first.at_maturity,
            term_days=first.term_days,
            days_earned=first.days_earned,
            days_left=(first.maturity_date - today).days,
        ),
    )


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
