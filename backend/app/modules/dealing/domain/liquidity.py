"""The liquidity buffer and how an early withdrawal is funded. Pure: no I/O.

Bills cannot be turned into cash the same day, so the desk keeps a buffer of cash. A small early
withdrawal is paid from the buffer at once. A large one is paid from a sale on the secondary
market. Both sources are used, and one withdrawal is never split between them.

The buffer is not a separate pot of client money and it is not a wallet. It is the cash the desk
controls beyond the client money that is waiting to be placed into bills.

The rules are versioned by effective date, and each version is proposed by one member of staff
and approved by another, like every other limit (docs/rules.md).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.shared.money import Currency, Money

MAX_PAY_WITHIN_WORKING_DAYS = 10


class InvalidBufferRules(ValueError):
    """A set of buffer rules that contradicts itself."""


class InvalidCash(ValueError):
    """Cash figures that cannot be used: negative, or in the wrong currency."""


@dataclass(frozen=True, slots=True)
class BufferRules:
    """One version of the rules for one currency."""

    effective_from: date
    # What the desk aims to hold.
    target: Money
    # Below this, new early withdrawals queue and the desk is alerted.
    floor: Money
    # The largest early withdrawal the buffer pays. Anything larger waits for a sale.
    from_buffer_up_to: Money
    # The promise to a client whose withdrawal waits for a sale.
    pay_within_working_days: int

    def __post_init__(self) -> None:
        if type(self.effective_from) is not date:
            raise InvalidBufferRules("The effective date is a calendar date")
        currency = self.target.currency
        for amount in (self.target, self.floor, self.from_buffer_up_to):
            if amount.currency is not currency:
                raise InvalidBufferRules("Every amount in a version is in the same currency")
            if not amount.is_positive:
                raise InvalidBufferRules("Every amount in the rules is positive")
        if self.floor > self.target:
            raise InvalidBufferRules("The floor cannot be above the target")
        if self.from_buffer_up_to > self.floor:
            # Otherwise a buffer sitting exactly on its floor could promise more than it holds.
            raise InvalidBufferRules("One withdrawal from the buffer cannot exceed the floor")
        if (
            type(self.pay_within_working_days) is not int
            or not 1 <= self.pay_within_working_days <= MAX_PAY_WITHIN_WORKING_DAYS
        ):
            raise InvalidBufferRules(
                f"Clients are paid within 1 to {MAX_PAY_WITHIN_WORKING_DAYS} working days"
            )

    @property
    def currency(self) -> Currency:
        return self.target.currency


def rules_in_force(versions: Iterable[BufferRules], on: date) -> BufferRules:
    """The version with the latest effective date that is not after `on`.

    No version in force is an error. It never means "no buffer needed".
    """
    in_force: BufferRules | None = None
    seen: set[date] = set()
    for version in versions:
        if version.effective_from in seen:
            raise InvalidBufferRules("Two versions of the rules share an effective date")
        seen.add(version.effective_from)
        if version.effective_from <= on and (
            in_force is None or version.effective_from > in_force.effective_from
        ):
            in_force = version
    if in_force is None:
        raise InvalidBufferRules("No buffer rules are in force on that date")
    return in_force


@dataclass(frozen=True, slots=True)
class DeskCash:
    """The cash the desk controls, split into what it is for."""

    # Cash beyond the client money waiting to be placed, less what is already promised.
    buffer: Money
    # Client money waiting to be placed that the cash does not cover. Zero on a normal day.
    # Above zero means cash has not arrived from a provider yet, or something is wrong.
    short: Money


def desk_cash(cash: Money, to_place: Money, promised: Money) -> DeskCash:
    """Work out the buffer.

    `cash` is the desk's cash in the ledger. `to_place` is client money waiting for a bill
    (`position.money_to_place`). `promised` is early withdrawals the buffer has agreed to pay
    that have not left yet. Client money is counted first: the buffer is only what is left.
    """
    for amount in (cash, to_place, promised):
        if amount.is_negative:
            raise InvalidCash("Cash figures are never negative")
    free = cash - to_place - promised
    zero = Money.zero(cash.currency)
    return DeskCash(buffer=free, short=zero) if not free.is_negative else DeskCash(zero, -free)


class BufferLevel(StrEnum):
    ON_TARGET = "on_target"
    UNDER_TARGET = "under_target"
    BELOW_FLOOR = "below_floor"


def buffer_level(buffer: Money, rules: BufferRules) -> BufferLevel:
    if buffer < rules.floor:
        return BufferLevel.BELOW_FLOOR
    return BufferLevel.UNDER_TARGET if buffer < rules.target else BufferLevel.ON_TARGET


class Funding(StrEnum):
    # Paid now, from the buffer.
    FROM_BUFFER = "from_buffer"
    # The desk sells bills first. The client is paid within the promised working days.
    SECONDARY_SALE = "secondary_sale"
    # Small enough for the buffer, but the buffer is below its floor. It waits, in the order
    # asked, until the buffer is back above the floor. The desk is alerted.
    QUEUED = "queued"


def fund_early_withdrawal(amount: Money, buffer: Money, rules: BufferRules) -> Funding:
    """Decide where the cash for one early withdrawal comes from.

    `buffer` is `DeskCash.buffer`, read under the same lock that records the promise, so two
    withdrawals cannot both be paid from the same cash. A buffer on or above its floor always
    holds enough for one withdrawal, because the rules keep the per-withdrawal amount at or
    below the floor.
    """
    if amount.currency is not rules.currency or buffer.currency is not rules.currency:
        raise InvalidCash("The withdrawal and the buffer are in the currency of the rules")
    if not amount.is_positive:
        raise InvalidCash("A withdrawal is a positive amount")
    if buffer.is_negative:
        raise InvalidCash("The buffer is never negative")
    if amount > rules.from_buffer_up_to:
        return Funding.SECONDARY_SALE
    return Funding.QUEUED if buffer < rules.floor else Funding.FROM_BUFFER


def to_raise(buffer: Money, rules: BufferRules, waiting_for_sale: Money) -> Money:
    """Cash the desk should raise by selling: enough to pay the withdrawals waiting for a sale
    and bring the buffer back to its target."""
    if waiting_for_sale.is_negative or buffer.is_negative:
        raise InvalidCash("Cash figures are never negative")
    gap = rules.target - buffer
    return waiting_for_sale + (gap if gap.is_positive else Money.zero(rules.currency))
