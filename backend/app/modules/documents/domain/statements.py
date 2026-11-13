"""A client statement: what a client held, paid in, earned and was paid over a period. Pure.

A statement is for one client and covers every investment they hold, under their account
number. There is no wallet, so the figures are the value of the client's investments and of
money on its way in or out, read from the ledger. Every figure is after tax: a client never
sees a tax rate, a tax amount or a rate before tax on a statement.

The figures must add up. For each currency, the value at the start, plus what was paid in,
plus what was earned, minus what was paid out, plus any correction, is the value at the end.
A statement that does not add up is refused before it is drawn, so a wrong one never reaches
a client.
"""

import calendar
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from app.modules.documents.domain import formatting
from app.shared.money import Currency, Money

# The longest period a client may ask for at once.
MAX_PERIOD_DAYS = 366


class StatementKind(StrEnum):
    MONTHLY = "monthly"  # sent to every client after each calendar month
    REQUESTED = "requested"  # a period the client chose in the app


class ActivityKind(StrEnum):
    PAID_IN = "paid_in"  # a payment received for an investment
    EARNED = "earned"  # interest, after tax
    PAID_OUT = "paid_out"  # a payout to the client's mobile money or bank account
    CORRECTION = "correction"  # a correction staff approved


class PeriodRefused(ValueError):
    """The period cannot be put on a statement. `code` is what the API returns."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class StatementDoesNotAddUp(ValueError):
    """The figures read from the ledger do not reconcile. A bug or a data problem, never sent."""


@dataclass(frozen=True, slots=True)
class Period:
    start: date
    end: date  # included

    @property
    def label(self) -> str:
        """`October 2026` for a whole calendar month, else `1 Aug 2026 to 13 Nov 2026`."""
        if self.start == self.start.replace(day=1) and self.end == month_end(self.start):
            return formatting.month(self.start)
        return f"{formatting.day(self.start)} to {formatting.day(self.end)}"


def month_end(any_day: date) -> date:
    return any_day.replace(day=calendar.monthrange(any_day.year, any_day.month)[1])


def previous_month(today: date) -> Period:
    """The calendar month before the one `today` is in: what the monthly run covers."""
    last = today.replace(day=1) - timedelta(days=1)
    return Period(last.replace(day=1), last)


def requested_period(start: date, end: date, *, today: date) -> Period:
    """A period a client asked for, or `PeriodRefused` saying why not."""
    if start > end:
        raise PeriodRefused("period_backwards", "The start date is after the end date")
    if end > today:
        raise PeriodRefused("period_in_future", "A statement cannot end after today")
    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        raise PeriodRefused("period_too_long", "A statement covers at most one year")
    return Period(start, end)


@dataclass(frozen=True, slots=True)
class Activity:
    """One line in the statement's list of what happened, in the client's terms."""

    on: date
    kind: ActivityKind
    description: str  # "Payment received, MTN Mobile Money" or "Interest, 91 Day Treasury Bill"
    reference: str  # the Finvest reference the client sees on the receipt or payout
    amount: Money  # signed: positive adds to the client's value, negative takes from it

    def __post_init__(self) -> None:
        if self.amount.is_zero:
            raise ValueError("An activity line moves money")
        if self.kind in (ActivityKind.PAID_IN, ActivityKind.EARNED) and self.amount.is_negative:
            raise ValueError("Money paid in or earned adds to the client's value")
        if self.kind is ActivityKind.PAID_OUT and self.amount.is_positive:
            raise ValueError("Money paid out takes from the client's value")


@dataclass(frozen=True, slots=True)
class Holding:
    """One investment as it stood at the end of the period."""

    product: str  # "91 Day Treasury Bill"
    reference: str  # the investment reference the client sees
    started_on: date | None
    matures_on: date | None
    status: str  # "Running", "Matured", "Paid out", "Waiting for payment"
    invested: Money
    earned_in_period: Money  # after tax
    value_at_end: Money


@dataclass(frozen=True, slots=True)
class Summary:
    """One currency's figures. Built by `summarise`, which checks that they add up."""

    currency: Currency
    opening: Money
    paid_in: Money
    earned: Money
    paid_out: Money  # a positive amount: what left
    corrections: Money  # signed
    closing: Money


def summarise(
    currency: Currency, *, opening: Money, closing: Money, activity: Iterable[Activity]
) -> Summary:
    """Totals for one currency, or `StatementDoesNotAddUp` if the lines do not explain the change.

    `opening` is the client's value at the end of the day before the period, and `closing` at
    the end of its last day, both read from the ledger independently of the lines.
    """
    zero = Money.zero(currency)
    totals = dict.fromkeys(ActivityKind, zero)
    for line in activity:
        if line.amount.currency is not currency:
            continue
        totals[line.kind] = totals[line.kind] + line.amount
    summary = Summary(
        currency=currency,
        opening=opening,
        paid_in=totals[ActivityKind.PAID_IN],
        earned=totals[ActivityKind.EARNED],
        paid_out=-totals[ActivityKind.PAID_OUT],
        corrections=totals[ActivityKind.CORRECTION],
        closing=closing,
    )
    explained = opening + summary.paid_in + summary.earned - summary.paid_out + summary.corrections
    if explained != closing:
        raise StatementDoesNotAddUp(f"The {currency} lines do not explain the change in value")
    return summary


@dataclass(frozen=True, slots=True)
class Statement:
    reference: str  # "ST-" and the statement's ID, printed on every page
    kind: StatementKind
    client_name: str
    account_number: str  # ten digits
    period: Period
    issued_on: date
    summaries: tuple[Summary, ...]
    holdings: tuple[Holding, ...]
    activity: tuple[Activity, ...]  # in date order

    def __post_init__(self) -> None:
        formatting.account_number(self.account_number)
        if not self.summaries:
            raise ValueError("A statement has at least one currency")
        if any(not self.period.start <= line.on <= self.period.end for line in self.activity):
            raise ValueError("An activity line falls outside the period")
        if any(
            later.on < earlier.on
            for earlier, later in zip(self.activity, self.activity[1:], strict=False)
        ):
            raise ValueError("Activity lines are in date order")

    @property
    def is_empty(self) -> bool:
        """Nothing held at any point and nothing happened: no monthly statement is sent."""
        return not self.activity and all(
            summary.opening.is_zero and summary.closing.is_zero for summary in self.summaries
        )


def group_interest(lines: Sequence[Activity]) -> list[Activity]:
    """Daily interest folded into one line per investment per calendar month.

    Interest is posted every day, so a year's statement would otherwise list hundreds of lines.
    Each month's line is dated the last day of that month inside the period, which is also the
    day its last posting can fall on. Other lines are kept as they are.
    """
    folded: dict[tuple[str, str, int, int, Currency], Activity] = {}
    kept: list[Activity] = []
    for line in lines:
        if line.kind is not ActivityKind.EARNED:
            kept.append(line)
            continue
        key = (line.description, line.reference, line.on.year, line.on.month, line.amount.currency)
        earlier = folded.get(key)
        if earlier is None:
            folded[key] = line
        else:
            folded[key] = Activity(
                on=max(earlier.on, line.on),
                kind=ActivityKind.EARNED,
                description=line.description,
                reference=line.reference,
                amount=earlier.amount + line.amount,
            )
    return sorted([*kept, *folded.values()], key=lambda line: (line.on, _ORDER[line.kind]))


# Within a day: money in, then what it earned, then corrections, then what left.
_ORDER = {
    ActivityKind.PAID_IN: 0,
    ActivityKind.EARNED: 1,
    ActivityKind.CORRECTION: 2,
    ActivityKind.PAID_OUT: 3,
}
