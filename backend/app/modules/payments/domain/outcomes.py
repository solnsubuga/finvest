"""What a payment provider told us, sorted by what is safe to do next. Pure: no I/O.

Used for money in and money out. Nothing here is specific to a deposit or a payout.

Sending a request ends in one of four ways:

* `Accepted`: the provider took it and will give the final result later.
* `Rejected`: the provider refused it. No money moved, and it is final.
* `Deferred`: the request certainly did not reach the provider's books (it was never sent, or
  the provider answered that it took nothing). Sending again later is safe.
* `Uncertain`: we cannot tell whether the provider took it. It is never sent again. The status
  is read back from the provider, and reconciliation settles what a status read cannot.

Reading a status back ends in one of five ways: `Pending`, `Confirmed`, `Rejected`, `NotFound`
(the provider has no record) or `Unavailable` (the read itself failed, which says nothing about
the money and is safe to repeat).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.shared.money import Money

# A provider's own identifier for a request. Opaque, and never a phone number.
_REFERENCE = re.compile(r"[A-Za-z0-9._:-]{1,128}")


class RejectionCode(StrEnum):
    """Why a provider refused. Safe to log, store and show: a code, never provider text."""

    DECLINED = "declined"  # the payer said no, or entered a wrong PIN
    INSUFFICIENT_FUNDS = "insufficient_funds"
    APPROVAL_TIMED_OUT = "approval_timed_out"  # the payer did not answer the prompt in time
    ACCOUNT_NOT_FOUND = "account_not_found"  # the number is not registered for mobile money
    LIMIT_EXCEEDED = "limit_exceeded"  # over the payer's or the network's limit
    OTHER = "other"


class UncertainReason(StrEnum):
    TIMED_OUT = "timed_out"  # no answer after the request was sent
    CONNECTION_LOST = "connection_lost"  # the connection dropped after the request was sent
    PROVIDER_ERROR = "provider_error"  # a 5xx or an answer we could not read
    INTERRUPTED = "interrupted"  # our own process stopped between sending and saving the answer


class InvalidOutcome(ValueError):
    """An outcome built with a value a provider adapter must never pass on."""


def _checked(reference: str | None, *, required: bool) -> None:
    if reference is None:
        if required:
            raise InvalidOutcome("This outcome needs the provider's reference")
        return
    if not isinstance(reference, str) or _REFERENCE.fullmatch(reference) is None:
        raise InvalidOutcome("A provider reference is 1 to 128 safe characters")


@dataclass(frozen=True, slots=True)
class Accepted:
    provider_reference: str

    def __post_init__(self) -> None:
        _checked(self.provider_reference, required=True)


@dataclass(frozen=True, slots=True)
class Rejected:
    code: RejectionCode
    provider_reference: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, RejectionCode):
            raise InvalidOutcome("Unknown rejection code")
        _checked(self.provider_reference, required=False)


@dataclass(frozen=True, slots=True)
class Deferred:
    """Nothing reached the provider's books. `retry_after_seconds` is the provider's own hint."""

    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.retry_after_seconds is not None and (
            type(self.retry_after_seconds) is not int or self.retry_after_seconds < 0
        ):
            raise InvalidOutcome("A retry hint is a whole number of seconds")


@dataclass(frozen=True, slots=True)
class Uncertain:
    reason: UncertainReason
    provider_reference: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reason, UncertainReason):
            raise InvalidOutcome("Unknown reason")
        _checked(self.provider_reference, required=False)


@dataclass(frozen=True, slots=True)
class Pending:
    provider_reference: str

    def __post_init__(self) -> None:
        _checked(self.provider_reference, required=True)


@dataclass(frozen=True, slots=True)
class Confirmed:
    """The provider says the money moved. `amount` is what the provider says moved."""

    provider_reference: str
    amount: Money

    def __post_init__(self) -> None:
        _checked(self.provider_reference, required=True)
        if not isinstance(self.amount, Money) or not self.amount.is_positive:
            raise InvalidOutcome("A confirmed amount is positive Money")


@dataclass(frozen=True, slots=True)
class NotFound:
    """The provider has no record of the request."""


@dataclass(frozen=True, slots=True)
class Unavailable:
    """The status could not be read. Says nothing about the money. Reading again is safe."""


SendResult = Accepted | Rejected | Deferred | Uncertain
StatusReading = Pending | Confirmed | Rejected | NotFound | Unavailable
