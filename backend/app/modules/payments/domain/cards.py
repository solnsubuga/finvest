"""Card payments: the rules a card deposit follows on top of every deposit's. Pure: no I/O.

A card is paid on the card provider's own hosted page, never in our apps, so no card number
reaches Finvest. A card deposit uses the mobile money states with one difference in meaning:
`awaiting_approval` means the payer has the provider's page to pay on.

    created ──► sending ──► awaiting_approval ──► confirmed
       │           │              │   │
       └─► failed ◄┘              │   └─► failed
                                  └─► uncertain ──► confirmed or failed

What is different from mobile money, and why:

* A card can be charged only on the page whose address the provider gave in its answer to our
  send. A send whose answer was lost, or a deposit left `sending` by a crash, therefore cannot
  have been paid: it fails as `provider_unavailable`, and the client may try again. It is never
  sent again, so a second page never exists for the same deposit.
* The payer may take a few minutes typing a card and passing 3-D Secure. A page left unpaid for
  `CARD_WINDOW` is cancelled at the provider and, once the provider says it cannot be paid, the
  deposit fails as `approval_timed_out`. When we cannot tell, it is `uncertain`, read for a day
  and then settled by reconciliation, as for mobile money.
* The client may be asked to pay the card charge on top of the investment. The charge is worked
  out here in whole shillings, from a rate staff set on Settings. The client only ever sees the
  shillings. The provider collects the total; the client's investment is credited the amount.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.modules.payments.domain.deposits import Code, DepositStatus, InvalidDeposit, Move
from app.modules.payments.domain.outcomes import (
    Confirmed,
    Deferred,
    InvalidOutcome,
    NotFound,
    Pending,
    Rejected,
    StatusReading,
    Unavailable,
    Uncertain,
)
from app.shared.money import Currency, Money

# Providers whose payer pays on their own hosted page. A card always goes through one; mobile
# money does when Settings says Pesapal collects it.
HOSTED_PROVIDERS = frozenset({"pesapal", "card_sandbox"})
# How long a payer has to pay on the provider's page before the order is cancelled.
CARD_WINDOW = timedelta(minutes=30)
# Seconds to wait before each status read while the payer has the page. The sweep reads every
# minute after these run out.
CARD_POLL_DELAYS = (20, 20, 20, 30, 30, 60)
# Rates are in basis points: 350 is 3.5%. Never above 10%.
MAX_CHARGE_BASIS_POINTS = 1000

_LAST_FOUR = re.compile(r"[0-9]{4}")
_BRAND = re.compile(r"[A-Za-z][A-Za-z ]{0,23}")
# An https page, or the local client app's own address for the card sandbox in development.
_PAGE = re.compile(
    r"(https://[A-Za-z0-9.-]+|http://(localhost|127\.0\.0\.1)(:[0-9]{1,5})?)"
    r"(/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?"
)
_CONFIRMATION = re.compile(r"[A-Za-z0-9._:-]{1,64}")


class CardRefusal(StrEnum):
    """Why a card payment may not be started. Safe to return to the caller."""

    ABOVE_PAYMENT_LIMIT = "above_card_payment_limit"
    ABOVE_30_DAY_LIMIT = "above_card_30_day_limit"


@dataclass(frozen=True, slots=True)
class CardTerms:
    """What staff set for card payments on Settings."""

    # The card charge the client pays on top, in basis points. 0 means Finvest bears it.
    charge_basis_points: int
    # The most one card payment may be, investment and charge together.
    max_payment: Money
    # The most one client may pay by card in any 30 days, investment and charge together.
    max_30_days: Money


@dataclass(frozen=True, slots=True)
class CardQuote:
    amount: Money
    charge: Money

    @property
    def total(self) -> Money:
        return self.amount + self.charge


def card_charge(amount: Money, basis_points: int) -> Money:
    """The card charge on `amount`, rounded up to a whole shilling. Integers only."""
    if not 0 <= basis_points <= MAX_CHARGE_BASIS_POINTS:
        raise ValueError("A card charge rate is 0 to 10%")
    return Money(-((-amount.minor * basis_points) // 10_000), amount.currency)


def quote(amount: Money, terms: CardTerms) -> CardQuote:
    if amount.currency is not Currency.UGX:
        raise InvalidDeposit("unsupported_currency")
    if not amount.is_positive:
        raise InvalidDeposit("invalid_amount")
    return CardQuote(amount, card_charge(amount, terms.charge_basis_points))


def check_limits(quoted: CardQuote, terms: CardTerms, *, paid_in_30_days: Money) -> None:
    """Refuse a card payment over either limit. `paid_in_30_days` counts every card deposit of
    the client's in the last 30 days that has not failed, at its total."""
    if quoted.total.minor > terms.max_payment.minor:
        raise InvalidDeposit(CardRefusal.ABOVE_PAYMENT_LIMIT.value)
    if paid_in_30_days.minor + quoted.total.minor > terms.max_30_days.minor:
        raise InvalidDeposit(CardRefusal.ABOVE_30_DAY_LIMIT.value)


