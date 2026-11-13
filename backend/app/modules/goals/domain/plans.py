"""The plan for a goal: how much to save each month to reach it by its date. Pure: no I/O.

A plan is an estimate the client is shown, never a promise and never a figure money is moved
by. It is worked out at today's rates, after tax, with these simplifications, each of which
can only make the estimate lower than what really happens:

* A saving earns interest only for whole terms that end on or before the goal's date. Days
  after the last whole term earn nothing in the estimate.
* Interest is simple. What a rollover adds by reinvesting interest is left out.
* What the goal is already worth earns nothing more in the estimate.

Savings are monthly, from today, on the same day of the month (the 28th at the latest, so every
month has the day). A saving is planned only if its first term ends by the goal's date, so the
money is back in time.

Every saving is its own investment and must meet the product's minimum. Nothing waits in a pot.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.modules.goals.domain.goals import saving_step
from app.modules.investments.domain.interest import Terms, client_returns
from app.shared.money import Money, Rounding

# A plan is for one to ten years of monthly savings at most.
MAX_SAVINGS = 12 * 10
LAST_SAVING_DAY = 28


@dataclass(frozen=True, slots=True)
class PlanProduct:
    """A product a plan can use: a fixed term that is live today, with today's rate.

    The rate before tax and the tax are here only so the arithmetic can be done. Nothing built
    from them but net amounts and `rate_percent` ever reaches a client.
    """

    code: str
    name: str
    term_days: int
    minimum: Money
    annual_rate_bps: int
    tax_bps: int
    # "10.63": the yearly rate after tax, as a label.
    rate_percent: str


@dataclass(frozen=True, slots=True)
class Plan:
    product: PlanProduct
    # The monthly saving, rounded up to a whole thousand shillings or a whole dollar.
    saving: Money
    savings: int
    first_on: date
    last_on: date
    # What the client pays in over the plan, and what interest adds, both after tax.
    put_in: Money
    interest: Money
    # The monthly saving needed with no interest at all, for comparison.
    without_investing: Money
    # The saving the target needs is below the product minimum, so the plan saves the minimum
    # and reaches the target early.
    raised_to_minimum: bool


def add_months(start: date, months: int) -> date:
    """The same day of the month `months` later, on the 28th at the latest."""
    index = start.month - 1 + months
    return date(start.year + index // 12, index % 12 + 1, min(start.day, LAST_SAVING_DAY))


def saving_dates(today: date, target_date: date, term_days: int) -> list[date]:
    """The monthly saving days whose first term ends on or before the goal's date."""
    dates: list[date] = []
    while len(dates) < MAX_SAVINGS:
        day = today if not dates else add_months(today, len(dates))
        if (target_date - day).days < term_days:
            break
        dates.append(day)
    return dates


def interest_by(saving: Money, product: PlanProduct, day: date, target_date: date) -> Money:
    """Interest after tax that one saving made on `day` earns by the goal's date, in whole terms."""
    terms = (target_date - day).days // product.term_days
    if terms <= 0:
        return Money.zero(saving.currency)
    days = terms * product.term_days
    return client_returns(Terms(saving, product.annual_rate_bps, product.tax_bps), days).interest


def reached_with(
    saving: Money, product: PlanProduct, dates: Sequence[date], target_date: date
) -> Money:
    """What `saving` on each of `dates` is worth on the goal's date, by the estimate."""
    total = Money.zero(saving.currency)
    for day in dates:
        total = total + saving + interest_by(saving, product, day, target_date)
    return total


def round_up_to(amount: Money, step: Money) -> Money:
    return amount.mul_ratio(1, step.minor, Rounding.UP).mul_ratio(step.minor, 1, Rounding.UP)


def plan_for(product: PlanProduct, needed: Money, today: date, target_date: date) -> Plan | None:
    """The smallest monthly saving with this product that reaches `needed` by the date.

    `None` if not even one saving's term fits before the date.
    """
    dates = saving_dates(today, target_date, product.term_days)
    if not dates or not needed.is_positive:
        return None
    step = saving_step(needed.currency)
    without = round_up_to(needed.mul_ratio(1, len(dates), Rounding.UP), step)
    # Saving `without` each month reaches the target with no interest at all, so the answer is
    # at most that. Search the whole steps below it: more saved never reaches less.
    low, high = 1, without.minor // step.minor
    while low < high:
        middle = (low + high) // 2
        candidate = Money(middle * step.minor, needed.currency)
        if reached_with(candidate, product, dates, target_date) >= needed:
            high = middle
        else:
            low = middle + 1
    saving = Money(high * step.minor, needed.currency)
    raised = saving < product.minimum
    if raised:
        saving = product.minimum
    put_in = saving.mul_ratio(len(dates), 1, Rounding.DOWN)
    return Plan(
        product=product,
        saving=saving,
        savings=len(dates),
        first_on=dates[0],
        last_on=dates[-1],
        put_in=put_in,
        interest=reached_with(saving, product, dates, target_date) - put_in,
        without_investing=without,
        raised_to_minimum=raised,
    )


def best_plan(
    products: Sequence[PlanProduct], needed: Money, today: date, target_date: date
) -> Plan | None:
    """The plan a goal is shown: the one that asks the client for the smallest monthly saving.

    A longer term pays more, but fewer savings fit before the date, so it can need a larger
    monthly amount. A product whose minimum is above what is needed asks for its minimum. Of
    plans asking for the same saving, the one where interest adds more wins, then the longer
    term.

    `None` when no product's term fits before the date, or nothing more is needed.
    """
    plans = [
        plan
        for product in products
        if product.minimum.currency is needed.currency
        and (plan := plan_for(product, needed, today, target_date)) is not None
    ]
    if not plans:
        return None
    return min(
        plans,
        key=lambda p: (p.saving.minor, -p.interest.minor, -p.product.term_days, p.product.code),
    )


def product_for_goal(
    products: Sequence[PlanProduct], needed: Money, today: date, target_date: date | None
) -> PlanProduct | None:
    """Where a goal's savings are invested.

    With a date, the product of its plan, and `None` when no term ends by that date. With no
    date, or nothing more needed, the shortest term in the goal's currency, which keeps money
    closest to hand.
    """
    if target_date is not None and needed.is_positive:
        plan = best_plan(products, needed, today, target_date)
        return None if plan is None else plan.product
    same = [p for p in products if p.minimum.currency is needed.currency]
    if not same:
        return None
    return min(same, key=lambda p: (p.term_days, -p.annual_rate_bps, p.code))
