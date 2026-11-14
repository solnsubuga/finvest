"""ioTec Pay mobile money collections, against the API at https://iotec.io/api-docs/pay.

A collection is `POST /api/collections/collect`. The payer is prompted on their phone and the
request stays `Pending` until they approve (`Success`), decline or time out (`Failed`). The
status is read back with `GET /api/collections/status/{id}`, or, when no answer to the send
came back and we never learnt ioTec's ID, with `GET /api/collections/external-id/{deposit id}`:
the deposit ID is sent as `externalId` for exactly that.

How each way a send can end is sorted (see `domain/outcomes.py`):

| What happened | Result |
| --- | --- |
| No token, the pool was full, or the connection could not be opened | Deferred: nothing was sent |
| 401 or 403 (the token is dropped), 429 | Deferred: ioTec took nothing |
| 400 or 404 | Rejected (`other`): ioTec refused the request itself |
| 200 with `Failed`, `Rejected`, `Cancelled` or `RolledBack` | Rejected, with ioTec's ID |
| 200 with any other status | Accepted: the worker reads the status |
| A timeout or a dropped connection after sending, a 5xx, an unreadable answer | Uncertain |

The payer pays exactly the investment's amount: ioTec's charges go to our wallet
(`ChargeWallet`). A callback is checked against the token ioTec is set to send, and is only
a hint: it never changes a deposit.

Nothing logged here holds a phone number, an amount or a token.
"""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import httpx
import structlog

