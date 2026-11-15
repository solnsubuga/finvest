"""A stand-in for a card provider, for development and tests. It moves no money.

It behaves the way Pesapal does: an order is opened with a page to pay on, and a status read
gives the result. It keeps no state, so the API and the worker can run it in separate processes:
what it needs is carried in the reference it hands out. Its "page" is the client app's own
return address, so a developer is taken straight back as if they had paid.

The last three digits of the total (investment and charge) choose what happens:

| Ends in | Result |
| --- | --- |
| 001 | The card is declined |
| 002 | Not enough money on the card |
| 003 | The payer never pays, so the order is cancelled after the window |
| 004 | No answer to the order: it fails, since nobody has its page |
| 005 | The provider is busy and took nothing (deferred) |
| 006 | Paid, but for a different amount |
| anything else | Paid after a few seconds, with a Visa ending 1111 |

A refund chooses by the last three digits of what goes back: 007 is refused, 008 gets no
answer, 009 is never reached. Anything else is taken, and reads as paid back at once.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from app.modules.payments.domain.cards import (
    CancelResult,
    CardDetails,
    CardOrderRequest,
    CardReading,
    CardSendResult,
    OrderOpened,
)
from app.modules.payments.domain.outcomes import (
    Confirmed,
    Deferred,
    NotFound,
    Pending,
    Rejected,
    RejectionCode,
    Uncertain,
    UncertainReason,
)
from app.modules.payments.domain.provider import InvalidNotice, ProviderNotice
from app.modules.payments.domain.provider_refunds import RefundRequest, RequestResult
from app.shared.clock import Clock
from app.shared.money import Currency, Money

PAY_TIME = timedelta(seconds=6)
_SCENARIOS = {"001": "declined", "002": "insufficient", "003": "abandoned", "006": "mismatch"}


class SandboxCards:
    name = "card_sandbox"

    def __init__(self, clock: Clock, *, return_url: str, notice_token: str | None = None) -> None:
        self._clock = clock
        self._return_url = return_url.rstrip("/")
        self._token = notice_token

    async def open_order(self, request: CardOrderRequest) -> CardSendResult:
        ending = f"{request.total.minor:03d}"[-3:]
        if ending == "004":
            return Uncertain(UncertainReason.TIMED_OUT)
        if ending == "005":
            return Deferred()
        scenario = _SCENARIOS.get(ending, "paid")
        opened = int(self._clock.now().timestamp())
        total = request.total
        reference = f"csbx:{scenario}:{total.currency.value}:{total.minor}:{opened}"
        return OrderOpened(reference, f"{self._return_url}?payment={request.request_id}&sandbox=1")

    async def order_status(self, *, request_id: str, provider_reference: str) -> CardReading:
        try:
            prefix, scenario, currency, minor, opened = provider_reference.split(":")
            total = Money(int(minor), Currency(currency))
            opened_at = datetime.fromtimestamp(int(opened), UTC)
        except ValueError:
            return CardReading(NotFound())
        if prefix != "csbx":
            return CardReading(NotFound())
        if scenario == "cancelled" or self._clock.now() - opened_at < PAY_TIME:
            return CardReading(Pending(provider_reference))
        if scenario == "declined":
            return CardReading(Rejected(RejectionCode.DECLINED, provider_reference))
        if scenario == "insufficient":
            return CardReading(Rejected(RejectionCode.INSUFFICIENT_FUNDS, provider_reference))
        if scenario == "abandoned":
            return CardReading(Pending(provider_reference))
        paid = Money(total.minor + 1, total.currency) if scenario == "mismatch" else total
        return CardReading(
            Confirmed(provider_reference, paid),
            CardDetails("Visa", "1111", f"SBX{opened}"),
        )

    async def cancel_order(self, *, provider_reference: str) -> CancelResult:
        if ":abandoned:" in provider_reference:
            return CancelResult.CANCELLED
        return CancelResult.REFUSED

    async def refund_payment(self, request: RefundRequest) -> RequestResult:
        ending = f"{request.amount.minor:03d}"[-3:]
        return {
            "007": RequestResult.REFUSED,
            "008": RequestResult.UNKNOWN,
            "009": RequestResult.NOT_SENT,
        }.get(ending, RequestResult.ACCEPTED)

    async def payment_reversed(self, *, request_id: str, provider_reference: str) -> bool | None:
        return provider_reference.startswith("csbx:paid:")

    def read_notice(self, token: str, query: Mapping[str, str], body: bytes) -> ProviderNotice:
        if self._token is None or not hmac.compare_digest(token.encode(), self._token.encode()):
            raise InvalidNotice("bad token")
        tracking = query.get("OrderTrackingId")
        reference = query.get("OrderMerchantReference")
        if not tracking or not reference:
            raise InvalidNotice("unreadable")
        return ProviderNotice(f"{tracking}:IPNCHANGE", reference, tracking)
