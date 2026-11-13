"""The port a mobile money disbursements provider implements. Pure: a Protocol and its values.

The adapter does the network call and sorts every way it can end into a `SendResult` or a
`StatusReading`, the same outcomes a collection has. It never raises for a provider or network
failure. An exception that does escape a request is treated as uncertain, because the money
may have gone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.modules.payments.domain.deposits import Network
from app.modules.payments.domain.outcomes import SendResult, StatusReading
from app.shared.money import Money


@dataclass(frozen=True, slots=True)
class DisbursementRequest:
    """Send money to a mobile money number.

    `request_id` is our payout ID. The provider is given it as the external reference, so the
    payout can be found by a status read even when no answer to the send came back, and so a
    provider that checks references refuses a second request for the same payout.
    """

    request_id: str
    network: Network
    msisdn: str
    amount: Money


class DisbursementsProvider(Protocol):
    @property
    def name(self) -> str:
        """Stored with each send attempt, such as "iotec" or "sandbox"."""
        ...

    async def send_disbursement(self, request: DisbursementRequest) -> SendResult: ...

    async def disbursement_status(
        self, *, request_id: str, provider_reference: str | None
    ) -> StatusReading: ...
