"""The rules for paying a deposit back to the card that paid it. Pure: no database, no clock.

A client paid for an investment by card and should have the money back before the investment
starts: they asked to cancel, or the investment cannot go ahead. The investment's amount is
paid back through the provider that collected it, to the same card. The card charge the client
paid on top goes back too only when the refund is Finvest's own mistake: the proposer says so,
and the second person sees it. Otherwise it stays Finvest's income (the owner's decision,
2026-11-15).

    proposed ──► approved ──► sending ──► requested ──► done
       │            │            │  │          │
       │            └─► failed ◄─┘  └─► uncertain ──► requested, done or failed
       └─► sent_back or withdrawn                 requested ──► failed

One member of staff proposes and a different one approves. The worker checks again, holds the
money and asks the provider once. The provider's yes only means it took the request: its own
people still pay it out, so a refund is `requested` until a status read shows the payment
reversed or staff see the refund on the provider's statement. A request whose answer was lost
is `uncertain` and is never sent again, because a provider takes one refund per payment: staff
look it up at the provider and say what happened. See docs/payments.md.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.modules.payments.domain.deposits import DepositStatus, Method, Purpose
from app.shared.money import Money

CONFIRMED_ENTRY_TYPE = "payment.deposit_confirmed"
# Providers that take a refund back to the payer. Card payments only, for now.
REFUNDING_PROVIDERS = frozenset({"pesapal", "card_sandbox"})


class RefundStatus(StrEnum):
    PROPOSED = "proposed"
    # Approved by a second person. The worker sends it.
    APPROVED = "approved"
    SENT_BACK = "sent_back"
    WITHDRAWN = "withdrawn"
    # The money is held and the request is with the provider, or about to be.
    SENDING = "sending"
    # The provider took the request and will pay it out.
    REQUESTED = "requested"
    # The answer to the request was lost. Staff look it up at the provider.
    UNCERTAIN = "uncertain"
    DONE = "done"
    # Nothing was paid back, and the money waits for the investment again.
    FAILED = "failed"

    @property
    def is_open(self) -> bool:
        return self is RefundStatus.PROPOSED

    @property
    def is_final(self) -> bool:
        return self in (
            RefundStatus.SENT_BACK,
            RefundStatus.WITHDRAWN,
            RefundStatus.DONE,
            RefundStatus.FAILED,
        )

    @property
    def holds_money(self) -> bool:
        """The client's money has left the investment and is on its way back to the card."""
        return self in (RefundStatus.SENDING, RefundStatus.REQUESTED, RefundStatus.UNCERTAIN)


class Refusal(StrEnum):
    """Why a deposit cannot be paid back to the payer now."""

    NOT_CONFIRMED = "not_confirmed"
    NOT_FOR_AN_INVESTMENT = "not_for_an_investment"
    # Only a card payment, through a provider that takes refunds, goes back this way.
    NOT_REFUNDABLE = "not_refundable"
    # Reversed as credited by mistake, or paid back already.
    ALREADY_TAKEN_BACK = "already_taken_back"
    NOT_A_PAYMENT = "not_a_payment"
    # The provider gave no confirmation code, which a refund is asked for by.
    NO_CONFIRMATION_CODE = "no_confirmation_code"
    # The whole amount is no longer waiting for the investment: it started (unwind it first),
    # or the money was moved.
    MONEY_MOVED_ON = "money_moved_on"


class Settlement(StrEnum):
    """What staff found at the provider for a refund the worker could not finish."""

    # The provider has the request and will pay it, or has paid it.
    REQUESTED = "requested"
    PAID = "paid"
    # The provider has no such refund, or refused it.
    NOT_PAID = "not_paid"


class RequestResult(StrEnum):
    """The provider's answer to a refund request."""

    ACCEPTED = "accepted"
    REFUSED = "refused"
    # Nothing reached the provider: it could not be reached, or refused us before reading.
    NOT_SENT = "not_sent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Facts:
    status: DepositStatus
    purpose: Purpose
    method: Method
    provider: str
    reversed: bool
    refunded: bool
    # The entry type of the deposit's ledger entry, None if it has none.
    entry_type: str | None
    confirmation_code: str | None
    amount: Money
    # What waits in the client's awaiting-start account for the investment now.
    waiting: Money


def refusal(facts: Facts) -> Refusal | None:
    """The first reason the deposit cannot be paid back to the card, or None if it can."""
    if facts.status is not DepositStatus.CONFIRMED:
        return Refusal.NOT_CONFIRMED
    if facts.purpose is not Purpose.INVESTMENT:
        return Refusal.NOT_FOR_AN_INVESTMENT
    if facts.method is not Method.CARD or facts.provider not in REFUNDING_PROVIDERS:
        return Refusal.NOT_REFUNDABLE
    if facts.reversed or facts.refunded:
        return Refusal.ALREADY_TAKEN_BACK
    if facts.entry_type != CONFIRMED_ENTRY_TYPE:
        return Refusal.NOT_A_PAYMENT
    if not facts.confirmation_code:
        return Refusal.NO_CONFIRMATION_CODE
    if facts.waiting.currency != facts.amount.currency or facts.waiting < facts.amount:
        return Refusal.MONEY_MOVED_ON
    return None


def refund_total(amount: Money, charge: Money, *, finvest_erred: bool) -> Money:
    """What goes back to the card: the investment's amount, and the card charge only when the
    refund is Finvest's mistake. Never more than the card paid."""
    if charge.currency != amount.currency or charge.minor < 0:
        raise ValueError("A card charge is a non-negative amount in the payment's currency")
    return amount + charge if finvest_erred else amount


def after_request(result: RequestResult) -> RefundStatus:
    """The next state of a `sending` refund, from the provider's answer to the request."""
    match result:
        case RequestResult.ACCEPTED:
            return RefundStatus.REQUESTED
        case RequestResult.REFUSED | RequestResult.NOT_SENT:
            return RefundStatus.FAILED
        case RequestResult.UNKNOWN:
            return RefundStatus.UNCERTAIN


def failure_code(result: RequestResult) -> str | None:
    """Why a refund failed at the request, None if it did not."""
    match result:
        case RequestResult.REFUSED:
            return "provider_refused"
        case RequestResult.NOT_SENT:
            return "provider_unavailable"
        case _:
            return None


@dataclass(frozen=True, slots=True)
class RefundRequest:
    """Ask the provider to pay a payment back to the card. `refund_id` is ours, for the logs."""

    refund_id: str
    confirmation_code: str
    amount: Money
    remarks: str


def after_settlement(current: RefundStatus, found: Settlement) -> RefundStatus | None:
    """The next state of a refund from what staff found at the provider, or None if that is
    not a move this refund can make."""
    if current not in (RefundStatus.REQUESTED, RefundStatus.UNCERTAIN):
        return None
    match found:
        case Settlement.PAID:
            return RefundStatus.DONE
        case Settlement.NOT_PAID:
            return RefundStatus.FAILED
        case Settlement.REQUESTED:
            return RefundStatus.REQUESTED if current is RefundStatus.UNCERTAIN else None
