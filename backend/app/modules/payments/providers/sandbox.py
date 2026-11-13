"""A stand-in for ioTec Pay collections, for development and tests. It moves no money.

ioTec's API details are not confirmed yet, so this is the only adapter. It behaves the way a
collections provider does: a request is accepted, the payer is prompted, and a status read
gives the result. It keeps no state, so the API and the worker can run it in separate
processes and a restart loses nothing: what it needs is carried in the reference it hands out.

The last two digits of the payer's number choose what happens:

| Ends in | Result |
| --- | --- |
| 01 | The payer declines |
| 02 | Not enough money |
| 03 | The payer never answers the prompt |
| 04 | No answer to the request, and the provider has no record of it (uncertain) |
| 05 | The provider is busy and took nothing (deferred) |
| 06 | Confirmed, but for a different amount |
| anything else | Approved after a few seconds |
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
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
from app.modules.payments.domain.provider import (
    CollectionRequest,
    InvalidNotice,
    ProviderNotice,
)
from app.shared.clock import Clock
from app.shared.money import Currency, Money

SIGNATURE_HEADER = "x-sandbox-signature"
PROMPT_TIME = timedelta(seconds=6)
MAX_NOTICE_BYTES = 4096

_SCENARIOS = {
    "01": "declined",
    "02": "insufficient",
    "03": "timeout",
    "06": "mismatch",
}
_REJECTIONS = {
    "declined": RejectionCode.DECLINED,
    "insufficient": RejectionCode.INSUFFICIENT_FUNDS,
    "timeout": RejectionCode.APPROVAL_TIMED_OUT,
}


class SandboxCollections:
    name = "sandbox"

    def __init__(self, clock: Clock, *, notice_secret: str | None = None) -> None:
        self._clock = clock
        self._secret = notice_secret.encode() if notice_secret else None

    async def request_collection(self, request: CollectionRequest) -> SendResult:
        ending = request.msisdn[-2:]
        if ending == "04":
            return Uncertain(UncertainReason.TIMED_OUT)
        if ending == "05":
            return Deferred(retry_after_seconds=5)
        scenario = _SCENARIOS.get(ending, "approved")
        accepted = int(self._clock.now().timestamp())
        amount = request.amount
        return Accepted(
            f"sbx:{scenario}:{amount.currency.value}:{amount.minor}:{accepted}:{request.request_id}"
        )

    async def collection_status(
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
        if prefix != "sbx" or owner != request_id:
            return NotFound()
        if self._clock.now() - accepted_at < PROMPT_TIME:
            return Pending(provider_reference)
        if scenario in _REJECTIONS:
            return Rejected(_REJECTIONS[scenario], provider_reference)
        if scenario == "mismatch":
            return Confirmed(provider_reference, Money(amount.minor + 1, amount.currency))
        return Confirmed(provider_reference, amount)

    def sign(self, body: bytes) -> str:
        """The signature a genuine sandbox callback carries. For tests and local tools."""
        if self._secret is None:
            raise InvalidNotice("no notice secret is configured")
        return hmac.new(self._secret, body, hashlib.sha256).hexdigest()

    def read_notice(self, headers: Mapping[str, str], body: bytes) -> ProviderNotice:
        if self._secret is None or len(body) > MAX_NOTICE_BYTES:
            raise InvalidNotice("refused")
        given = {name.lower(): value for name, value in headers.items()}.get(SIGNATURE_HEADER, "")
        if not hmac.compare_digest(given.encode(), self.sign(body).encode()):
            raise InvalidNotice("bad signature")
        try:
            data = json.loads(body)
            notice_id, request_id = data["id"], data.get("request_id")
            reference = data.get("reference")
        except (ValueError, KeyError, TypeError, AttributeError):
            raise InvalidNotice("unreadable") from None
        if not isinstance(notice_id, str) or not all(
            value is None or isinstance(value, str) for value in (request_id, reference)
        ):
            raise InvalidNotice("unreadable")
        return ProviderNotice(notice_id, request_id, reference)
