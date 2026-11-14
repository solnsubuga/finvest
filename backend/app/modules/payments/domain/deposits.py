"""A deposit: one payment in, for one investment or goal. Pure: no I/O.

There is no wallet, so a deposit always names what it pays for. A mobile money deposit moves
through these states:

    created ──► sending ──► awaiting_approval ──► confirmed
       │           │  │             │   │
       │           │  └─► uncertain ◄───┘ └─► failed
       └─► failed ◄┘          │
                              └─► awaiting_approval, confirmed or failed

* `created`: saved with a job to send it. Nothing has reached the provider.
* `sending`: a worker is about to call the provider. Saved before the call, so a worker that
  dies mid-call leaves a mark. A deposit found with that mark is never sent: it becomes
  `uncertain`.
* `awaiting_approval`: the provider took the request and the payer has a prompt on their phone.
* `confirmed`: the provider's own record says the money arrived, and the ledger entry is
  posted in the same transaction. Final.
* `failed`: no money moved. Final.
* `uncertain`: we cannot tell. Only a status read from the provider, or reconciliation, moves
  it on. It is never sent again.

A bank transfer has a shorter path of its own, `in_review` to `confirmed` or `failed`, described
in `bank_transfers.py`. A mobile money deposit is never `in_review`, and a bank transfer is never
in any of the mobile money states. The database holds both to that.

The functions here decide the next state from what a provider said. They never look at a
callback: a callback only causes a status read.
"""

from __future__ import annotations

import re
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
from app.shared.money import Currency, Money

# How long a payer has to approve before we stop waiting and hand the deposit to reconciliation.
# The provider normally ends the prompt itself well before this.
APPROVAL_WINDOW = timedelta(minutes=15)
# A deposit still `sending` after this long was interrupted between the call and saving its answer.
SENDING_GRACE = timedelta(minutes=2)
# A deposit still `created` after this long was never sent, and the payer has stopped waiting.
UNSENT_EXPIRY = timedelta(minutes=10)

# How long status reads keep trying to settle an uncertain deposit before it is left to
# reconciliation, and how far apart the reads are as it ages.
UNCERTAIN_READ_WINDOW = timedelta(hours=24)
_UNCERTAIN_READ_STEPS = (
    (timedelta(minutes=15), timedelta(minutes=1)),
    (timedelta(hours=2), timedelta(minutes=10)),
    (UNCERTAIN_READ_WINDOW, timedelta(hours=1)),
)

_MSISDN = re.compile(r"\+256[0-9]{9}")
_LOCAL = re.compile(r"0[0-9]{9}")
_NO_PLUS = re.compile(r"256[0-9]{9}")


class DepositStatus(StrEnum):
    CREATED = "created"
    SENDING = "sending"
    AWAITING_APPROVAL = "awaiting_approval"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    IN_REVIEW = "in_review"  # a bank transfer waiting for staff to find it on the statement

    @property
    def is_final(self) -> bool:
        return self in (DepositStatus.CONFIRMED, DepositStatus.FAILED)


class Network(StrEnum):
    MTN = "mtn"
    AIRTEL = "airtel"


class Method(StrEnum):
    """How a deposit is paid. Stored as the deposit's `method`."""

    MTN = "mtn"
    AIRTEL = "airtel"
    BANK_TRANSFER = "bank_transfer"

    @property
    def network(self) -> Network | None:
        return None if self is Method.BANK_TRANSFER else Network(self.value)


class Purpose(StrEnum):
    """What a payment in is for. Never "a balance"."""

    INVESTMENT = "investment"
    GOAL = "goal"


class Code(StrEnum):
    """Why a deposit failed or is uncertain, beyond the provider's own rejection codes."""

    PROVIDER_UNAVAILABLE = "provider_unavailable"  # never sent; no money moved
    SEND_UNCERTAIN = "send_uncertain"  # the request may or may not have reached the provider
    UNKNOWN_TO_PROVIDER = "unknown_to_provider"  # the provider has no record of it
    APPROVAL_OVERDUE = "approval_overdue"  # still pending after the approval window
    AMOUNT_MISMATCH = "amount_mismatch"  # the provider confirmed a different amount


