"""Allocating bills to client investments, oldest payment first. Pure: no I/O.

Bills are bought for the pool. Each purchase is then shared out to the investments that are
waiting for a bill, in the order the clients paid. The same rule shares out bills that are held
and have no investment behind them, which happens after an early withdrawal.

An allocation is an amount of what the bills cost. It never changes what a client is owed: that
is the ledger's job. It records which real bills stand behind which investment.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from app.shared.money import Money


class InvalidAllocation(ValueError):
    """Inputs that cannot be allocated."""


@dataclass(frozen=True, slots=True)
class Waiting:
    """A running investment with principal that has no bill behind it yet."""

    investment_id: str
    started_at: datetime
    # The part of the principal not yet allocated.
    amount: Money

    def __post_init__(self) -> None:
        if not self.investment_id:
            raise InvalidAllocation("An investment has an ID")
        if self.started_at.utcoffset() is None:
            raise InvalidAllocation("Times must be timezone-aware")
        if not self.amount.is_positive:
            raise InvalidAllocation("Only an investment that is waiting for money is listed")


@dataclass(frozen=True, slots=True)
class Allocation:
    investment_id: str
    amount: Money


@dataclass(frozen=True, slots=True)
class Allocated:
    allocations: tuple[Allocation, ...]
    # Cost with no investment behind it. It stays with the desk until new money comes in.
    left_over: Money


def allocate(available: Money, waiting: Iterable[Waiting]) -> Allocated:
    """Share `available`, the cost of unallocated bills, among the investments waiting.

    The oldest payment is served first and in full before the next gets anything, so only the
    last investment served can be part covered. Two payments at the same instant are ordered by
    investment ID, which makes the result the same on every run.
    """
    if available.is_negative:
        raise InvalidAllocation("The amount to allocate cannot be negative")
    queue = sorted(waiting, key=lambda item: (item.started_at, item.investment_id))
    if len({item.investment_id for item in queue}) != len(queue):
        raise InvalidAllocation("An investment is listed once")

    left = available
    allocations: list[Allocation] = []
    for item in queue:
        if left.is_zero:
            break
        share = item.amount if item.amount <= left else left
        allocations.append(Allocation(item.investment_id, share))
        left -= share
    return Allocated(tuple(allocations), left)
