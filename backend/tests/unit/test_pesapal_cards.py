"""The Pesapal adapter against a fake that answers as Pesapal API 3.0 documents: success,
refusal, a timeout, a lost connection, a server error and an answer we cannot read."""

import json
from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from app.infrastructure.pesapal import PesapalSettings
from app.modules.payments.domain.cards import CancelResult, CardOrderRequest, OrderOpened
from app.modules.payments.domain.outcomes import (
    Confirmed,
    Deferred,
    Pending,
    Rejected,
    RejectionCode,
    Unavailable,
    Uncertain,
)
from app.modules.payments.domain.provider import InvalidNotice
from app.modules.payments.domain.provider_refunds import RefundRequest, RequestResult
from app.modules.payments.providers.pesapal import PesapalCards
from app.shared.money import Currency, Money
from tests.support.clock import FakeClock

BASE = "https://cybqa.pesapal.com/pesapalv3"
TOKEN = "n" * 40
Answer = Callable[[httpx.Request], httpx.Response]


def settings() -> PesapalSettings:
    return PesapalSettings.model_validate(
        {
            "consumer_key": SecretStr("key"),
            "consumer_secret": SecretStr("secret"),
            "base_url": BASE,
            "ipn_token": SecretStr(TOKEN),
        }
    )


class Pesapal:
    def __init__(self, answer: Answer) -> None:
        self.answer = answer
        self.calls: list[str] = []
        self.registered = 0
        self.tokens = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/pesapalv3")
        self.calls.append(path)
        if path == "/api/Auth/RequestToken":
            self.tokens += 1
            assert json.loads(request.content) == {
                "consumer_key": "key",
                "consumer_secret": "secret",
            }
            return httpx.Response(200, json={"token": f"token-{self.tokens}", "status": "200"})
        if path == "/api/URLSetup/GetIpnList":
            return httpx.Response(200, json=[{"url": "https://elsewhere/", "ipn_id": "other"}])
        if path == "/api/URLSetup/RegisterIPN":
            self.registered += 1
            body = json.loads(request.content)
            assert body == {
                "url": f"https://api.finvestug.com/provider-callbacks/cards/pesapal/{TOKEN}",
                "ipn_notification_type": "GET",
            }
            return httpx.Response(200, json={"ipn_id": "ipn-9", "status": "200"})
        return self.answer(request)


def adapter(fake: Pesapal) -> PesapalCards:
    clock = FakeClock()
    return PesapalCards(settings(), httpx.AsyncClient(transport=httpx.MockTransport(fake)), clock)


def order() -> CardOrderRequest:
    return CardOrderRequest(
        "-P6Tu6H1w4XyH0RidYpK", Money(1_035_000, Currency.UGX), "Finvest", "+256772123456"
    )


