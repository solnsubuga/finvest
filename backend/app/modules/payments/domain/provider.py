"""The port a mobile money collections provider implements. Pure: a Protocol and its values.

The adapter does the network call and sorts every way it can end into a `SendResult` or a
`StatusReading`. It never raises for a provider or network failure. An exception that does
escape a request is treated as uncertain, because the request may have gone out.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from app.modules.payments.domain.deposits import Network
from app.modules.payments.domain.outcomes import SendResult, StatusReading
from app.shared.money import Money


@dataclass(frozen=True, slots=True)
class CollectionRequest:
    """Ask a payer to approve a payment on their phone.

    `request_id` is our deposit ID. The provider is given it as the external reference, so the
    request can be found again by a status read even when no answer to the send came back.
    """

    request_id: str
    network: Network
    msisdn: str
    amount: Money


@dataclass(frozen=True, slots=True)
class ProviderNotice:
    """A callback, after its signature was checked. A hint to read the status, never proof."""

    notice_id: str
    request_id: str | None
    provider_reference: str | None


class InvalidNotice(Exception):
    """A callback that is not from the provider, or that cannot be read."""


class CollectionsProvider(Protocol):
    @property
    def name(self) -> str:
        """Stored with each deposit, such as "iotec" or "sandbox"."""
        ...

    async def request_collection(self, request: CollectionRequest) -> SendResult: ...

    async def collection_status(
        self, *, request_id: str, provider_reference: str | None
    ) -> StatusReading: ...

    def read_notice(self, headers: Mapping[str, str], body: bytes) -> ProviderNotice:
        """Check the callback's signature and parse it. Raises `InvalidNotice`."""
        ...
