"""The life of one payout, and what each step does to the money. Pure: no I/O.

The money for a payout leaves the client's holding when the payout is created and sits in a
"payout in flight" ledger account for that client until the provider's answer is known. Every
change of status names the one ledger movement that goes with it, so the service posts exactly
that entry in the same transaction and the payout and the books cannot drift apart.

The rule that matters most is here as a table: once we do not know whether the provider took a
payout, it is never sent again. Only reading the status back from the provider can move it,
or, when the provider has no answer at all, two staff members working from the statement.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PayoutStatus(StrEnum):
    # Waiting for the client to be screened against sanctions and politically exposed person
    # lists. Its money is reserved and nothing has been sent.
    SCREENING = "screening"
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
    # The client was screened clear: on to the queue, or to staff review for a large payout.
    SCREENING_CLEARED = "screening_cleared"
    SCREENING_CLEARED_FOR_REVIEW = "screening_cleared_for_review"
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
    # Two staff members settled a payout the provider cannot answer for, from the statement.
    STAFF_RESOLVED_PAID = "staff_resolved_paid"
    STAFF_RESOLVED_FAILED = "staff_resolved_failed"
    # A bank payout, paid by staff from the trust account. A second person approves the batch
    # it is in, and later a settlement from the trust statement.
    BANK_BATCH_APPROVED = "bank_batch_approved"
    BANK_SETTLED_PAID = "bank_settled_paid"
    BANK_SETTLED_FAILED = "bank_settled_failed"


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
    (_S.SCREENING, _E.SCREENING_CLEARED): Transition(_S.QUEUED),
    (_S.SCREENING, _E.SCREENING_CLEARED_FOR_REVIEW): Transition(_S.HELD),
    # Staff may decline a payout waiting for screening, as they may a held one.
    (_S.SCREENING, _E.REVIEW_DECLINED): _CANCELLED,
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
    (_S.SENT, _E.STAFF_RESOLVED_PAID): _PAID,
    (_S.SENT, _E.STAFF_RESOLVED_FAILED): _FAILED,
    (_S.UNCERTAIN, _E.STAFF_RESOLVED_PAID): _PAID,
    (_S.UNCERTAIN, _E.STAFF_RESOLVED_FAILED): _FAILED,
    (_S.QUEUED, _E.BANK_BATCH_APPROVED): Transition(_S.SENT),
    (_S.SENT, _E.BANK_SETTLED_PAID): _PAID,
    (_S.SENT, _E.BANK_SETTLED_FAILED): _FAILED,
}


def initial_status(*, needs_review: bool, screened: bool = True) -> PayoutStatus:
    """Waiting for screening first, then staff review for a large payout, then the queue."""
    if not screened:
        return PayoutStatus.SCREENING
    return PayoutStatus.HELD if needs_review else PayoutStatus.QUEUED


def after_screening(*, needs_review: bool) -> PayoutEvent:
    return (
        PayoutEvent.SCREENING_CLEARED_FOR_REVIEW if needs_review else PayoutEvent.SCREENING_CLEARED
    )


def apply(status: PayoutStatus, event: PayoutEvent) -> Transition:
    """The status and ledger movement that follow, or `IllegalTransition`."""
    try:
        return _TABLE[(status, event)]
    except KeyError:
        raise IllegalTransition(status, event) from None


# A person's word replaces the provider's, so it takes two people and is always audited.
NEEDS_TWO_PEOPLE = frozenset({PayoutEvent.STAFF_RESOLVED_PAID, PayoutEvent.STAFF_RESOLVED_FAILED})
# Paying a bank payout from the trust account. Each step is a second person's approval.
BANK_EVENTS = frozenset(
    {
        PayoutEvent.BANK_BATCH_APPROVED,
        PayoutEvent.BANK_SETTLED_PAID,
        PayoutEvent.BANK_SETTLED_FAILED,
    }
)


def staff_may_resolve(status: PayoutStatus, *, provider_has_answer: bool) -> bool:
    """True when two staff members may settle the payout by hand.

    Only for a payout that may have left, and only when a fresh status read gave no answer.
    While the provider can say what happened, its answer is the one that counts.
    """
    return needs_status_read(status) and not provider_has_answer


def may_send(status: PayoutStatus) -> bool:
    """True only while nothing has reached the provider. Never true for an uncertain payout."""
    return status is PayoutStatus.QUEUED


def needs_status_read(status: PayoutStatus) -> bool:
    """True while the answer can only come from asking the provider."""
    return status in (PayoutStatus.SENT, PayoutStatus.UNCERTAIN)