def opened(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert body["amount"] == 1_035_000 and body["currency"] == "UGX"
    assert body["notification_id"] == "ipn-9"
    assert body["billing_address"] == {"phone_number": "0772123456", "country_code": "UG"}
    assert body["callback_url"].startswith("https://app.finvestug.com/pay/card/return?payment=")
    return httpx.Response(
        200,
        json={
            "order_tracking_id": "b945e4af-80a5-4ec1-8706-e03f8332fb04",
            "merchant_reference": body["id"],
            "redirect_url": "https://cybqa.pesapal.com/pesapaliframe/PesapalIframe3/Index?x=1",
            "error": None,
            "status": "200",
        },
    )


async def test_an_order_is_opened_with_its_page_and_the_notification_address_registered_once() -> (
    None
):
    fake = Pesapal(opened)
    cards = adapter(fake)
    first = await cards.open_order(order())
    second = await cards.open_order(order())
    assert isinstance(first, OrderOpened) and isinstance(second, OrderOpened)
    assert first.provider_reference == "b945e4af-80a5-4ec1-8706-e03f8332fb04"
    assert fake.registered == 1 and fake.tokens == 1


def raises(error: type[Exception]) -> Answer:
    def answer(request: httpx.Request) -> httpx.Response:
        raise error("boom", request=request)  # type: ignore[call-arg]

    return answer


@pytest.mark.parametrize(
    ("answer", "kind"),
    [
        (lambda _: httpx.Response(429), Deferred),
        (lambda _: httpx.Response(403), Deferred),
        (raises(httpx.ConnectError), Deferred),
        (lambda _: httpx.Response(400), Rejected),
        (
            lambda _: httpx.Response(
                200, json={"error": {"code": "invalid_amount"}, "status": "500"}
            ),
            Rejected,
        ),
        (raises(httpx.ReadTimeout), Uncertain),
        (raises(httpx.RemoteProtocolError), Uncertain),
        (lambda _: httpx.Response(502), Uncertain),
        (lambda _: httpx.Response(200, content=b"<html>"), Uncertain),
        (
            lambda _: httpx.Response(
                200, json={"order_tracking_id": "t", "redirect_url": "http://insecure/"}
            ),
            Uncertain,
        ),
    ],
)
async def test_every_way_an_order_can_end_is_sorted(answer: Answer, kind: type) -> None:
    assert isinstance(await adapter(Pesapal(answer)).open_order(order()), kind)


async def test_a_refused_token_is_fetched_again_once() -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        if len(seen) == 1:
            return httpx.Response(401)
        return opened(request)

    fake = Pesapal(answer)
    assert isinstance(await adapter(fake).open_order(order()), OrderOpened)
    assert seen == ["Bearer token-1", "Bearer token-2"]


def status(**fields: object) -> Answer:
    def answer(request: httpx.Request) -> httpx.Response:
        assert request.url.params["orderTrackingId"] == "track-1"
        body = {
            "merchant_reference": "-P6Tu6H1w4XyH0RidYpK",
            "currency": "UGX",
            "amount": 1035000.0,
            "status": "200",
            **fields,
        }
        # Pesapal writes amounts as decimals: the adapter must not read them as floats.
        return httpx.Response(200, content=json.dumps(body).encode())

    return answer


async def read(answer: Answer) -> object:
    found = await adapter(Pesapal(answer)).order_status(
        request_id="-P6Tu6H1w4XyH0RidYpK", provider_reference="track-1"
    )
    return found


async def test_a_completed_order_confirms_its_amount_and_card() -> None:
    found = await read(
        status(
            status_code=1,
            payment_method="Visa",
            payment_account="476173**0010",
            confirmation_code="7XDPLQ8X1M",
        )
    )
    assert found.reading == Confirmed("track-1", Money(1_035_000, Currency.UGX))  # type: ignore[attr-defined]
    assert found.details.brand == "Visa" and found.details.last_four == "0010"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"status_code": 0}, Pending("track-1")),
        (
            {"status_code": 2, "description": "Insufficient funds"},
            Rejected(RejectionCode.INSUFFICIENT_FUNDS, "track-1"),
        ),
        ({"status_code": 2}, Rejected(RejectionCode.DECLINED, "track-1")),
        ({"status_code": 3}, Rejected(RejectionCode.OTHER, "track-1")),
        ({"status_code": 7}, Unavailable()),
        ({"status_code": 1, "amount": "1035000.5"}, Unavailable()),
        ({"status_code": 1, "amount": 1035000.5}, Unavailable()),
        ({"status_code": 1, "currency": "USD"}, Unavailable()),
        ({"status_code": 1, "merchant_reference": "someone-else"}, Unavailable()),
    ],
)
async def test_every_status_is_sorted(fields: dict[str, object], expected: object) -> None:
    found = await read(status(**fields))
    assert found.reading == expected  # type: ignore[attr-defined]


async def test_a_status_read_that_fails_says_nothing() -> None:
    assert (await read(lambda _: httpx.Response(500))).reading == Unavailable()  # type: ignore[attr-defined]
    assert (await read(raises(httpx.ReadTimeout))).reading == Unavailable()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("answer", "result"),
    [
        (lambda _: httpx.Response(200, json={"status": "200"}), CancelResult.CANCELLED),
        (lambda _: httpx.Response(200, json={"status": "500"}), CancelResult.REFUSED),
        (lambda _: httpx.Response(503), CancelResult.UNKNOWN),
        (raises(httpx.ReadTimeout), CancelResult.UNKNOWN),
    ],
)
async def test_cancelling_an_order(answer: Answer, result: CancelResult) -> None:
    assert await adapter(Pesapal(answer)).cancel_order(provider_reference="t") is result


