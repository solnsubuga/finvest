"""A stand-in for ioTec Pay disbursements, for development and tests. It moves no money.

ioTec's API details are not confirmed yet, so this is the only adapter. It keeps no state, so
the worker can restart and lose nothing: what a status read needs is carried in the reference
it hands out.

The last two digits of the number paid choose what happens:

| Ends in | Result |
| --- | --- |
| 01 | Refused when sent: the number has no mobile money account |
| 02 | Accepted, then fails |
| 04 | No answer to the request, and the provider has no record of it (uncertain) |
| 05 | The provider is busy and took nothing (deferred) |
| 06 | Confirmed, but for a different amount |
| anything else | Paid after a few seconds |
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.modules.payments.domain.outcomes import (
    Accepted,
    Confirmed,
    Deferred,
    NotFound,
    Pending,
    Rejected,
    RejectionCode,
    SendResult,
    StatusReading,
    Uncertain,
    UncertainReason,
)
from app.modules.payouts.domain.provider import DisbursementRequest
from app.shared.clock import Clock
from app.shared.money import Currency, Money

SEND_TIME = timedelta(seconds=5)
_SCENARIOS = {"02": "fails", "06": "mismatch"}


class SandboxDisbursements:
    name = "sandbox"

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def send_disbursement(self, request: DisbursementRequest) -> SendResult:
        ending = request.msisdn[-2:]
        if ending == "01":
            return Rejected(RejectionCode.ACCOUNT_NOT_FOUND)
        if ending == "04":
            return Uncertain(UncertainReason.TIMED_OUT)
        if ending == "05":
            return Deferred(retry_after_seconds=5)
        scenario = _SCENARIOS.get(ending, "paid")
        accepted = int(self._clock.now().timestamp())
        amount = request.amount
        return Accepted(
            f"sbxp:{scenario}:{amount.currency.value}:{amount.minor}:{accepted}:{request.request_id}"
        )

    async def disbursement_status(
        self, *, request_id: str, provider_reference: str | None
    ) -> StatusReading:
        if provider_reference is None:
            return NotFound()
        try:
            prefix, scenario, currency, minor, accepted, owner = provider_reference.split(":")
            amount = Money(int(minor), Currency(currency))
            accepted_at = datetime.fromtimestamp(int(accepted), UTC)
        except ValueError:
            return NotFound()
        if prefix != "sbxp" or owner != request_id:
            return NotFound()
        if self._clock.now() - accepted_at < SEND_TIME:
            return Pending(provider_reference)
        if scenario == "fails":
            return Rejected(RejectionCode.OTHER, provider_reference)
        if scenario == "mismatch":
            return Confirmed(provider_reference, Money(amount.minor - 1, amount.currency))
        return Confirmed(provider_reference, amount)
