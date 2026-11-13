"""The Expo push transport sorts every answer into sent, retry, refused or uncertain."""

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.infrastructure.push import (
    ConsolePush,
    DisabledPush,
    ExpoPush,
    InvalidPush,
    PushDeferred,
    PushMessage,
    PushRejected,
    PushSettings,
    PushUncertain,
    check_token,
)
from app.modules.notifications.domain.templates import InvalidTemplate, render, render_sms

ONE = "ExponentPushToken[abcdefghij0123456789]"
TWO = "ExpoPushToken[zyxwvutsrq9876543210]"


def a_message(*tokens: str) -> PushMessage:
    return PushMessage(
        tokens=tokens or (ONE,),
        title="Payment received",
        body="We received UGX 150,000 from MTN Mobile Money.",
        data={"notification_id": "-Nabc0000000000000N1"},
    )


def expo(handler: httpx.MockTransport, token: str | None = None) -> ExpoPush:
    return ExpoPush(
        access_token=SecretStr(token) if token else None,
        client=httpx.AsyncClient(transport=handler),
    )


def answering(
    status: int, body: object | None = None, *, seen: list[httpx.Request] | None = None
) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

    return httpx.MockTransport(handle)


def raising(error: Exception) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        raise error

    return httpx.MockTransport(handle)


async def test_a_push_goes_to_every_device_in_one_request() -> None:
    seen: list[httpx.Request] = []
    transport = expo(
        answering(
            200, {"data": [{"status": "ok", "id": "a"}, {"status": "ok", "id": "b"}]}, seen=seen
        ),
        "secret-token",
    )
    result = await transport.send(a_message(ONE, TWO))
    assert (result.provider, result.delivered, result.dead_tokens) == ("expo", 2, ())
    [request] = seen
    assert request.url == "https://exp.host/--/api/v2/push/send"
    assert request.headers["Authorization"] == "Bearer secret-token"
    sent = request.read().decode()
    assert ONE in sent and TWO in sent and "Payment received" in sent


async def test_without_an_access_token_no_authorization_is_sent() -> None:
    seen: list[httpx.Request] = []
    await expo(answering(200, {"data": [{"status": "ok"}]}, seen=seen)).send(a_message())
    assert "Authorization" not in seen[0].headers


async def test_a_device_expo_no_longer_knows_is_reported() -> None:
    body = {
        "data": [
            {"status": "ok", "id": "a"},
            {
                "status": "error",
                "message": "not registered",
                "details": {"error": "DeviceNotRegistered"},
            },
        ]
    }
    result = await expo(answering(200, body)).send(a_message(ONE, TWO))
    assert (result.delivered, result.dead_tokens) == (1, (TWO,))


async def test_every_device_refusing_is_a_refusal() -> None:
    body = {"data": [{"status": "error", "details": {"error": "MessageTooBig"}}]}
    with pytest.raises(PushRejected, match="MessageTooBig"):
        await expo(answering(200, body)).send(a_message())


async def test_a_reason_expo_gives_is_kept_only_when_it_is_a_plain_name() -> None:
    body = {
        "data": [{"status": "error", "details": {"error": "<script>ExponentPushToken[x]</script>"}}]
    }
    with pytest.raises(PushRejected) as refused:
        await expo(answering(200, body)).send(a_message())
    assert "Unknown" in refused.value.safe_message
    assert "ExponentPushToken" not in refused.value.safe_message


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_expo_asking_to_wait_is_retried(status: int) -> None:
    with pytest.raises(PushDeferred):
        await expo(answering(status)).send(a_message())


@pytest.mark.parametrize("status", [401, 403])
async def test_a_bad_access_token_is_refused(status: int) -> None:
    with pytest.raises(PushRejected) as refused:
        await expo(answering(status)).send(a_message())
    assert refused.value.code == "push_auth_failed"


async def test_a_bad_request_is_refused() -> None:
    with pytest.raises(PushRejected):
        await expo(answering(400, {"errors": [{"code": "VALIDATION_ERROR"}]})).send(a_message())


async def test_a_request_that_never_left_is_retried() -> None:
    with pytest.raises(PushDeferred):
        await expo(raising(httpx.ConnectError("refused"))).send(a_message())


@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("cut")])
async def test_a_request_with_no_answer_may_have_arrived(error: Exception) -> None:
    with pytest.raises(PushUncertain):
        await expo(raising(error)).send(a_message())


@pytest.mark.parametrize(
    "body",
    [
        {"data": []},
        {"data": "ok"},
        {"errors": []},
        ["ok"],
        {"data": [{"status": "ok"}, {"status": "ok"}]},
    ],
)
async def test_an_answer_this_code_does_not_read_is_uncertain(body: object) -> None:
    with pytest.raises(PushUncertain):
        await expo(answering(200, body)).send(a_message())


async def test_the_disabled_transport_waits_and_the_console_prints(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(PushDeferred) as deferred:
        await DisabledPush().send(a_message())
    assert deferred.value.code == "push_not_configured"
    result = await ConsolePush().send(a_message())
    assert result.delivered == 1
    assert "Payment received" in capsys.readouterr().out


@pytest.mark.parametrize(
    "token",
    [
        "",
        "ExponentPushToken[]",
        "ExponentPushToken[short]",
        "FCM:abc",
        "ExponentPushToken[a b c d e f g h]",
    ],
)
def test_a_token_is_checked(token: str) -> None:
    with pytest.raises(InvalidPush):
        check_token(token)


def test_a_message_is_checked() -> None:
    with pytest.raises(InvalidPush):
        PushMessage(tokens=(), title="t", body="b")
    with pytest.raises(InvalidPush):
        PushMessage(tokens=(ONE,), title=" ", body="b")
    with pytest.raises(InvalidPush):
        PushMessage(tokens=(ONE,), title="t", body="b" * 2001)
    with pytest.raises(InvalidPush):
        PushMessage(tokens=tuple([ONE] * 101), title="t", body="b")


def test_a_message_does_not_show_its_words_or_tokens() -> None:
    shown = repr(a_message())
    assert ONE not in shown and "Payment" not in shown


def test_settings_choose_the_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINVEST_ENVIRONMENT", raising=False)
    assert isinstance(PushSettings().transport(), DisabledPush)
    monkeypatch.setenv("FINVEST_PUSH_BACKEND", "expo")
    assert isinstance(PushSettings().transport(), ExpoPush)
    monkeypatch.setenv("FINVEST_PUSH_BACKEND", "local-console")
    assert isinstance(PushSettings().transport(), ConsolePush)
    monkeypatch.setenv("FINVEST_ENVIRONMENT", "production")
    with pytest.raises(ValidationError):
        PushSettings()
    monkeypatch.setenv("FINVEST_PUSH_BACKEND", "expo")
    monkeypatch.setenv("FINVEST_PUSH_EXPO_URL", "http://exp.host")
    with pytest.raises(ValidationError):
        PushSettings()


def test_a_client_notice_is_sent_in_its_own_words() -> None:
    mail = render(
        "client_notice", {"title": "Payment received", "body": "We received <b>UGX 150,000</b>."}
    )
    assert mail.subject == "Payment received"
    assert "We received <b>UGX 150,000</b>." in mail.text
    assert "&lt;b&gt;" in mail.html
    assert (
        render_sms("client_notice", {"sms": "Finvest: we received {amount}."})
        == "Finvest: we received {amount}."
    )
    with pytest.raises(InvalidTemplate):
        render_sms("client_notice", {"title": "x", "body": "y"})
    with pytest.raises(InvalidTemplate):
        render("client_notice", {"sms": "x"})