def test_a_notification_is_read_only_at_our_address() -> None:
    cards = adapter(Pesapal(opened))
    query = {
        "OrderTrackingId": "track-1",
        "OrderMerchantReference": "-P6Tu6H1w4XyH0RidYpK",
        "OrderNotificationType": "IPNCHANGE",
    }
    notice = cards.read_notice(TOKEN, query, b"")
    assert notice.request_id == "-P6Tu6H1w4XyH0RidYpK" and notice.provider_reference == "track-1"
    assert cards.read_notice(TOKEN, {}, json.dumps(query).encode()) == notice
    with pytest.raises(InvalidNotice):
        cards.read_notice("x" * 40, query, b"")
    with pytest.raises(InvalidNotice):
        cards.read_notice(TOKEN, {**query, "OrderTrackingId": "<bad>"}, b"")


def test_decimals_are_never_floats() -> None:
    from app.infrastructure.pesapal import read_json

    assert read_json(b'{"amount": 1000.10}')["amount"] == Decimal("1000.10")


# Refunds -----------------------------------------------------------------------------------------


def refund(amount: int = 1_000_000) -> RefundRequest:
    return RefundRequest("-OdRefund00000000001", "PSP1234", Money(amount, Currency.UGX), "Refund")


async def test_a_refund_is_asked_by_its_confirmation_code_and_never_names_the_person() -> None:
    seen: list[dict[str, object]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/api/Transactions/RefundRequest")
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "200", "message": "Refund request successfully"})

    assert await adapter(Pesapal(answer)).refund_payment(refund()) is RequestResult.ACCEPTED
    assert seen == [
        {
            "confirmation_code": "PSP1234",
            "amount": 1_000_000,
            "username": "Finvest",
            "remarks": "Refund",
        }
    ]


def _lost(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("lost", request=request)


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("no route", request=request)


@pytest.mark.parametrize(
    ("answer", "result"),
    [
        (lambda _: httpx.Response(200, json={"status": "500"}), RequestResult.REFUSED),
        # Pesapal's documents also name the code `error`.
        (lambda _: httpx.Response(200, json={"error": 500}), RequestResult.REFUSED),
        (lambda _: httpx.Response(200, json={"error": 200}), RequestResult.ACCEPTED),
        (lambda _: httpx.Response(400, json={}), RequestResult.REFUSED),
        (lambda _: httpx.Response(403, json={}), RequestResult.NOT_SENT),
        (lambda _: httpx.Response(429, json={}), RequestResult.NOT_SENT),
        (_unreachable, RequestResult.NOT_SENT),
        (_lost, RequestResult.UNKNOWN),
        (lambda _: httpx.Response(502, json={}), RequestResult.UNKNOWN),
        (lambda _: httpx.Response(200, content=b"<html>"), RequestResult.UNKNOWN),
        (lambda _: httpx.Response(200, json={"message": "ok"}), RequestResult.UNKNOWN),
    ],
)
async def test_every_way_a_refund_request_can_end_is_sorted(
    answer: Answer, result: RequestResult
) -> None:
    assert await adapter(Pesapal(answer)).refund_payment(refund()) is result


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"status_code": 3, "merchant_reference": "-OdDeposit0000000001"}, True),
        ({"status_code": 1, "merchant_reference": "-OdDeposit0000000001"}, False),
        ({"status_code": 0, "merchant_reference": "-OdDeposit0000000001"}, None),
        # About another payment: not trusted either way.
        ({"status_code": 3, "merchant_reference": "-OdDeposit0000000002"}, None),
        ({"status_code": True, "merchant_reference": "-OdDeposit0000000001"}, None),
    ],
)
async def test_a_paid_back_payment_reads_as_reversed(
    fields: dict[str, object], expected: bool | None
) -> None:
    fake = Pesapal(lambda _: httpx.Response(200, json={**fields, "status": "200"}))
    seen = await adapter(fake).payment_reversed(
        request_id="-OdDeposit0000000001", provider_reference="track-1"
    )
    assert seen is expected
