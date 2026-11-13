"""Turning ledger changes into the lines and holdings a client reads on a statement. Pure.

The input is what the ledger says changed on the client's own accounts, one change per
journal entry and currency, and the names of the things those entries are about. Moves
between a client's own accounts, such as a payment starting an investment, change nothing the
client is owed and are left out. Every other change becomes exactly one line, so the lines
always explain the whole change in value, and `summarise` can check that against the balances.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from app.modules.documents.domain.statements import Activity, ActivityKind, Holding, Period
from app.modules.investments.domain.investments import InvestmentStatus
from app.shared.money import Currency, Money

DEPOSIT_CONFIRMED = "payment.deposit_confirmed"
INTEREST_ACCRUED = "investment.interest_accrued"
PAYOUT_PREFIX = "payout."

_METHODS = {"mtn": "MTN Mobile Money", "airtel": "Airtel Money", "bank_transfer": "Bank transfer"}
_DESTINATIONS = {"mobile_money": "your mobile money", "bank": "your bank account"}


def method_name(method: str) -> str:
    """How a client knows a way of paying: `MTN Mobile Money`."""
    return _METHODS.get(method, "Bank transfer")


def destination_name(kind: str) -> str:
    return _DESTINATIONS.get(kind, "your account")


@dataclass(frozen=True, slots=True)
class EntryChange:
    """What one journal entry changed in what Finvest owes the client, in one currency."""

    entry_id: str
    on: date
    entry_type: str
    source_type: str | None
    source_id: str | None
    owed: Money  # positive when the client is owed more
    investment_ids: tuple[str, ...]  # account references of the client lines it touched


@dataclass(frozen=True, slots=True)
class Names:
    products: Mapping[str, str]  # investment ID -> product name
    deposit_methods: Mapping[str, str]  # deposit ID -> method, as stored ("mtn")
    payout_kinds: Mapping[str, str]  # payout ID -> destination kind ("bank")


def activity_line(change: EntryChange, names: Names) -> Activity | None:
    """The line for one change, or None when it moved money between the client's accounts."""
    owed = change.owed
    if owed.is_zero:
        return None
    reference = change.source_id or change.entry_id
    if change.entry_type == DEPOSIT_CONFIRMED and owed.is_positive:
        method = names.deposit_methods.get(reference)
        description = f"Payment received, {method_name(method)}" if method else "Payment received"
        return Activity(change.on, ActivityKind.PAID_IN, description, reference, owed)
    if change.entry_type == INTEREST_ACCRUED and owed.is_positive:
        investment = change.investment_ids[0] if len(change.investment_ids) == 1 else reference
        product = names.products.get(investment, "your investment")
        return Activity(change.on, ActivityKind.EARNED, f"Interest, {product}", investment, owed)
    if change.entry_type.startswith(PAYOUT_PREFIX):
        if owed.is_negative:
            where = destination_name(names.payout_kinds.get(reference, ""))
            return Activity(
                change.on, ActivityKind.PAID_OUT, f"Paid out to {where}", reference, owed
            )
        return Activity(change.on, ActivityKind.CORRECTION, "Payout returned", reference, owed)
    return Activity(change.on, ActivityKind.CORRECTION, "Correction", reference, owed)


@dataclass(frozen=True, slots=True)
class InvestmentFacts:
    """What the investments module says about one investment."""

    id: str
    product: str
    principal: Money
    status: InvestmentStatus
    start_date: date | None
    maturity_date: date | None
    # At maturity: the investment its money rolled into, and the payout carrying it, if any.
    rolled_into_id: str | None = None
    payout_id: str | None = None


# The one place an investment's status becomes words on a statement. Every status has a label,
# and a unit test fails if a new one is added without one.
STATUS_LABELS: dict[InvestmentStatus, str] = {
    InvestmentStatus.AWAITING_PAYMENT: "Payment received, starting",
    InvestmentStatus.ACTIVE: "Running",
    InvestmentStatus.CANCELLED: "Cancelled",
    InvestmentStatus.MATURED: "Matured",
    InvestmentStatus.WITHDRAWN: "Withdrawn early",
}


def holding_status(facts: InvestmentFacts, *, period: Period, value_at_end: Money) -> str:
    """How an investment stood at the end of the period, in words.

    The status is read today, so an investment that has matured since the period ended is
    shown as it was then. At maturity its money leaves the holding: into a new investment when
    it rolls over (shown as a holding of its own), or to the client's payout account, where it
    still counts as theirs until the payout is sent.
    """
    matured_by_then = facts.maturity_date is not None and facts.maturity_date <= period.end
    if facts.status is InvestmentStatus.MATURED and matured_by_then:
        if facts.rolled_into_id is not None:
            return "Matured and reinvested"
        if value_at_end.is_zero:
            return "Matured and paid out"
        if facts.payout_id is not None:
            return "Matured, being paid out"
        return "Matured, waiting to be paid out"
    if value_at_end.is_zero:
        return "Paid out"
    if facts.status is InvestmentStatus.MATURED:
        # Matured after the period ended: it was still running then.
        return STATUS_LABELS[InvestmentStatus.ACTIVE]
    if facts.status is InvestmentStatus.ACTIVE and matured_by_then:
        return "Matured"
    return STATUS_LABELS[facts.status]


def holding(
    facts: InvestmentFacts, *, period: Period, value_at_end: Money, earned: Money
) -> Holding:
    status = holding_status(facts, period=period, value_at_end=value_at_end)
    return Holding(
        product=facts.product,
        reference=facts.id,
        started_on=facts.start_date,
        matures_on=facts.maturity_date,
        status=status,
        invested=facts.principal,
        earned_in_period=earned,
        value_at_end=value_at_end,
    )


def totals_by_currency(amounts: Iterable[Money]) -> dict[Currency, Money]:
    totals: dict[Currency, Money] = {}
    for amount in amounts:
        totals[amount.currency] = totals.get(amount.currency, Money.zero(amount.currency)) + amount
    return totals
