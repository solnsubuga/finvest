"""Withdrawal limits. Pure: no I/O.

Limits are versioned. Each version has an effective date, and the service stores who proposed
and who approved it, so the limit applied to any past payout can always be shown. The check
never rounds and never converts: amounts and limits are `Money` in the same currency.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum

from app.modules.payouts.domain.destinations import DestinationKind
from app.shared.money import Currency, Money

# Uganda keeps East Africa Time all year. A client's "today" is the Kampala calendar day.
KAMPALA = timezone(timedelta(hours=3), "EAT")

# The most a mobile money wallet accepts in one transfer.
MOBILE_MONEY_PER_PAYOUT_UGX = Money(5_000_000, Currency.UGX)


def business_date(now: datetime) -> date:
    """The Kampala calendar day that a moment falls in."""
    if now.utcoffset() is None:
        raise ValueError("Times must be timezone-aware")
    return now.astimezone(KAMPALA).date()


class InvalidLimits(ValueError):
    """A set of limits that contradicts itself."""


@dataclass(frozen=True, slots=True)
class WithdrawalLimits:
    """One version of the limits for one currency. `None` means no limit of that sort."""

    effective_from: date
    mobile_money_per_payout: Money
    mobile_money_per_day: Money | None = None
    bank_per_payout: Money | None = None
    bank_per_day: Money | None = None
    # A payout above this is held until a staff member approves it.
    review_above: Money | None = None

    def __post_init__(self) -> None:
        currency = self.mobile_money_per_payout.currency
        if currency is not Currency.UGX:
            raise InvalidLimits("Mobile money pays in Uganda shillings only")
        for limit in self._all():
            if limit.currency is not currency:
                raise InvalidLimits("Every limit in a version is in the same currency")
            if not limit.is_positive:
                raise InvalidLimits("A limit is a positive amount")
        if self.mobile_money_per_payout > MOBILE_MONEY_PER_PAYOUT_UGX:
            raise InvalidLimits("Mobile money cannot carry more than 5,000,000 in one payout")
        for per_payout, per_day in (
            (self.mobile_money_per_payout, self.mobile_money_per_day),
            (self.bank_per_payout, self.bank_per_day),
        ):
            if per_payout is not None and per_day is not None and per_day < per_payout:
                raise InvalidLimits("A daily limit cannot be below the limit for one payout")

    @property
    def currency(self) -> Currency:
        return self.mobile_money_per_payout.currency

    def _all(self) -> tuple[Money, ...]:
        optional = (
            self.mobile_money_per_day,
            self.bank_per_payout,
            self.bank_per_day,
            self.review_above,
        )
        return (self.mobile_money_per_payout, *(limit for limit in optional if limit is not None))


def limits_in_force(versions: Iterable[WithdrawalLimits], on: date) -> WithdrawalLimits:
    """The version with the latest effective date that is not after `on`."""
    in_force: WithdrawalLimits | None = None
    seen: set[date] = set()
    for version in versions:
        if version.effective_from in seen:
            raise InvalidLimits("Two versions of the limits share an effective date")
        seen.add(version.effective_from)
        if version.effective_from <= on and (
            in_force is None or version.effective_from > in_force.effective_from
        ):
            in_force = version
    if in_force is None:
        raise InvalidLimits("No withdrawal limits are in force on that date")
    return in_force


class Refusal(StrEnum):
    AMOUNT_NOT_POSITIVE = "amount_not_positive"
    CURRENCY_NOT_SUPPORTED = "currency_not_supported"
    # The client can still withdraw this amount to a bank account.
    OVER_MOBILE_MONEY_LIMIT = "over_mobile_money_limit"
    OVER_BANK_LIMIT = "over_bank_limit"
    OVER_DAILY_LIMIT = "over_daily_limit"


@dataclass(frozen=True, slots=True)
class WithdrawalDecision:
    refusal: Refusal | None = None
    # Allowed, but held for a staff member before anything is sent.
    needs_review: bool = False

    @property
    def allowed(self) -> bool:
        return self.refusal is None


def check_withdrawal(
    limits: WithdrawalLimits,
    kind: DestinationKind,
    amount: Money,
    already_today: Money,
) -> WithdrawalDecision:
    """Decide whether one payout fits the limits.

    `already_today` is what the client has withdrawn to this kind of destination on the same
    Kampala day, counting every payout that was not cancelled or failed. The service reads it
    and inserts the new payout in one transaction under a lock on the client, so two requests
    cannot both fit under the same remaining allowance.
    """
    if amount.currency is not limits.currency or already_today.currency is not limits.currency:
        return WithdrawalDecision(Refusal.CURRENCY_NOT_SUPPORTED)
    if not amount.is_positive:
        return WithdrawalDecision(Refusal.AMOUNT_NOT_POSITIVE)
    if already_today.is_negative:
        raise ValueError("The amount already withdrawn today cannot be negative")

    if kind is DestinationKind.MOBILE_MONEY:
        per_payout: Money | None = limits.mobile_money_per_payout
        per_day = limits.mobile_money_per_day
        over = Refusal.OVER_MOBILE_MONEY_LIMIT
    else:
        per_payout, per_day, over = (
            limits.bank_per_payout,
            limits.bank_per_day,
            Refusal.OVER_BANK_LIMIT,
        )

    if per_payout is not None and amount > per_payout:
        return WithdrawalDecision(over)
    if per_day is not None and already_today + amount > per_day:
        return WithdrawalDecision(Refusal.OVER_DAILY_LIMIT)
    return WithdrawalDecision(
        needs_review=limits.review_above is not None and amount > limits.review_above
    )
