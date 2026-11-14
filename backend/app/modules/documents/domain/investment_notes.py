"""The notes that confirm an investment started and ended. Pure.

One note for each investment when the money goes to work, and one when it ends: at maturity,
paid out or rolled over, or withdrawn early. The first shows what the client invested, the
yearly rate after tax, the dates and what comes back at maturity. The second shows what the
investment earned and came to, and where the money went. Like every client figure they are
after tax: neither shows a tax rate or a tax amount.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.shared.money import Money


@dataclass(frozen=True, slots=True)
class AtMaturityFigures:
    interest: Money  # after tax
    total: Money  # the amount invested plus that interest


@dataclass(frozen=True, slots=True)
class InvestmentNote:
    reference: str  # "IN-" and the note's ID
    client_name: str
    account_number: str  # ten digits
    investment_id: str
    product_name: str  # "91 Day Treasury Bill"
    amount: Money
    rate_percent: str  # "10.63": the yearly rate after tax
    start_date: date
    # None for an open term, which earns until the client withdraws.
    term_days: int | None
    maturity_date: date | None
    at_maturity: AtMaturityFigures | None
    # "Roll over into a new investment" or "Pay out", for a fixed term.
    then: str | None

    def __post_init__(self) -> None:
        if not self.amount.is_positive:
            raise ValueError("An investment note is for an amount above zero")
        fixed = (self.term_days, self.maturity_date, self.at_maturity, self.then)
        if any(value is None for value in fixed) and any(value is not None for value in fixed):
            raise ValueError("A fixed term has a term, a maturity date, figures and a choice")
        if self.maturity_date is not None and self.maturity_date <= self.start_date:
            raise ValueError("An investment matures after it starts")
        if self.at_maturity is not None and self.at_maturity.total != (
            self.amount + self.at_maturity.interest
        ):
            raise ValueError("What comes back is the amount invested plus the interest")

    @property
    def is_open_term(self) -> bool:
        return self.term_days is None


class Ending(StrEnum):
    """How an investment ended."""

    PAID_OUT = "paid_out"  # matured, and the money is on its way to the client
    ROLLED_OVER = "rolled_over"  # matured into a new investment
    WAITING = "waiting"  # matured, and waits for the client to say where to send it
    WITHDRAWN = "withdrawn"  # withdrawn early at the client's request


@dataclass(frozen=True, slots=True)
class EndedNote:
    """The note that confirms an investment ended, and what it paid, after tax."""

    reference: str  # "IN-" and the note's ID
    client_name: str
    account_number: str
    investment_id: str
    product_name: str
    amount: Money  # what was invested
    interest: Money  # earned after tax, up to the end
    start_date: date
    ended_on: date
    ending: Ending
    # The investment a rollover opened.
    rolled_into_id: str | None = None

    def __post_init__(self) -> None:
        if not self.amount.is_positive:
            raise ValueError("An investment note is for an amount above zero")
        if self.interest.minor < 0:
            raise ValueError("Interest earned is never below zero")
        if self.ended_on < self.start_date:
            raise ValueError("An investment ends on or after it starts")
        if (self.ending is Ending.ROLLED_OVER) != (self.rolled_into_id is not None):
            raise ValueError("A rollover names the investment it opened, and only a rollover")

    @property
    def total(self) -> Money:
        """What the investment came to: the amount invested plus the interest after tax."""
        return self.amount + self.interest
