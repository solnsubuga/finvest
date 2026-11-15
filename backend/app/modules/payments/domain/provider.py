"""The ports a collections provider implements, for mobile money and for cards. Pure.

The adapter does the network call and sorts every way it can end into a `SendResult` or a
`StatusReading`. It never raises for a provider or network failure. An exception that does
escape a request is treated as uncertain, because the request may have gone out.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from app.modules.payments.domain.cards import (
    CancelResult,
    CardOrderRequest,
    CardReading,
    CardSendResult,
)
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


class CardCollectionsProvider(Protocol):
    """A card provider with a hosted page. Every failure is sorted into a result, never raised.

    Pure types live in `domain/cards.py`. A notification is read from the query and the body,
    because a card provider may call either way.
    """

    @property
    def name(self) -> str:
        """Stored with each deposit, such as "pesapal" or "card_sandbox"."""
        ...

    async def open_order(self, request: CardOrderRequest) -> CardSendResult: ...

    async def order_status(self, *, request_id: str, provider_reference: str) -> CardReading: ...

    async def cancel_order(self, *, provider_reference: str) -> CancelResult: ...

    def read_notice(self, token: str, query: Mapping[str, str], body: bytes) -> ProviderNotice:
        """Check the notification's address token and read it. Raises `InvalidNotice`."""
        ...
