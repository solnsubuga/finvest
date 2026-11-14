"""ioTec Pay disbursements: a payout sent to a mobile money number from Finvest's ioTec wallet.

From ioTec's API reference (docs/payouts.md, "ioTec Pay"):

* `POST /api/disbursements/disburse` sends money, with our payout ID as `externalId`.
* `GET /api/disbursements/status/{id}` reads it by ioTec's own ID, and
  `GET /api/disbursements/external-id/{externalId}` by ours, when no answer to the send came back.

ioTec does not refuse a second request with the same `externalId`, so nothing here relies on it
to stop a double payment. The payout worker sends a payout at most once, and an answer this
adapter cannot be sure of is `Uncertain`, which is read back and never sent again.

Every way a call can end is sorted into the port's outcomes. The rule for each is what is safe
to do next:

| What happened | Send | Status read |
| --- | --- | --- |
| No token, or the connection was never made | `Deferred`: nothing reached ioTec | `Unavailable` |
| 401 twice, 404, 429, or a 400 about our wallet or its balance | `Deferred` | `Unavailable` |
| Any other 400 | `Rejected` | `Unavailable` |
| No answer after sending, a 5xx, or an answer we cannot read | `Uncertain` | `Unavailable` |
| 404 on a status read | | `NotFound` |

A 400 about the wallet is ours to fix, not the client's, so it waits rather than failing the
payout: the sweep keeps trying, and staff see the payout as held up after two hours.

Nothing here logs. A phone number, an amount or ioTec's own message text never leaves this file
except in the request to ioTec.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.infrastructure.iotec import IotecSettings, IotecTokens, IotecUnavailable
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
    Unavailable,
    Uncertain,
    UncertainReason,
)
from app.modules.payouts.domain.provider import DisbursementRequest
from app.shared.money import Currency, Money

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# ioTec's statuses, by what they say about the money.
_PAID = frozenset({"Success"})
_FAILED = frozenset({"Failed", "Rejected", "Cancelled", "RolledBack"})
# Words in a 400 that mean our wallet, not the client's number, is the problem.
_OURS = ("wallet", "balance", "insufficient", "fund")
# Shown to the client on their mobile money message. No amount, no name.
_PAYEE_NOTE = "Finvest payout"


class _Unreadable(Exception):
    """ioTec's answer is not what its reference describes."""


class IotecDisbursements:
    name = "iotec"

    def __init__(
        self, settings: IotecSettings, tokens: IotecTokens, http: httpx.AsyncClient
    ) -> None:
        self._settings = settings
        self._tokens = tokens
        self._http = http

    async def send_disbursement(self, request: DisbursementRequest) -> SendResult:
        if request.amount.currency is not Currency.UGX or not request.msisdn.startswith("+256"):
            # ioTec pays Ugandan numbers in shillings. Nothing was sent.
            return Rejected(RejectionCode.OTHER)
        body = {
            "category": "MobileMoney",
            "currency": self._settings.currency_code,
            "walletId": self._settings.wallet_id,
            "externalId": request.request_id,
            "payee": request.msisdn.removeprefix("+"),
            "amount": request.amount.minor,
            "payeeNote": _PAYEE_NOTE,
        }

        def call(token: str) -> Awaitable[httpx.Response]:
            return self._http.post(
                self._url("/api/disbursements/disburse"), json=body, headers=_auth(token)
            )

        try:
            answer = await self._authorised(call)
        except IotecUnavailable:
            return Deferred()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            # The request never left this process.
            return Deferred()
        except httpx.TimeoutException:
            return Uncertain(UncertainReason.TIMED_OUT)
        except httpx.TransportError:
            return Uncertain(UncertainReason.CONNECTION_LOST)

        status = answer.status_code
        if status in (401, 404):
            return Deferred()
        if status == 429:
            return Deferred(_retry_after(answer))
        if status == 400:
            return Deferred() if _about_our_wallet(answer) else Rejected(RejectionCode.OTHER)
        if status != 200:
            return Uncertain(UncertainReason.PROVIDER_ERROR)
        try:
            found = _read(answer)
            reference = _reference(found)
            if found.get("externalId") != request.request_id:
                raise _Unreadable()
            state = found.get("status")
        except _Unreadable:
            return Uncertain(UncertainReason.PROVIDER_ERROR)
        if state in _FAILED:
            return Rejected(RejectionCode.OTHER, reference)
        return Accepted(reference)

    async def disbursement_status(
        self, *, request_id: str, provider_reference: str | None
    ) -> StatusReading:
        if provider_reference is not None and _UUID.fullmatch(provider_reference):
            path = f"/api/disbursements/status/{provider_reference}"
        else:
            path = f"/api/disbursements/external-id/{request_id}"

        def call(token: str) -> Awaitable[httpx.Response]:
            return self._http.get(self._url(path), headers=_auth(token))

        try:
            answer = await self._authorised(call)
        except (IotecUnavailable, httpx.TransportError):
            return Unavailable()
        if answer.status_code == 404:
            return NotFound()
        if answer.status_code != 200:
            return Unavailable()
        try:
            found = _read(answer)
            reference = _reference(found)
            if found.get("externalId") != request_id:
                # Not our payout. Say nothing about ours.
                return NotFound()
            state = found.get("status")
            if state in _PAID:
                return Confirmed(reference, self._amount(found))
        except _Unreadable:
            return Unavailable()
        if state in _FAILED:
            return Rejected(RejectionCode.OTHER, reference)
        # Pending, SentToVendor, AwaitingApproval, Scheduled, or one added later.
        return Pending(reference)

    async def _authorised(self, call: Callable[[str], Awaitable[httpx.Response]]) -> httpx.Response:
        """One call, and once more with a new token if ioTec says the token is no good."""
        answer = await call(await self._tokens.bearer())
        if answer.status_code == 401:
            self._tokens.forget()
            answer = await call(await self._tokens.bearer())
        return answer

    def _url(self, path: str) -> str:
        return self._settings.pay_url.rstrip("/") + path

    def _amount(self, found: dict[str, Any]) -> Money:
        """What ioTec says it paid, in shillings. Anything else is not believed."""
        if found.get("currency") != self._settings.currency_code:
            raise _Unreadable()
        amount = found.get("amount")
        if not isinstance(amount, Decimal | int) or isinstance(amount, bool):
            raise _Unreadable()
        whole = Decimal(amount)
        if whole <= 0 or whole != whole.to_integral_value():
            raise _Unreadable()
        try:
            return Money(int(whole), Currency.UGX)
        except ValueError as error:
            raise _Unreadable() from error


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _read(answer: httpx.Response) -> dict[str, Any]:
    try:
        found = json.loads(answer.content, parse_float=Decimal)
    except (ValueError, InvalidOperation) as error:
        raise _Unreadable() from error
    if not isinstance(found, dict):
        raise _Unreadable()
    return found


def _reference(found: dict[str, Any]) -> str:
    reference = found.get("id")
    if not isinstance(reference, str) or not _UUID.fullmatch(reference):
        raise _Unreadable()
    return reference


def _about_our_wallet(answer: httpx.Response) -> bool:
    """Whether ioTec's refusal is about Finvest's wallet. Its text is read here and dropped."""
    try:
        found = json.loads(answer.content)
    except ValueError:
        return False
    if not isinstance(found, dict):
        return False
    text = " ".join(str(found.get(key) or "") for key in ("code", "message")).lower()
    return any(word in text for word in _OURS)


def _retry_after(answer: httpx.Response) -> int | None:
    hint = answer.headers.get("Retry-After", "")
    return int(hint) if hint.isdigit() and int(hint) <= 3600 else None
