"""Funding early withdrawals: where the cash comes from, and when a withdrawal that waits is
funded. Pure.

The buffer is read from the ledger as the cash in the trust bank account and the provider
wallets, less the client money that cash already stands behind. A withdrawal the investing
module has already handed over is part of that client money, so the buffer this module sees
already counts it as owed. A withdrawal is paid from the buffer when the buffer before it, the
buffer seen plus the withdrawal, is on or above the floor.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.modules.dealing.domain.liquidity import (
    BufferRules,
    DeskCash,
    Funding,
    InvalidCash,
    desk_cash,
    fund_early_withdrawal,
)
from app.shared.money import Currency, Money

# The ledger accounts that hold cash the desk controls.
CASH_CODES = ("bank.trust", "iotec.wallet")
# Client and tax money held in that cash that no bill stands behind: paid in and not started,
# handed to payouts and not yet left, and tax withheld and not yet paid to URA. Running
# investments are counted separately, against the bills held for each product.
CLAIM_CODES = (
    "client.awaiting_start",
    "client.payout_pending",
    "client.payout_in_flight",
    "tax.withholding_payable",
)


class Source(StrEnum):
    BUFFER = "buffer"
    SALE = "sale"


class FundingStatus(StrEnum):
    # Waiting for the buffer to recover, or for a sale.
    WAITING = "waiting"
    FUNDED = "funded"


@dataclass(frozen=True, slots=True)
class Decision:
    source: Source
    status: FundingStatus


def buffer_from(
    currency: Currency,
    cash: Mapping[str, Money],
    claims: Mapping[str, Money],
    to_place: Money,
) -> DeskCash:
    """The buffer from the ledger figures. `cash` and `claims` are keyed by account code and
    must name every code in CASH_CODES and CLAIM_CODES."""
    if set(cash) != set(CASH_CODES) or set(claims) != set(CLAIM_CODES):
        raise InvalidCash("Every cash and claim account is counted")
    figures = [*cash.values(), *claims.values(), to_place]
    if any(amount.currency is not currency for amount in figures):
        raise InvalidCash("Every figure is in the currency of the buffer")
    zero = Money.zero(currency)
    owed = sum(claims.values(), zero)
    if owed.is_negative:
        raise InvalidCash("Cash figures are never negative")
    return desk_cash(sum(cash.values(), zero), to_place, owed)


def decide(
    amount: Money, buffer: Money, rules: BufferRules, *, others_waiting_for_buffer: bool
) -> Decision:
    """Where one early withdrawal's cash comes from. `buffer` already counts it as owed.

    A withdrawal small enough for the buffer joins the end of the queue when earlier ones
    wait, so the queue is paid in the order asked.
    """
    funding = fund_early_withdrawal(amount, buffer + amount, rules)
    if funding is Funding.SECONDARY_SALE:
        return Decision(Source.SALE, FundingStatus.WAITING)
    if funding is Funding.QUEUED or others_waiting_for_buffer:
        return Decision(Source.BUFFER, FundingStatus.WAITING)
    return Decision(Source.BUFFER, FundingStatus.FUNDED)


def released_from_queue(waiting: Iterable[Money], buffer: Money, rules: BufferRules) -> int:
    """How many of the withdrawals queued for the buffer, oldest first, are funded now.

    `buffer` already counts every one of them as owed, so funding one does not change it. Each
    is funded while the buffer before it would be on or above the floor, and the queue stops at
    the first that is not, so nobody is paid ahead of an earlier request.
    """
    released = 0
    for amount in waiting:
        if fund_early_withdrawal(amount, buffer + amount, rules) is not Funding.FROM_BUFFER:
            break
        released += 1
    return released


def funded_by_sale(waiting: Iterable[Money], proceeds_left: Money) -> int:
    """How many of the withdrawals waiting for a sale, oldest first, one sale's proceeds fund.

    Stops at the first that the proceeds left do not cover, so nobody is paid ahead of an
    earlier request.
    """
    if proceeds_left.is_negative:
        raise InvalidCash("Proceeds are never negative")
    funded = 0
    left = proceeds_left
    for amount in waiting:
        if amount.currency is not left.currency or not amount.is_positive:
            raise InvalidCash("A withdrawal is a positive amount in the currency of the sale")
        if amount > left:
            break
        left -= amount
        funded += 1
    return funded
