"""Pesapal card payments, against Pesapal API 3.0 (developer.pesapal.com).

An order is opened with `POST /api/Transactions/SubmitOrderRequest`. Pesapal answers with its
`order_tracking_id` and a `redirect_url`, the hosted page where the payer types their card and
passes 3-D Secure. Nothing can be paid without that page. The result is read back with
`GET /api/Transactions/GetTransactionStatus`: `status_code` 1 is completed, 2 failed, 3 reversed
and 0 not paid (yet). An unpaid order is cancelled with `POST /api/Transactions/CancelOrder`.
A paid one is paid back to the card with `POST /api/Transactions/RefundRequest`, by its
confirmation code. Pesapal's `"200"` only means it took the request: the merchant approves it
and Pesapal's finance team pays it out later. It takes one refund per payment.

How each way an order can end is sorted (see `domain/cards.py`):

| What happened | Result |
| --- | --- |
| No token or notification address, or the connection could not be opened | Deferred |
| 401, 403 or 429 | Deferred: Pesapal took nothing |
| 200 with a tracking ID and an https page | OrderOpened |
| 200 with an `error`, or a 400 | Rejected (`other`) |
| A timeout or dropped connection after sending, a 5xx, an unreadable answer | Uncertain |

Pesapal gives amounts as decimal numbers. They are read as `Decimal`, never a float, and a
shilling amount with a fraction is not read as paid. The notification Pesapal sends is not
signed: its address carries a long random token, and it only ever causes a status read.

The notification address is registered with Pesapal once, the first time an order is opened,
and found again by its address after a restart, so no ID has to be copied into settings.

Nothing logged here holds a card detail, a phone number, an amount or a token.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import httpx
import structlog

from app.infrastructure.pesapal import (
    PesapalSettings,
    PesapalTokens,
    PesapalUnavailable,
    read_json,
)
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
    InvalidOutcome,
    Pending,
    Rejected,
    RejectionCode,
    Unavailable,
    Uncertain,
    UncertainReason,
)
from app.modules.payments.domain.provider import InvalidNotice, ProviderNotice
from app.modules.payments.domain.provider_refunds import RefundRequest, RequestResult
from app.shared.clock import Clock
from app.shared.money import Currency, Money

logger = structlog.get_logger()

SUBMIT_PATH = "/api/Transactions/SubmitOrderRequest"
STATUS_PATH = "/api/Transactions/GetTransactionStatus"
CANCEL_PATH = "/api/Transactions/CancelOrder"
REFUND_PATH = "/api/Transactions/RefundRequest"
# Who asked for the refund, as Pesapal records it. Never a person's name.
REFUND_USERNAME = "Finvest"
REGISTER_IPN_PATH = "/api/URLSetup/RegisterIPN"
LIST_IPN_PATH = "/api/URLSetup/GetIpnList"
MAX_NOTICE_BYTES = 4096

_FIELD = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_WORD = re.compile(r"[A-Za-z][A-Za-z ]{0,23}")


class _Unreadable(Exception):
    """An answer from Pesapal that is not what its API describes."""


class PesapalCards:
    name = "pesapal"

    def __init__(
        self,
        settings: PesapalSettings,
        http: httpx.AsyncClient,
        clock: Clock,
        *,
        tokens: PesapalTokens | None = None,
    ) -> None:
        self._settings = settings
        self._http = http
        self._tokens = tokens or PesapalTokens(settings, http, clock)
        self._ipn_token = settings.ipn_token.get_secret_value()
        self._ipn_id: str | None = None
        self._ipn_lock = asyncio.Lock()

    # Opening an order -----------------------------------------------------------------------

    async def open_order(self, request: CardOrderRequest) -> CardSendResult:
        if request.total.currency is not Currency.UGX or not request.total.is_positive:
            return Rejected(RejectionCode.OTHER)
        try:
            notification_id = await self._notification_id()
        except (PesapalUnavailable, httpx.HTTPError, _Unreadable):
            # Without a notification address Pesapal refuses an order, so nothing is sent.
            logger.warning("pesapal_order_deferred", reason="no_notification_id")
            return Deferred()
        body = {
            "id": request.request_id,
            "currency": request.total.currency.value,
            "amount": int(request.total.minor),
            "description": request.description[:100],
            "callback_url": f"{self._settings.return_url}?payment={request.request_id}",
            "notification_id": notification_id,
            "billing_address": {
                "phone_number": "0" + request.phone.removeprefix("+256"),
                "country_code": "UG",
            },
        }
        try:
            response = await self._authorised("POST", SUBMIT_PATH, json=body)
        except PesapalUnavailable as error:
            logger.warning("pesapal_order_deferred", reason=str(error))
            return Deferred()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            logger.warning("pesapal_order_deferred", reason="connect_failed")
            return Deferred()
        except httpx.TimeoutException:
            logger.warning("pesapal_order_uncertain", reason="timed_out")
            return Uncertain(UncertainReason.TIMED_OUT)
        except httpx.HTTPError:
            logger.warning("pesapal_order_uncertain", reason="connection_lost")
            return Uncertain(UncertainReason.CONNECTION_LOST)
        status = response.status_code
        if status in (401, 403, 429):
            logger.warning("pesapal_order_deferred", reason=f"http_{status}")
            return Deferred()
        if status == 400:
            logger.warning("pesapal_order_refused", status_code=status)
            return Rejected(RejectionCode.OTHER)
        if status != 200:
            logger.warning("pesapal_order_uncertain", reason=f"http_{status}")
            return Uncertain(UncertainReason.PROVIDER_ERROR)
        try:
            data = read_json(response.content)
        except ValueError:
            logger.warning("pesapal_order_uncertain", reason="unreadable_answer")
            return Uncertain(UncertainReason.PROVIDER_ERROR)
        if data.get("error") or data.get("status") not in (None, "200", 200):
            logger.warning("pesapal_order_refused", code=_error_code(data))
            return Rejected(RejectionCode.OTHER)
        try:
            reference = data.get("order_tracking_id")
            page = data.get("redirect_url")
            if data.get("merchant_reference") not in (None, request.request_id):
                raise _Unreadable
            if not isinstance(reference, str) or not isinstance(page, str):
                raise _Unreadable
            return OrderOpened(reference, page)
        except (_Unreadable, InvalidOutcome):
            logger.warning("pesapal_order_uncertain", reason="unreadable_answer")
            return Uncertain(UncertainReason.PROVIDER_ERROR)

    # Reading the status ---------------------------------------------------------------------

    async def order_status(self, *, request_id: str, provider_reference: str) -> CardReading:
        try:
            response = await self._authorised(
                "GET", STATUS_PATH, params={"orderTrackingId": provider_reference}
            )
        except (PesapalUnavailable, httpx.HTTPError):
            logger.warning("pesapal_status_unavailable", reason="request_failed")
            return CardReading(Unavailable())
        if response.status_code != 200:
            logger.warning("pesapal_status_unavailable", reason=f"http_{response.status_code}")
            return CardReading(Unavailable())
        try:
            data = read_json(response.content)
            # The read must be about this deposit: Pesapal's merchant reference is our ID.
            if data.get("merchant_reference") != request_id:
                raise _Unreadable
            code = data.get("status_code")
            if isinstance(code, bool) or code not in (0, 1, 2, 3):
                raise _Unreadable
            if code == 1:
                amount = _amount(data)
                return CardReading(Confirmed(provider_reference, amount), _details(data))
            if code == 2:
                return CardReading(Rejected(_refusal(data), provider_reference))
            if code == 3:
                # Reversed before we ever confirmed it: no money is held for the client.
                return CardReading(Rejected(RejectionCode.OTHER, provider_reference))
            # 0: the order exists and nothing has been paid on it, or not yet.
            return CardReading(Pending(provider_reference))
        except (_Unreadable, InvalidOutcome, ValueError):
            logger.warning("pesapal_status_unavailable", reason="unreadable_answer")
            return CardReading(Unavailable())

    # Cancelling -----------------------------------------------------------------------------

    async def cancel_order(self, *, provider_reference: str) -> CancelResult:
        try:
            response = await self._authorised(
                "POST", CANCEL_PATH, json={"order_tracking_id": provider_reference}
            )
        except (PesapalUnavailable, httpx.HTTPError):
            logger.warning("pesapal_cancel_unknown", reason="request_failed")
            return CancelResult.UNKNOWN
        if response.status_code != 200:
            logger.warning("pesapal_cancel_unknown", reason=f"http_{response.status_code}")
            return CancelResult.UNKNOWN
        try:
            data = read_json(response.content)
        except ValueError:
            return CancelResult.UNKNOWN
        # Pesapal answers `"status": "200"` when the order was cancelled, and `"500"` when it
        # would not (it is paid, ended or was cancelled before).
        if str(data.get("status")) == "200" and not data.get("error"):
            return CancelResult.CANCELLED
        if str(data.get("status")) == "500":
            return CancelResult.REFUSED
        return CancelResult.UNKNOWN

    # Refunds --------------------------------------------------------------------------------

    async def refund_payment(self, request: RefundRequest) -> RequestResult:
        """Ask once. A connection never opened, or a token refused, sent nothing; anything after
        the request may have reached Pesapal is unknown, and is looked up by staff."""
        if request.amount.currency is not Currency.UGX or not request.amount.is_positive:
            return RequestResult.REFUSED
        body = {
            "confirmation_code": request.confirmation_code,
            "amount": int(request.amount.minor),
            "username": REFUND_USERNAME,
            "remarks": request.remarks[:100],
        }
        try:
            response = await self._authorised("POST", REFUND_PATH, json=body)
        except PesapalUnavailable as error:
            logger.warning(
                "pesapal_refund_not_sent", reason=str(error), refund_id=request.refund_id
            )
            return RequestResult.NOT_SENT
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            logger.warning("pesapal_refund_not_sent", reason="connect_failed")
            return RequestResult.NOT_SENT
        except httpx.HTTPError:
            logger.warning(
                "pesapal_refund_unknown", reason="no_answer", refund_id=request.refund_id
            )
            return RequestResult.UNKNOWN
        status = response.status_code
        if status in (401, 403, 429):
            logger.warning("pesapal_refund_not_sent", reason=f"http_{status}")
            return RequestResult.NOT_SENT
        if status == 400:
            logger.warning("pesapal_refund_refused", status_code=status)
            return RequestResult.REFUSED
        if status != 200:
            logger.warning("pesapal_refund_unknown", reason=f"http_{status}")
            return RequestResult.UNKNOWN
        try:
            data = read_json(response.content)
        except ValueError:
            logger.warning("pesapal_refund_unknown", reason="unreadable_answer")
            return RequestResult.UNKNOWN
        # Pesapal's documents name the answer's code `status` in one place and `error` in
        # another: 200 is taken, 500 is refused. Anything else is not trusted either way.
        codes = {str(data.get(name)) for name in ("status", "error") if data.get(name) is not None}
        if "500" in codes:
            logger.warning("pesapal_refund_refused", refund_id=request.refund_id)
            return RequestResult.REFUSED
        if "200" in codes:
            logger.info("pesapal_refund_requested", refund_id=request.refund_id)
            return RequestResult.ACCEPTED
        logger.warning("pesapal_refund_unknown", reason="unreadable_answer")
        return RequestResult.UNKNOWN

    async def payment_reversed(self, *, request_id: str, provider_reference: str) -> bool | None:
        """True once Pesapal reads the order as reversed (status code 3), False while it still
        reads it as paid, None when it cannot tell."""
        try:
            response = await self._authorised(
                "GET", STATUS_PATH, params={"orderTrackingId": provider_reference}
            )
        except (PesapalUnavailable, httpx.HTTPError):
            return None
        if response.status_code != 200:
            return None
        try:
            data = read_json(response.content)
        except ValueError:
            return None
        code = data.get("status_code")
        if data.get("merchant_reference") != request_id or isinstance(code, bool):
            return None
        if code == 3:
            return True
        if code == 1:
            return False
        return None

    # Notifications --------------------------------------------------------------------------

    def read_notice(self, token: str, query: Mapping[str, str], body: bytes) -> ProviderNotice:
        if not hmac.compare_digest(token.encode(), self._ipn_token.encode()):
            raise InvalidNotice("bad token")
        if len(body) > MAX_NOTICE_BYTES:
            raise InvalidNotice("too large")
        fields: Mapping[str, Any] = query
        if body.strip():
            try:
                fields = read_json(body)
            except ValueError:
                raise InvalidNotice("unreadable") from None
        tracking = fields.get("OrderTrackingId")
        reference = fields.get("OrderMerchantReference")
        kind = fields.get("OrderNotificationType")
        for value in (tracking, reference, kind):
            if not isinstance(value, str) or _FIELD.fullmatch(value) is None:
                raise InvalidNotice("unreadable")
        assert isinstance(tracking, str) and isinstance(reference, str)
        return ProviderNotice(f"{tracking}:{kind}", reference, tracking)

    # HTTP -----------------------------------------------------------------------------------

    async def _notification_id(self) -> str:
        """Pesapal's ID for our notification address, registered the first time it is needed."""
        if self._ipn_id is not None:
            return self._ipn_id
        async with self._ipn_lock:
            if self._ipn_id is not None:
                return self._ipn_id
            url = self._settings.notification_url()
            listed = await self._authorised("GET", LIST_IPN_PATH)
            if listed.status_code != 200:
                raise _Unreadable
            try:
                entries = _list(listed.content)
            except ValueError:
                raise _Unreadable from None
            for entry in entries:
                found = entry.get("ipn_id")
                if entry.get("url") == url and isinstance(found, str):
                    self._ipn_id = found
                    return found
            registered = await self._authorised(
                "POST", REGISTER_IPN_PATH, json={"url": url, "ipn_notification_type": "GET"}
            )
            if registered.status_code != 200:
                raise _Unreadable
            try:
                ipn_id = read_json(registered.content).get("ipn_id")
            except ValueError:
                raise _Unreadable from None
            if not isinstance(ipn_id, str) or _FIELD.fullmatch(ipn_id) is None:
                raise _Unreadable
            logger.info("pesapal_notification_registered")
            self._ipn_id = ipn_id
            return ipn_id

    async def _authorised(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """One request with the bearer token, and once more with a new token after a 401.

        A 401 means Pesapal refused the token before looking at the request, so sending again
        with a fresh token cannot open a second order.
        """
        url = self._settings.base_url + path
        headers = {"Accept": "application/json"}
        response = await self._http.request(
            method,
            url,
            headers={**headers, "Authorization": f"Bearer {await self._tokens.bearer()}"},
            **kwargs,
        )
        if response.status_code != 401:
            return response
        self._tokens.forget()
        return await self._http.request(
            method,
            url,
            headers={**headers, "Authorization": f"Bearer {await self._tokens.bearer()}"},
            **kwargs,
        )


def _list(content: bytes) -> list[dict[str, Any]]:
    data = json.loads(content, parse_float=Decimal)
    if not isinstance(data, list):
        raise ValueError("not a list")
    return [entry for entry in data if isinstance(entry, dict)]


def _amount(data: Mapping[str, Any]) -> Money:
    if data.get("currency") != Currency.UGX.value:
        raise _Unreadable
    amount = data.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, Decimal)):
        raise _Unreadable
    value = Decimal(amount)
    if not value.is_finite() or value != value.to_integral_value() or value <= 0:
        # A fraction of a shilling is not an amount we can have been paid.
        raise _Unreadable
    return Money(int(value), Currency.UGX)