from app.infrastructure.iotec import IotecSettings, IotecTokens, IotecUnavailable
from app.modules.payments.domain.outcomes import (
    Accepted,
    Confirmed,
    Deferred,
    InvalidOutcome,
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
from app.modules.payments.domain.provider import (
    CollectionRequest,
    InvalidNotice,
    ProviderNotice,
)
from app.shared.clock import Clock
from app.shared.money import Currency, Money

logger = structlog.get_logger()

COLLECT_PATH = "/api/collections/collect"
STATUS_PATH = "/api/collections/status/{id}"
EXTERNAL_ID_PATH = "/api/collections/external-id/{id}"
PAYER_NOTE = "Finvest investment"
MAX_NOTICE_BYTES = 16 * 1024

_SAFE_CODE = re.compile(r"[A-Za-z_]{1,64}")
_REFUSED = frozenset({"Failed", "Rejected", "Cancelled", "RolledBack"})
_UNDER_WAY = frozenset({"Pending", "SentToVendor", "AwaitingApproval", "Scheduled"})
# ioTec's machine-friendly `statusCode` for a refusal, matched loosely: ioTec does not publish
# the list. Anything not recognised is `other`.
_REFUSAL_WORDS: tuple[tuple[str, RejectionCode], ...] = (
    ("insufficient", RejectionCode.INSUFFICIENT_FUNDS),
    ("balance", RejectionCode.INSUFFICIENT_FUNDS),
    ("timeout", RejectionCode.APPROVAL_TIMED_OUT),
    ("timed", RejectionCode.APPROVAL_TIMED_OUT),
    ("expired", RejectionCode.APPROVAL_TIMED_OUT),
    ("limit", RejectionCode.LIMIT_EXCEEDED),
    ("not_registered", RejectionCode.ACCOUNT_NOT_FOUND),
    ("notregistered", RejectionCode.ACCOUNT_NOT_FOUND),
    ("invalid_number", RejectionCode.ACCOUNT_NOT_FOUND),
    ("not_found", RejectionCode.ACCOUNT_NOT_FOUND),
    ("declined", RejectionCode.DECLINED),
    ("rejected", RejectionCode.DECLINED),
    ("cancel", RejectionCode.DECLINED),
    ("pin", RejectionCode.DECLINED),
)


class _Unreadable(Exception):
    """An answer from ioTec that is not what its API describes."""


class IotecCollections:
    name = "iotec"

    def __init__(
        self,
        settings: IotecSettings,
        http: httpx.AsyncClient,
        clock: Clock,
        *,
        tokens: IotecTokens | None = None,
    ) -> None:
        self._settings = settings
        self._http = http
        self._tokens = tokens or IotecTokens(settings, http, clock)
        self._callback_token = (
            settings.callback_token.get_secret_value() if settings.callback_token else None
        )

    # Sending --------------------------------------------------------------------------------

    async def request_collection(self, request: CollectionRequest) -> SendResult:
        if request.amount.currency is not Currency.UGX or not request.amount.is_positive:
            # Mobile money in Uganda is in shillings. Nothing is sent.
            return Rejected(RejectionCode.OTHER)
        body = {
            "category": "MobileMoney",
            "currency": self._settings.currency_code,
            "walletId": self._settings.wallet_id,
            "externalId": request.request_id,
            "payer": request.msisdn.removeprefix("+"),
            "amount": request.amount.minor,
            "payerNote": PAYER_NOTE,
            "payeeNote": request.request_id,
            "transactionChargesCategory": "ChargeWallet",
        }
        try:
            response = await self._authorised("POST", COLLECT_PATH, json=body)
        except IotecUnavailable as error:
            logger.warning("iotec_collect_deferred", reason=str(error))
            return Deferred()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            # The connection was never opened, so the request never left.
            logger.warning("iotec_collect_deferred", reason="connect_failed")
            return Deferred()
        except httpx.TimeoutException:
            logger.warning("iotec_collect_uncertain", reason="timed_out")
            return Uncertain(UncertainReason.TIMED_OUT)
        except httpx.HTTPError:
            logger.warning("iotec_collect_uncertain", reason="connection_lost")
            return Uncertain(UncertainReason.CONNECTION_LOST)
        status = response.status_code
        if status in (401, 403, 429):
            logger.warning("iotec_collect_deferred", reason=f"http_{status}")
            return Deferred(retry_after_seconds=_retry_after(response))
        if status in (400, 404):
            logger.warning("iotec_collect_refused", status_code=status, code=_error_code(response))
            return Rejected(RejectionCode.OTHER)
        if status != 200:
            logger.warning("iotec_collect_uncertain", reason=f"http_{status}")
            return Uncertain(UncertainReason.PROVIDER_ERROR)
        try:
            data = _read(response)
            reference = _reference(data)
            state = _status(data)
            if state in _REFUSED:
                return Rejected(_refusal(data), reference)
            if state in _UNDER_WAY or state == "Success":
                return Accepted(reference)
            raise _Unreadable
        except (_Unreadable, InvalidOutcome):
            logger.warning("iotec_collect_uncertain", reason="unreadable_answer")
            return Uncertain(UncertainReason.PROVIDER_ERROR)

    # Reading the status ---------------------------------------------------------------------

    async def collection_status(
        self, *, request_id: str, provider_reference: str | None
    ) -> StatusReading:
        if provider_reference is not None:
            path = STATUS_PATH.format(id=provider_reference)
        else:
            path = EXTERNAL_ID_PATH.format(id=request_id)
        try:
            response = await self._authorised("GET", path)
        except (IotecUnavailable, httpx.HTTPError):
            logger.warning("iotec_status_unavailable", reason="request_failed")
            return Unavailable()
        if response.status_code == 404:
            return NotFound()
        if response.status_code != 200:
            logger.warning("iotec_status_unavailable", reason=f"http_{response.status_code}")
            return Unavailable()
        try:
            data = _read(response)
            reference = _reference(data)
            # Both reads must be about this deposit: ioTec's ID we hold, and our own ID sent
            # as `externalId`. Anything else is not read as this deposit's money.
            if provider_reference is not None and reference != provider_reference:
                raise _Unreadable
            if data.get("externalId") != request_id:
                raise _Unreadable
            state = _status(data)
            if state == "Success":
                return Confirmed(reference, self._amount(data))
            if state in _REFUSED:
                return Rejected(_refusal(data), reference)
            if state in _UNDER_WAY:
                return Pending(reference)
            raise _Unreadable
        except (_Unreadable, InvalidOutcome):
            # Nothing is decided on an answer we cannot read. The read is repeated later.
            logger.warning("iotec_status_unavailable", reason="unreadable_answer")
            return Unavailable()

    def _amount(self, data: Mapping[str, Any]) -> Money:
        if data.get("currency") != self._settings.currency_code:
            raise _Unreadable
        amount = data.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, (int, Decimal)):
            raise _Unreadable
        if isinstance(amount, Decimal) and (
            not amount.is_finite() or amount != amount.to_integral_value()
        ):
            # A fraction of a shilling is not an amount we can have been paid.
            raise _Unreadable
        return Money(int(amount), Currency.UGX)

    # Callbacks ------------------------------------------------------------------------------

    def read_notice(self, headers: Mapping[str, str], body: bytes) -> ProviderNotice:
        if self._callback_token is None or len(body) > MAX_NOTICE_BYTES:
            raise InvalidNotice("refused")
        given = {name.lower(): value for name, value in headers.items()}.get("authorization", "")
        expected = f"Bearer {self._callback_token}"
        if not hmac.compare_digest(given.encode(), expected.encode()):
            raise InvalidNotice("bad token")
        try:
            data = json.loads(body, parse_float=Decimal)
            reference, state = data["id"], data["status"]
            external_id = data.get("externalId")
        except (ValueError, KeyError, TypeError):
            raise InvalidNotice("unreadable") from None
        if not isinstance(reference, str) or not isinstance(state, str):
            raise InvalidNotice("unreadable")
        if external_id is not None and not isinstance(external_id, str):
            raise InvalidNotice("unreadable")
        # ioTec calls back on each status change, so the same transaction can call more than
        # once. Each change is its own notice; repeats of one change are recorded once.
        return ProviderNotice(f"{reference}:{state}", external_id, reference)

    # HTTP -----------------------------------------------------------------------------------

    async def _authorised(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """One request with the bearer token, and once more with a new token after a 401.

        A 401 means ioTec refused the token before looking at the request, so sending again
        with a fresh token cannot take a payment twice.
        """
        url = self._settings.pay_url.rstrip("/") + path
        response = await self._http.request(
            method,
            url,
            headers={"Authorization": f"Bearer {await self._tokens.bearer()}"},
            **kwargs,
        )
        if response.status_code != 401:
            return response
        self._tokens.forget()
        return await self._http.request(
            method,
            url,
            headers={"Authorization": f"Bearer {await self._tokens.bearer()}"},
            **kwargs,
        )


def _read(response: httpx.Response) -> Mapping[str, Any]:
    try:
        data = json.loads(response.content, parse_float=Decimal)
    except ValueError:
        raise _Unreadable from None
    if not isinstance(data, dict):
        raise _Unreadable
    return data


def _reference(data: Mapping[str, Any]) -> str:
    reference = data.get("id")
    if not isinstance(reference, str):
        raise _Unreadable
    return reference


def _status(data: Mapping[str, Any]) -> str:
    state = data.get("status")
    if not isinstance(state, str):
        raise _Unreadable
    return state


def _refusal(data: Mapping[str, Any]) -> RejectionCode:
    code = data.get("statusCode")
    if not isinstance(code, str):
        return RejectionCode.OTHER
    code = code.lower()
    for word, rejection in _REFUSAL_WORDS:
        if word in code:
            return rejection
    return RejectionCode.OTHER


def _error_code(response: httpx.Response) -> str | None:
    try:
        code = response.json().get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) and _SAFE_CODE.fullmatch(code) else None


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after", "")
    return int(value) if value.isdigit() and len(value) <= 6 else None
