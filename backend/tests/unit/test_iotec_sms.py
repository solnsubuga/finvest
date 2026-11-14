"""The ioTec Messaging adapter against a fake ioTec: sent, refused, slowed, lost and unknown."""

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from app.infrastructure.sms import (
    SmsDeferred,
    SmsMessage,
    SmsRejected,
    SmsUncertain,
)
from app.infrastructure.sms.iotec import IotecSms, local_number

MESSAGE_ID = "5e83b187-801e-410e-b76e-f491928547e0"
KEY = "sign-in-code:-Nabc0000000000000A1"
SECRET = "IO-made-up-key-for-this-test"
MESSAGE = SmsMessage(to="+256772123456", text="Your Finvest code is 123456", idempotency_key=KEY)

Handler = Callable[[httpx.Request], httpx.Response]


def transport(handler: Handler) -> tuple[IotecSms, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    sms = IotecSms(client_id="test-client", api_key=SecretStr(SECRET), client=client)
    return sms, seen


def held(*rows: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"page": 1, "data": list(rows)})


async def test_a_message_is_sent_with_our_key_as_its_reference() -> None:
    sms, seen = transport(
        lambda _: httpx.Response(200, json={"id": MESSAGE_ID, "status": "Pending"})
    )
    result = await sms.send(MESSAGE)
    assert (result.provider, result.provider_message_id) == ("iotec", MESSAGE_ID)
    [request] = seen
    assert (request.method, request.url.path) == ("POST", "/api/sms-message")
    assert request.url.host == "messaging-api.iotec.io"
    assert request.headers["Client-Id"] == "test-client"
    assert request.headers["X-Api-Key"] == SECRET
    assert json.loads(request.content) == {
        "recipient": "0772123456",
        "body": "Your Finvest code is 123456",
        "referenceId": KEY,
    }


@pytest.mark.parametrize(
    ("phone", "local"), [("+256772123456", "0772123456"), ("+256701000000", "0701000000")]
)
def test_ugandan_mobile_numbers_are_written_as_ioTec_writes_them(phone: str, local: str) -> None:
    assert local_number(phone) == local


@pytest.mark.parametrize("phone", ["+254712345678", "+256414123456", "+2567721234567"])
async def test_a_number_ioTec_cannot_reach_is_refused_before_any_request(phone: str) -> None:
    sms, seen = transport(lambda _: httpx.Response(200, json={"id": MESSAGE_ID}))
    with pytest.raises(SmsRejected) as refused:
        await sms.send(SmsMessage(to=phone, text="Hello", idempotency_key=KEY))
    assert refused.value.code == "sms_unsupported_number"
    assert seen == []


@pytest.mark.parametrize("status", [401, 403])
async def test_refused_credentials_are_final(status: int) -> None:
    sms, _ = transport(lambda _: httpx.Response(status, json={"statusCode": status}))
    with pytest.raises(SmsRejected) as refused:
        await sms.send(MESSAGE)
    assert refused.value.code == "sms_auth_failed"
    assert SECRET not in refused.value.safe_message


async def test_a_bad_request_is_final() -> None:
    sms, _ = transport(lambda _: httpx.Response(400, json={"statusCode": 400}))
    with pytest.raises(SmsRejected):
        await sms.send(MESSAGE)


async def test_a_message_ioTec_marks_failed_is_final() -> None:
    sms, _ = transport(lambda _: httpx.Response(200, json={"id": MESSAGE_ID, "status": "Failed"}))
    with pytest.raises(SmsRejected):
        await sms.send(MESSAGE)


async def test_being_slowed_down_or_unreachable_is_safe_to_retry() -> None:
    slowed, _ = transport(lambda _: httpx.Response(429))
    with pytest.raises(SmsDeferred):
        await slowed.send(MESSAGE)

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    offline, _ = transport(unreachable)
    with pytest.raises(SmsDeferred):
        await offline.send(MESSAGE)


def lost_then(answer: httpx.Response | None) -> Handler:
    """The send times out; the follow-up lookup gets `answer`, or times out too."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" or answer is None:
            raise httpx.ReadTimeout("no answer", request=request)
        return answer

    return handle


async def test_a_lost_answer_is_settled_by_asking_ioTec_for_our_reference() -> None:
    sms, seen = transport(lost_then(held({"id": MESSAGE_ID, "referenceId": KEY, "status": "Sent"})))
    result = await sms.send(MESSAGE)
    assert result.provider_message_id == MESSAGE_ID
    lookup = seen[-1]
    assert (lookup.method, lookup.url.path) == ("GET", "/api/sms-message")
    assert lookup.url.params["ReferenceId"] == KEY
    # Asked once, never sent twice.
    assert [request.method for request in seen] == ["POST", "GET"]


@pytest.mark.parametrize(
    "answer",
    [
        held(),
        # A message with some other reference is not ours.
        held({"id": MESSAGE_ID, "referenceId": "other", "status": "Sent"}),
        httpx.Response(500),
        None,
    ],
)
async def test_a_lost_answer_that_ioTec_cannot_settle_stays_uncertain(
    answer: httpx.Response | None,
) -> None:
    sms, seen = transport(lost_then(answer))
    with pytest.raises(SmsUncertain) as uncertain:
        await sms.send(MESSAGE)
    assert [request.method for request in seen] == ["POST", "GET"]
    for private in ("772123456", "123456", SECRET):
        assert private not in uncertain.value.safe_message


async def test_a_server_error_is_checked_before_anything_else() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(502)
        return held({"id": MESSAGE_ID, "referenceId": KEY, "status": "Pending"})

    sms, seen = transport(handle)
    assert (await sms.send(MESSAGE)).provider_message_id == MESSAGE_ID
    assert [request.method for request in seen] == ["POST", "GET"]


async def test_a_lost_answer_for_a_message_ioTec_failed_is_final() -> None:
    sms, _ = transport(
        lost_then(held({"id": MESSAGE_ID, "referenceId": KEY, "status": "FailedDelivery"}))
    )
    with pytest.raises(SmsRejected):
        await sms.send(MESSAGE)


async def test_an_unexpected_id_is_not_stored() -> None:
    sms, _ = transport(lambda _: httpx.Response(200, json={"id": "<script>", "status": "Pending"}))
    assert (await sms.send(MESSAGE)).provider_message_id is None