def _details(data: Mapping[str, Any]) -> CardDetails:
    """The card's brand and last four digits, and Pesapal's confirmation code. Anything that
    does not look right is left out rather than kept: these are shown, never relied on."""
    method = data.get("payment_method")
    brand = method.strip() if isinstance(method, str) else None
    if brand is not None and _WORD.fullmatch(brand) is None:
        brand = None
    account = data.get("payment_account")
    last_four = None
    if isinstance(account, str):
        digits = re.sub(r"[^0-9]", "", account[-4:])
        last_four = digits if len(digits) == 4 else None
    confirmation = data.get("confirmation_code")
    if (
        not isinstance(confirmation, str)
        or re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", confirmation) is None
    ):
        confirmation = None
    return CardDetails(brand, last_four, confirmation)


def _refusal(data: Mapping[str, Any]) -> RejectionCode:
    words = " ".join(
        value.lower()
        for value in (data.get("payment_status_description"), data.get("description"))
        if isinstance(value, str)
    )
    if "insufficient" in words or "funds" in words:
        return RejectionCode.INSUFFICIENT_FUNDS
    if "limit" in words:
        return RejectionCode.LIMIT_EXCEEDED
    return RejectionCode.DECLINED


def _error_code(data: Mapping[str, Any]) -> str | None:
    error = data.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and _FIELD.fullmatch(code) else None
