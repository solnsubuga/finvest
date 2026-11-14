"""The allocation report for one product: which bills stand behind client investments, and what
is left over on either side. Pure: no I/O.

It reads the allocations as they stand. The worker brings them up to date every 15 minutes, so
for a short while after a purchase settles or an investment ends, the report shows cost not yet
shared out, or investments still waiting, that the next run settles.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from app.shared.money import Currency, Money


@dataclass(frozen=True, slots=True)
class LotHeld:
    """What is still held of one purchase."""

    lot_id: str
    settled_on: date
    matures_on: date
    custodian_reference: str
    face: Money
    cost: Money


@dataclass(frozen=True, slots=True)
class Allocated:
    """Some of a lot's cost standing behind one investment."""

    lot_id: str
    investment_id: str
    cost: Money


@dataclass(frozen=True, slots=True)
class Owed:
    """A running investment and the principal it is owed."""

    investment_id: str
    principal: Money


@dataclass(frozen=True, slots=True)
class LotLine:
    lot_id: str
    settled_on: date
    matures_on: date
    custodian_reference: str
    face: Money
    cost: Money
    allocated: Money
    # Cost no investment stands behind yet.
    unallocated: Money
    investments: int


@dataclass(frozen=True, slots=True)
class ProductReport:
    product_code: str
    currency: Currency
    # Oldest purchase first.
    lots: tuple[LotLine, ...]
    cost_held: Money
    allocated: Money
    unallocated: Money
    # Running investments, what they are owed, and the part with no bill behind it yet.
    investments: int
    owed: Money
    covered: Money
    investments_waiting: int
    waiting: Money


def product_report(
    product_code: str,
    currency: Currency,
    lots: Iterable[LotHeld],
    allocations: Iterable[Allocated],
    owed: Iterable[Owed],
) -> ProductReport:
    """The report for one product. Lots keep the order given, which is oldest purchase first.

    Allocations against a lot that is no longer held, or for an investment no longer running,
    count in neither total: the next run releases them."""
    zero = Money.zero(currency)
    lots = list(lots)
    running = {item.investment_id: item.principal for item in owed}
    held = {lot.lot_id for lot in lots}
    by_lot: defaultdict[str, Money] = defaultdict(lambda: zero)
    investments_by_lot: defaultdict[str, set[str]] = defaultdict(set)
    by_investment: defaultdict[str, Money] = defaultdict(lambda: zero)
    for item in allocations:
        if item.lot_id not in held or item.investment_id not in running:
            continue
        by_lot[item.lot_id] += item.cost
        investments_by_lot[item.lot_id].add(item.investment_id)
        by_investment[item.investment_id] += item.cost

    lines = tuple(
        LotLine(
            lot_id=lot.lot_id,
            settled_on=lot.settled_on,
            matures_on=lot.matures_on,
            custodian_reference=lot.custodian_reference,
            face=lot.face,
            cost=lot.cost,
            allocated=by_lot[lot.lot_id],
            unallocated=_at_least_zero(lot.cost - by_lot[lot.lot_id], zero),
            investments=len(investments_by_lot[lot.lot_id]),
        )
        for lot in lots
    )
    gaps = [
        _at_least_zero(principal - by_investment[investment_id], zero)
        for investment_id, principal in running.items()
    ]
    return ProductReport(
        product_code=product_code,
        currency=currency,
        lots=lines,
        cost_held=_total((line.cost for line in lines), zero),
        allocated=_total((line.allocated for line in lines), zero),
        unallocated=_total((line.unallocated for line in lines), zero),
        investments=len(running),
        owed=_total(running.values(), zero),
        # Never more than an investment is owed, though for a moment more may stand behind it.
        covered=_total(
            (
                min(by_investment[investment_id], principal, key=lambda money: money.minor)
                for investment_id, principal in running.items()
            ),
            zero,
        ),
        investments_waiting=sum(1 for gap in gaps if gap.is_positive),
        waiting=_total(gaps, zero),
    )


def _total(amounts: Iterable[Money], zero: Money) -> Money:
    total = zero
    for amount in amounts:
        total += amount
    return total


def _at_least_zero(amount: Money, zero: Money) -> Money:
    return amount if amount.is_positive else zero
