"""Sending a payout to the provider and reading back how it ended. Pure: no I/O.

These functions turn what the provider said into the payout event that follows, and decide
when the status is read again. The worker in `payouts/jobs.py` does the calling and saving.

A payout is sent at most once. Before the provider is called, a send attempt is saved and
committed. The database allows only one attempt per payout that did not end as `deferred`, so
a second send can only follow a provider's clear answer that it took nothing. An attempt that
never got an answer saved is `interrupted`, and the payout becomes uncertain: it is read, never
sent again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.modules.payments.domain.outcomes import (
    Accepted,
    Confirmed,
    Deferred,
    NotFound,
    Pending,
    Rejected,
    SendResult,
    StatusReading,
    Unavailable,
    Uncertain,
)
from app.modules.payouts.domain.payouts import PayoutEvent
from app.shared.money import Money

# How many times one send job asks again after the provider said it took nothing.
SEND_ATTEMPTS = 5
# A send attempt with no answer saved after this long was interrupted. It may have gone.
SEND_GRACE = timedelta(minutes=2)
# A queued payout left alone this long has lost its send job, and the sweep queues another.
RESEND_AFTER = timedelta(minutes=10)
# A queued mobile money payout first tried this long ago, that the provider has taken nothing
# of since, is held up: the sweep keeps sending it, and staff are shown it so a person can find
# out why.
HELD_UP_AFTER = timedelta(hours=2)
# Status reads go on for this long after the send. After that, two staff members settle the
# payout from the statement.
READ_WINDOW = timedelta(days=3)
_READ_DELAYS = (
    timedelta(seconds=10),
    timedelta(seconds=30),
    timedelta(minutes=1),
    timedelta(minutes=2),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(minutes=30),
)
_LATER_READS = timedelta(hours=1)


class AttemptOutcome(StrEnum):
    """How one send attempt ended, as saved with the attempt."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"  # the provider took nothing; the only outcome that allows another send
    UNCERTAIN = "uncertain"
    INTERRUPTED = "interrupted"  # no answer was saved, so it may have gone


# A provider that confirms the money arrived, but for another amount, is not believed by a
# machine. The payout stays as it is for two staff members, and is not read again.
AMOUNT_MISMATCH = "amount_mismatch"


@dataclass(frozen=True, slots=True)
class SendStep:
    event: PayoutEvent
    outcome: AttemptOutcome
    provider_reference: str | None = None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class ReadStep:
    """What a status read does. `event` is None when the payout is left as it is."""

    event: PayoutEvent | None
    read_again: bool
    provider_reference: str | None = None
    failure_code: str | None = None


def after_send(result: SendResult) -> SendStep:
    """The payout event that follows the provider's answer to a send."""
    match result:
        case Accepted(provider_reference=reference):
            return SendStep(PayoutEvent.SEND_ACCEPTED, AttemptOutcome.ACCEPTED, reference)
        case Rejected(code=code, provider_reference=reference):
            return SendStep(
                PayoutEvent.SEND_REJECTED, AttemptOutcome.REJECTED, reference, code.value
            )
        case Deferred():
            return SendStep(PayoutEvent.SEND_DEFERRED, AttemptOutcome.DEFERRED)
        case Uncertain(provider_reference=reference):
            return SendStep(PayoutEvent.SEND_UNCERTAIN, AttemptOutcome.UNCERTAIN, reference)


def after_status(reading: StatusReading, expected: Money) -> ReadStep:
    """The payout event that follows a status read of a sent or uncertain payout."""
    match reading:
        case Pending(provider_reference=reference):
            return ReadStep(PayoutEvent.STATUS_PENDING, True, reference)
        case Confirmed(provider_reference=reference, amount=amount):
            if amount != expected:
                return ReadStep(None, False, reference, AMOUNT_MISMATCH)
            return ReadStep(PayoutEvent.STATUS_PAID, False, reference)
        case Rejected(code=code, provider_reference=reference):
            return ReadStep(PayoutEvent.STATUS_FAILED, False, reference, code.value)
        case NotFound() | Unavailable():
            # No record yet, or the read failed. Neither says the money did not go.
            return ReadStep(PayoutEvent.STATUS_UNKNOWN, True)


def next_read(number: int, sent_at: datetime, now: datetime) -> datetime | None:
    """When read `number` (from 0) is due, or None once the read window has passed."""
    if number < 0:
        raise ValueError("Reads are numbered from 0")
    delay = _READ_DELAYS[number] if number < len(_READ_DELAYS) else _LATER_READS
    due = now + delay
    return due if due - sent_at <= READ_WINDOW else None


_LABELS = {"mtn": "MTN Mobile Money", "airtel": "Airtel Money"}


def destination_label(network: str) -> str:
    """How a client's messages name where the money went. Never the number itself."""
    return _LABELS.get(network, "your mobile money number")