@dataclass(frozen=True, slots=True)
class CardOrderRequest:
    """Open a card order for one deposit. `request_id` is our deposit ID, sent as the merchant
    reference, so the provider's notifications and statements name it."""

    request_id: str
    total: Money
    description: str
    # The client's phone as `+256` and nine digits: the provider needs a contact for the payer.
    phone: str


@dataclass(frozen=True, slots=True)
class OrderOpened:
    """The provider took the order. The payer pays on `page_url`, and only there."""

    provider_reference: str
    page_url: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider_reference, str) or not re.fullmatch(
            r"[A-Za-z0-9._:-]{1,128}", self.provider_reference
        ):
            raise InvalidOutcome("A provider reference is 1 to 128 safe characters")
        if not isinstance(self.page_url, str) or len(self.page_url) > 1024:
            raise InvalidOutcome("A payment page is an https address")
        if _PAGE.fullmatch(self.page_url) is None:
            raise InvalidOutcome("A payment page is an https address")


CardSendResult = OrderOpened | Rejected | Deferred | Uncertain


@dataclass(frozen=True, slots=True)
class CardDetails:
    """What the provider tells us about the card that paid, kept to show the client and for a
    refund. Never the card number: the brand, the last four digits and the provider's code."""

    brand: str | None
    last_four: str | None
    confirmation_code: str | None

    def __post_init__(self) -> None:
        if self.brand is not None and _BRAND.fullmatch(self.brand) is None:
            raise InvalidOutcome("A card brand is a short word")
        if self.last_four is not None and _LAST_FOUR.fullmatch(self.last_four) is None:
            raise InvalidOutcome("The last four digits of a card")
        if self.confirmation_code is not None and (
            _CONFIRMATION.fullmatch(self.confirmation_code) is None
        ):
            raise InvalidOutcome("A confirmation code is 1 to 64 safe characters")


@dataclass(frozen=True, slots=True)
class CardReading:
    """A status read of a hosted order, the card's details when a card paid, and the number
    that paid when it was mobile money (`+256` and nine digits, None when not given)."""

    reading: StatusReading
    details: CardDetails | None = None
    paid_from: str | None = None


class CancelResult(StrEnum):
    CANCELLED = "cancelled"  # the provider says the order can no longer be paid
    REFUSED = "refused"  # the provider would not cancel: it may be paid or already ended
    UNKNOWN = "unknown"  # no answer we can trust


def after_card_send(result: CardSendResult, *, last_attempt: bool) -> Move:
    """The next state of a `sending` card deposit, from the provider's answer to the order."""
    match result:
        case OrderOpened(provider_reference=reference):
            return Move(DepositStatus.AWAITING_APPROVAL, provider_reference=reference)
        case Rejected(code=code, provider_reference=reference):
            return Move(DepositStatus.FAILED, code=code.value, provider_reference=reference)
        case Deferred():
            if last_attempt:
                return Move(DepositStatus.FAILED, code=Code.PROVIDER_UNAVAILABLE.value)
            return Move(DepositStatus.CREATED)
        case Uncertain():
            # Without the page we never received, nobody can pay this order. Safe to fail.
            return Move(DepositStatus.FAILED, code=Code.PROVIDER_UNAVAILABLE.value)


def card_window_over(opened_at: datetime | None, now: datetime) -> bool:
    return opened_at is not None and now - opened_at >= CARD_WINDOW


def after_card_status(
    current: DepositStatus,
    reading: StatusReading,
    *,
    expected: Money,
    credited: Money,
    payer: str | None = None,
    paid_from: str | None = None,
) -> Move | None:
    """The next state of a card deposit from a status read, or None to leave it as it is.

    `expected` is what the provider must have collected, investment and charge together.
    `credited` is what the client's investment receives, posted only when exactly `expected`
    was collected. A pending order is left as it is here: cancelling an overdue one is the
    worker's job, because it calls the provider.

    `payer` is the client's own number, for a mobile money payment on a hosted page. It must be
    the number that paid: a payment from any other number, or one the provider does not name,
    is held as uncertain for staff, as one for the wrong amount is.
    """
    if current not in (DepositStatus.AWAITING_APPROVAL, DepositStatus.UNCERTAIN):
        return None
    match reading:
        case Confirmed(provider_reference=reference, amount=amount):
            if amount != expected:
                if current is DepositStatus.UNCERTAIN:
                    return None
                return Move(
                    DepositStatus.UNCERTAIN,
                    code=Code.AMOUNT_MISMATCH.value,
                    provider_reference=reference,
                )
            if payer is not None and paid_from != payer:
                if current is DepositStatus.UNCERTAIN:
                    return None
                return Move(
                    DepositStatus.UNCERTAIN,
                    code=Code.PAYER_DIFFERS.value,
                    provider_reference=reference,
                )
            return Move(DepositStatus.CONFIRMED, provider_reference=reference, post=credited)
        case Rejected(code=code, provider_reference=reference):
            return Move(DepositStatus.FAILED, code=code.value, provider_reference=reference)
        case Pending():
            return None
        case NotFound():
            if current is DepositStatus.UNCERTAIN:
                return None
            return Move(DepositStatus.UNCERTAIN, code=Code.UNKNOWN_TO_PROVIDER.value)
        case Unavailable():
            return None
