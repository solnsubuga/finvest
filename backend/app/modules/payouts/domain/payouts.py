"""The life of one payout, and what each step does to the money. Pure: no I/O.

The money for a payout leaves the client's holding when the payout is created and sits in a
"payout in flight" ledger account for that client until the provider's answer is known. Every
change of status names the one ledger movement that goes with it, so the service posts exactly
that entry in the same transaction and the payout and the books cannot drift apart.

The rule that matters most is here as a table: once we do not know whether the provider took a
payout, it is never sent again. Only reading the status back from the provider can move it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PayoutStatus(StrEnum):
    HELD = "held"  # waiting for a staff member to approve a large withdrawal
    QUEUED = "queued"  # ready for the worker to send; nothing has reached the provider
    SENT = "sent"  # the provider accepted it and has not yet said how it ended
    UNCERTAIN = "uncertain"  # we do not know whether the provider took it
    PAID = "paid"  # the provider confirmed the money arrived
    FAILED = "failed"  # the provider confirmed it did not go; the money is parked for staff
    CANCELLED = "cancelled"  # stopped before anything was sent; the money went back


FINAL = frozenset({PayoutStatus.PAID, PayoutStatus.FAILED, PayoutStatus.CANCELLED})
# Counted against the client's daily limit: everything that did or still may pay out.
COUNTS_TOWARDS_LIMIT = frozenset(PayoutStatus) - {PayoutStatus.FAILED, PayoutStatus.CANCELLED}


class PayoutEvent(StrEnum):
    # Staff review of a held payout.
    REVIEW_APPROVED = "review_approved"
    REVIEW_DECLINED = "review_declined"
    # The client changed their mind while the payout was still held.
    CLIENT_CANCELLED = "client_cancelled"
    # What came back from asking the provider to send the money.
    SEND_ACCEPTED = "send_accepted"
    SEND_CONFIRMED = "send_confirmed"
    SEND_REJECTED = "send_rejected"
    SEND_DEFERRED = "send_deferred"  # certainly not sent; trying again later is safe
    SEND_UNCERTAIN = "send_uncertain"  # timed out or broke after the request left
    # What came back from reading the payout's status from the provider.
    STATUS_PAID = "status_paid"
    STATUS_FAILED = "status_failed"
    STATUS_PENDING = "status_pending"  # the provider has it and is still working
    STATUS_UNKNOWN = "status_unknown"  # the read failed, or the provider has no record yet


class LedgerEffect(StrEnum):
    """The one journal entry that goes with a change of status."""

    NONE = "none"
    # In flight -> the wallet or bank the money left from. The client has been paid.
    SETTLE = "settle"
    # In flight -> the client's "payout pending" account. Staff are alerted.
    PARK = "park"
    # In flight -> back where it came from. Nothing was sent.
    RELEASE = "release"


@dataclass(frozen=True, slots=True)
class Transition:
    status: PayoutStatus
    effect: LedgerEffect = LedgerEffect.NONE


class IllegalTransition(Exception):
    """The event cannot happen to a payout in this status."""

    code = "payout_illegal_transition"

    def __init__(self, status: PayoutStatus, event: PayoutEvent) -> None:
        super().__init__(f"{event.value} cannot follow {status.value}")
        self.status = status
        self.event = event


_S, _E, _L = PayoutStatus, PayoutEvent, LedgerEffect
_PAID = Transition(_S.PAID, _L.SETTLE)
_FAILED = Transition(_S.FAILED, _L.PARK)
_CANCELLED = Transition(_S.CANCELLED, _L.RELEASE)

_TABLE: dict[tuple[PayoutStatus, PayoutEvent], Transition] = {
    (_S.HELD, _E.REVIEW_APPROVED): Transition(_S.QUEUED),
    (_S.HELD, _E.REVIEW_DECLINED): _CANCELLED,
    (_S.HELD, _E.CLIENT_CANCELLED): _CANCELLED,
    (_S.QUEUED, _E.SEND_ACCEPTED): Transition(_S.SENT),
    (_S.QUEUED, _E.SEND_CONFIRMED): _PAID,
    (_S.QUEUED, _E.SEND_REJECTED): _FAILED,
    (_S.QUEUED, _E.SEND_DEFERRED): Transition(_S.QUEUED),
    (_S.QUEUED, _E.SEND_UNCERTAIN): Transition(_S.UNCERTAIN),
    (_S.SENT, _E.STATUS_PAID): _PAID,
    (_S.SENT, _E.STATUS_FAILED): _FAILED,
    (_S.SENT, _E.STATUS_PENDING): Transition(_S.SENT),
    (_S.SENT, _E.STATUS_UNKNOWN): Transition(_S.SENT),
    (_S.UNCERTAIN, _E.STATUS_PAID): _PAID,
    (_S.UNCERTAIN, _E.STATUS_FAILED): _FAILED,
    # The provider has it after all, so it is no longer uncertain, only unfinished.
    (_S.UNCERTAIN, _E.STATUS_PENDING): Transition(_S.SENT),
    (_S.UNCERTAIN, _E.STATUS_UNKNOWN): Transition(_S.UNCERTAIN),
}


def initial_status(*, needs_review: bool) -> PayoutStatus:
    return PayoutStatus.HELD if needs_review else PayoutStatus.QUEUED


def apply(status: PayoutStatus, event: PayoutEvent) -> Transition:
    """The status and ledger movement that follow, or `IllegalTransition`."""
    try:
        return _TABLE[(status, event)]
    except KeyError:
        raise IllegalTransition(status, event) from None


def may_send(status: PayoutStatus) -> bool:
    """True only while nothing has reached the provider. Never true for an uncertain payout."""
    return status is PayoutStatus.QUEUED


def needs_status_read(status: PayoutStatus) -> bool:
    """True while the answer can only come from asking the provider."""
    return status in (PayoutStatus.SENT, PayoutStatus.UNCERTAIN)