# Every move a deposit may make. The service refuses anything else.
TRANSITIONS: dict[DepositStatus, frozenset[DepositStatus]] = {
    DepositStatus.CREATED: frozenset({DepositStatus.SENDING, DepositStatus.FAILED}),
    DepositStatus.SENDING: frozenset(
        {
            DepositStatus.CREATED,
            DepositStatus.AWAITING_APPROVAL,
            DepositStatus.FAILED,
            DepositStatus.UNCERTAIN,
        }
    ),
    DepositStatus.AWAITING_APPROVAL: frozenset(
        {DepositStatus.CONFIRMED, DepositStatus.FAILED, DepositStatus.UNCERTAIN}
    ),
    DepositStatus.UNCERTAIN: frozenset(
        {DepositStatus.AWAITING_APPROVAL, DepositStatus.CONFIRMED, DepositStatus.FAILED}
    ),
    DepositStatus.IN_REVIEW: frozenset({DepositStatus.CONFIRMED, DepositStatus.FAILED}),
    DepositStatus.CONFIRMED: frozenset(),
    DepositStatus.FAILED: frozenset(),
}


class InvalidDeposit(ValueError):
    """A deposit request that breaks a rule. `code` is safe to return to the caller."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Move:
    """The next state of a deposit, decided from what the provider said.

    `post` is set only with `confirmed`: the ledger entry must be posted in the same
    transaction. `code` says why for `failed` and `uncertain`. `provider_reference` is set when
    the provider gave one we did not have. `charge` is what the provider took from Finvest's
    wallet for a confirmed deposit, posted as Finvest's cost beside the deposit's entry.
    """

    status: DepositStatus
    code: str | None = None
    provider_reference: str | None = None
    post: Money | None = None
    charge: Money | None = None

    def __post_init__(self) -> None:
        if (self.post is not None) != (self.status is DepositStatus.CONFIRMED):
            raise ValueError("A deposit is confirmed exactly when its entry is posted")
        if self.charge is not None and (self.post is None or not self.charge.is_positive):
            raise ValueError("Only a confirmed deposit carries a charge, and it is positive")
        if (self.code is not None) != (
            self.status in (DepositStatus.FAILED, DepositStatus.UNCERTAIN)
        ):
            raise ValueError("A failed or uncertain deposit says why, and no other does")


def normalise_msisdn(value: str) -> str:
    """A Ugandan mobile number as `+256` and nine digits. Refuses anything else.

    Accepts `0772123456`, `256772123456` and `+256772123456`, with spaces. Which network a
    number belongs to is not guessed from its prefix: number portability makes that wrong, and
    the provider refuses a number that is not registered with the chosen network.
    """
    if not isinstance(value, str):
        raise InvalidDeposit("invalid_phone")
    digits = value.replace(" ", "")
    if _LOCAL.fullmatch(digits):
        digits = "+256" + digits[1:]
    elif _NO_PLUS.fullmatch(digits):
        digits = "+" + digits
    if _MSISDN.fullmatch(digits) is None:
        raise InvalidDeposit("invalid_phone")
    return digits


def mask_msisdn(msisdn: str) -> str:
    """The number as a client may see it again: `+256 77• ••• •56`. Never logged in full."""
    return f"{msisdn[:4]} {msisdn[4:6]}• ••• •{msisdn[-2:]}"


def check_mobile_money_amount(amount: Money) -> None:
    """Mobile money collects whole shillings only."""
    if not isinstance(amount, Money) or amount.currency is not Currency.UGX:
        raise InvalidDeposit("unsupported_currency")
    if not amount.is_positive:
        raise InvalidDeposit("invalid_amount")


def can_move(current: DepositStatus, to: DepositStatus) -> bool:
    return to in TRANSITIONS[current]


def after_send(result: SendResult, *, last_attempt: bool) -> Move:
    """The next state of a `sending` deposit, from the provider's answer to the request.

    A deferred request goes back to `created` to be sent later, unless this was the last
    attempt, in which case it fails: nothing reached the provider, so no money moved.
    """
    match result:
        case Accepted(provider_reference=reference):
            return Move(DepositStatus.AWAITING_APPROVAL, provider_reference=reference)
        case Rejected(code=code, provider_reference=reference):
            return Move(DepositStatus.FAILED, code=code.value, provider_reference=reference)
        case Deferred():
            if last_attempt:
                return Move(DepositStatus.FAILED, code=Code.PROVIDER_UNAVAILABLE.value)
            return Move(DepositStatus.CREATED)
        case Uncertain(provider_reference=reference):
            return Move(
                DepositStatus.UNCERTAIN,
                code=Code.SEND_UNCERTAIN.value,
                provider_reference=reference,
            )


def after_status(
    current: DepositStatus,
    reading: StatusReading,
    *,
    expected: Money,
    sent_at: datetime | None,
    now: datetime,
) -> Move | None:
    """The next state of a deposit from a status read, or None to leave it as it is.

    Only a deposit that is `awaiting_approval` or `uncertain` can be moved by a status read.
    The ledger is posted only when the provider confirms exactly the amount we asked for. Any other
    confirmed amount is left for staff, with nothing posted.
    """
    if current not in (DepositStatus.AWAITING_APPROVAL, DepositStatus.UNCERTAIN):
        return None
    overdue = sent_at is not None and now - sent_at >= APPROVAL_WINDOW
    match reading:
        case Confirmed(provider_reference=reference, amount=amount, charge=charge):
            if amount.currency is not expected.currency or amount != expected:
                if current is DepositStatus.UNCERTAIN:
                    return None
                return Move(
                    DepositStatus.UNCERTAIN,
                    code=Code.AMOUNT_MISMATCH.value,
                    provider_reference=reference,
                )
            return Move(
                DepositStatus.CONFIRMED,
                provider_reference=reference,
                post=expected,
                charge=charge if charge is not None and charge.is_positive else None,
            )
        case Rejected(code=code, provider_reference=reference):
            return Move(DepositStatus.FAILED, code=code.value, provider_reference=reference)
        case Pending(provider_reference=reference):
            if current is DepositStatus.UNCERTAIN:
                if overdue:
                    return None
                # The provider has it after all: the payer is being prompted.
                return Move(DepositStatus.AWAITING_APPROVAL, provider_reference=reference)
            if overdue:
                return Move(
                    DepositStatus.UNCERTAIN,
                    code=Code.APPROVAL_OVERDUE.value,
                    provider_reference=reference,
                )
            return None
        case NotFound():
            if current is DepositStatus.UNCERTAIN:
                return None
            return Move(DepositStatus.UNCERTAIN, code=Code.UNKNOWN_TO_PROVIDER.value)
        case Unavailable():
            return None


def next_uncertain_read(since: datetime, now: datetime) -> tuple[int, datetime] | None:
    """The next status read of a deposit uncertain since `since`: its number and when it is due.

    Reads are frequent at first, when a late answer is most likely, and thin out: every minute
    for fifteen minutes, every ten minutes for two hours, then hourly. Each read has a number,
    so it can be queued once however often it is asked for. After `UNCERTAIN_READ_WINDOW` the
    answer is None: status reads stop and reconciliation settles the deposit from the
    provider's statement.
    """
    number, due = 0, since
    for limit, step in _UNCERTAIN_READ_STEPS:
        while due + step <= since + limit:
            number, due = number + 1, due + step
            if due > now:
                return number, due
    return None
