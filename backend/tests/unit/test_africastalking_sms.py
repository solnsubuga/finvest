"""The Africa's Talking adapter against a fake Africa's Talking: sent, refused, slowed and lost."""

from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from app.infrastructure.sms import SmsDeferred, SmsMessage, SmsRejected, SmsUncertain
from app.infrastructure.sms.africastalking import AfricasTalkingSms

MESSAGE_ID = "ATXid_0f1e2d3c4b5a69788796a5b4c3d2e1f0"
KEY = "sign-in-code:-Nabc0000000000000A1"
SECRET = "atsk_made-up-key-for-this-test"
MESSAGE = SmsMessage(to="+256772123456", text="Your Finvest code is 123456", idempotency_key=KEY)

Handler = Callable[[httpx.Request], httpx.Response]


def transport(
    handler: Handler, sender_id: str | None = "FINVEST"
) -> tuple[AfricasTalkingSms, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    sms = AfricasTalkingSms(
        username="finvest", api_key=SecretStr(SECRET), sender_id=sender_id, client=client
    )
    return sms, seen


def answered(*recipients: object) -> httpx.Response:
    return httpx.Response(
        201,
        json={
            "SMSMessageData": {
                "Message": f"Sent to {len(recipients)}/1 Total Cost: UGX 25.0000",
                "Recipients": list(recipients),
            }
        },
    )


def recipient(code: int, status: str, message_id: str | None = MESSAGE_ID) -> dict[str, object]:
    return {
        "statusCode": code,
        "number": "+256772123456",
        "status": status,
        "cost": "UGX 25.0000",
        "messageId": message_id,
    }


def raising(error: Exception) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


async def test_a_message_is_sent_as_africas_talking_expects_it() -> None:
    sms, seen = transport(lambda _: answered(recipient(101, "Success")))
    result = await sms.send(MESSAGE)
    assert (result.provider, result.provider_message_id) == ("africastalking", MESSAGE_ID)
    [request] = seen
    assert (request.method, request.url.host, request.url.path) == (
        "POST",
        "api.africastalking.com",
        "/version1/messaging",
    )
    assert request.headers["apiKey"] == SECRET
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert parse_qs(request.content.decode()) == {
        "username": ["finvest"],
        "to": ["+256772123456"],
        "message": ["Your Finvest code is 123456"],
        "from": ["FINVEST"],
    }


async def test_without_a_sender_name_africas_talking_chooses_one() -> None:
    sms, seen = transport(lambda _: answered(recipient(101, "Success")), sender_id=None)
    await sms.send(MESSAGE)
    assert "from" not in parse_qs(seen[0].content.decode())


@pytest.mark.parametrize(
    ("code", "status"), [(100, "Processed"), (101, "Success"), (102, "Queued")]
)
async def test_processed_sent_and_queued_all_count_as_sent(code: int, status: str) -> None:
    sms, _ = transport(lambda _: answered(recipient(code, status)))
    assert (await sms.send(MESSAGE)).provider == "africastalking"


async def test_a_message_id_of_another_shape_is_not_stored() -> None:
    sms, _ = transport(lambda _: answered(recipient(101, "Success", "+256772123456")))
    assert (await sms.send(MESSAGE)).provider_message_id is None


@pytest.mark.parametrize(
    ("code", "status", "error_code"),
    [
        (401, "RiskHold", "sms_rejected"),
        (402, "InvalidSenderId", "sms_rejected"),
        (403, "InvalidPhoneNumber", "sms_unsupported_number"),
        (404, "UnsupportedNumberType", "sms_unsupported_number"),
        (405, "InsufficientBalance", "sms_provider_balance"),
        (406, "UserInBlacklist", "sms_blocked_by_recipient"),
        (407, "CouldNotRoute", "sms_rejected"),
        (409, "DoNotDisturbRejection", "sms_blocked_by_recipient"),
        (500, "InternalServerError", "sms_rejected"),
        (501, "GatewayError", "sms_rejected"),
        (502, "RejectedByGateway", "sms_rejected"),
    ],
)
async def test_a_refused_recipient_is_a_rejection(code: int, status: str, error_code: str) -> None:
    sms, _ = transport(lambda _: answered(recipient(code, status, None)))
    with pytest.raises(SmsRejected) as refused:
        await sms.send(MESSAGE)
    assert refused.value.code == error_code


async def test_sent_to_nobody_is_a_rejection() -> None:
    sms, _ = transport(lambda _: answered())
    with pytest.raises(SmsRejected):
        await sms.send(MESSAGE)


@pytest.mark.parametrize("status", [401, 403])
async def test_refused_credentials_are_a_rejection(status: int) -> None:
    sms, _ = transport(
        lambda _: httpx.Response(status, text="The supplied authentication is invalid")
    )
    with pytest.raises(SmsRejected) as refused:
        await sms.send(MESSAGE)
    assert refused.value.code == "sms_auth_failed"


async def test_a_bad_request_is_a_rejection() -> None:
    sms, _ = transport(lambda _: httpx.Response(400, text="Invalid request"))
    with pytest.raises(SmsRejected):
        await sms.send(MESSAGE)


@pytest.mark.parametrize(
    "handler",
    [
        raising(httpx.ConnectError("refused")),
        raising(httpx.ConnectTimeout("slow")),
        lambda _: httpx.Response(429),
        lambda _: httpx.Response(503),
    ],
)
async def test_unreachable_or_turned_away_is_deferred(handler: Handler) -> None:
    sms, _ = transport(handler)
    with pytest.raises(SmsDeferred):
        await sms.send(MESSAGE)


@pytest.mark.parametrize(
    "handler",
    [
        raising(httpx.ReadTimeout("lost")),
        raising(httpx.RemoteProtocolError("cut")),
        lambda _: httpx.Response(500),
        lambda _: httpx.Response(502),
        lambda _: httpx.Response(504),
        lambda _: httpx.Response(201, text="not json"),
        lambda _: httpx.Response(201, json={"SMSMessageData": {"Recipients": [{"status": "?"}]}}),
    ],
)
async def test_a_lost_or_unreadable_answer_stays_uncertain(handler: Handler) -> None:
    # Africa's Talking cannot be asked about a message by our reference, so it is never sent
    # again: not here, and not through the fallback either.
    sms, seen = transport(handler)
    with pytest.raises(SmsUncertain):
        await sms.send(MESSAGE)
    assert len(seen) == 1


async def test_errors_never_hold_the_number_text_cost_or_key() -> None:
    for handler in (
        lambda _: answered(recipient(405, "InsufficientBalance")),
        lambda _: httpx.Response(401, text=SECRET),
        lambda _: httpx.Response(500, text="+256772123456"),
    ):
        sms, _ = transport(handler)
        with pytest.raises((SmsRejected, SmsUncertain)) as failed:
            await sms.send(MESSAGE)
        for private in ("+256772123456", "0772123456", "123456", "UGX", SECRET, "25.0000"):
            assert private not in str(failed.value)


def test_credentials_are_required() -> None:
    with pytest.raises(ValueError, match="username and API key"):
        AfricasTalkingSms(username="", api_key=SecretStr(SECRET))
    with pytest.raises(ValueError, match="username and API key"):
        AfricasTalkingSms(username="finvest", api_key=SecretStr(""))
