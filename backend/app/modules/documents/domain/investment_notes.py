"""The note that confirms an investment started. Pure.

One note for each investment, issued once, on the day the money goes to work. It shows what
the client invested, the yearly rate after tax, the dates, and what the client gets back at
maturity after tax. Like every client figure it is after tax: it never shows a tax rate or a
tax amount.
"""

from dataclasses import dataclass
from datetime import date

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
