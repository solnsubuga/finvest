"""Resend's delivery events: the Svix signature, reading an event, and the environment tag."""

import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.infrastructure.email import EmailMessage, EmailSettings, InvalidEmail, ResendTransport
from app.infrastructure.email.message import Sender
from app.infrastructure.email.webhooks import (
    InvalidSignature,
    ResendHookSettings,
    WebhookVerifier,
)
from app.modules.notifications.domain.email_events import (
    UnreadableEvent,
    address_hash,
    moves_forward,
    read_event,
)

SECRET = "whsec_" + base64.b64encode(b"a test secret of thirty-two byte").decode()
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
BODY = b'{"type":"email.delivered"}'


def signed(verifier: WebhookVerifier, *, at: datetime = NOW, body: bytes = BODY) -> dict[str, str]:
    stamp = int(at.timestamp())
    return {
        "svix-id": "msg_2KWPBgLlAfxdpx2AI54pPJ85f4W",
        "svix-timestamp": str(stamp),
        "svix-signature": verifier.sign("msg_2KWPBgLlAfxdpx2AI54pPJ85f4W", stamp, body),
    }


def test_the_published_svix_example_verifies() -> None:
    # The example from Svix's own libraries, so this is checked against more than ourselves.
    verifier = WebhookVerifier(SecretStr("whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"))
    headers = {
        "svix-id": "msg_p5jXN8AQM9LWM0D4loKWxJek",
        "svix-timestamp": "1614265330",
        "svix-signature": "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=",
    }
    body = b'{"test": 2432232314}'
    at = datetime.fromtimestamp(1614265330, UTC)
    assert verifier.verify(headers, body, at) == "msg_p5jXN8AQM9LWM0D4loKWxJek"


def test_a_signed_request_gives_its_event_id() -> None:
    verifier = WebhookVerifier(SecretStr(SECRET))
    assert verifier.verify(signed(verifier), BODY, NOW) == "msg_2KWPBgLlAfxdpx2AI54pPJ85f4W"


def test_any_one_of_several_signatures_is_enough() -> None:
    # While a secret is rotated, the router signs with the old and the new one.
    verifier = WebhookVerifier(SecretStr(SECRET))
    headers = signed(verifier)
    headers["svix-signature"] = f"v1,bm90IGl0 v2,whatever {headers['svix-signature']}"
    assert verifier.verify(headers, BODY, NOW)


@pytest.mark.parametrize(
    "change",
    [
        {"svix-signature": "v1,bm90IHRoZSByaWdodCBzaWduYXR1cmU="},
        {"svix-signature": ""},
        {"svix-id": "msg_another"},
        {"svix-id": "msg with spaces"},
        {"svix-id": ""},
        {"svix-timestamp": "not a number"},
        {"svix-timestamp": str(int(NOW.timestamp()) + 1)},
    ],
)
def test_anything_that_does_not_match_is_refused(change: dict[str, str]) -> None:
    verifier = WebhookVerifier(SecretStr(SECRET))
    with pytest.raises(InvalidSignature):
        verifier.verify({**signed(verifier), **change}, BODY, NOW)


def test_a_changed_body_or_another_secret_is_refused() -> None:
    verifier = WebhookVerifier(SecretStr(SECRET))
    with pytest.raises(InvalidSignature):
        verifier.verify(signed(verifier), BODY + b" ", NOW)
    other = WebhookVerifier(SecretStr("whsec_" + base64.b64encode(b"x" * 32).decode()))
    with pytest.raises(InvalidSignature):
        other.verify(signed(verifier), BODY, NOW)


@pytest.mark.parametrize("skew", [timedelta(minutes=-6), timedelta(minutes=6)])
def test_an_old_or_future_signature_is_refused(skew: timedelta) -> None:
    verifier = WebhookVerifier(SecretStr(SECRET))
    with pytest.raises(InvalidSignature):
        verifier.verify(signed(verifier, at=NOW + skew), BODY, NOW)
    assert verifier.verify(signed(verifier, at=NOW + skew / 2), BODY, NOW)


def test_the_hook_secret_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEND_HOOK_SECRET", raising=False)
    assert ResendHookSettings().verifier() is None
    monkeypatch.setenv("RESEND_HOOK_SECRET", "")
    assert ResendHookSettings().verifier() is None
    for bad in ("re_dashboard_secret", "whsec_not*base64", "whsec_c2hvcnQ="):
        monkeypatch.setenv("RESEND_HOOK_SECRET", bad)
        with pytest.raises(ValidationError) as raised:
            ResendHookSettings()
        assert bad not in str(raised.value)
    monkeypatch.setenv("RESEND_HOOK_SECRET", SECRET)
    settings = ResendHookSettings()
    assert SECRET not in repr(settings)
    assert isinstance(settings.verifier(), WebhookVerifier)


def event(kind: str = "email.bounced", **data: object) -> bytes:
    return json.dumps(
        {
            "type": kind,
            "created_at": "2026-11-13T11:59:58.123Z",
            "data": {
                "email_id": "4ef9a417-02e9-4d39-ad75-9611e0fcc33c",
                "from": "Finvest <no-reply@finvestug.com>",
                "to": ["Ada@Example.com"],
                "subject": "Your Finvest sign-in code",
                "tags": {"fidiba_environment": "app_finvest_prod"},
                **data,
            },
        }
    ).encode()


def test_a_bounce_is_read_without_keeping_the_address() -> None:
    found = read_event(event(bounce={"type": "Permanent", "subType": "General", "message": "x"}))
    assert found is not None
    assert found.delivery == "bounced"
    assert found.email_id == "4ef9a417-02e9-4d39-ad75-9611e0fcc33c"
    assert found.occurred_at == datetime(2026, 11, 13, 11, 59, 58, 123000, tzinfo=UTC)
    assert found.recipients == (address_hash("ada@example.com"),)
    assert "ada" not in repr(found).lower()
    assert found.suppresses


@pytest.mark.parametrize(
    ("kind", "data", "delivery", "suppresses"),
    [
        ("email.sent", {}, "accepted", False),
        ("email.delivery_delayed", {}, "delayed", False),
        ("email.delivered", {}, "delivered", False),
        ("email.failed", {"failed": {"reason": "x"}}, "failed", False),
        ("email.bounced", {"bounce": {"type": "Transient"}}, "bounced", False),
        ("email.bounced", {}, "bounced", True),
        ("email.complained", {}, "complained", True),
    ],
)
def test_each_event_type(
    kind: str, data: dict[str, object], delivery: str, suppresses: bool
) -> None:
    found = read_event(event(kind, **data))
    assert found is not None
    assert (found.delivery, found.suppresses) == (delivery, suppresses)


@pytest.mark.parametrize("kind", ["email.opened", "email.clicked", "contact.created", "x"])
def test_events_we_do_not_act_on_are_ignored(kind: str) -> None:
    assert read_event(event(kind)) is None


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"\xff\xfe",
        b"[]",
        b'{"data": {}}',
        b'{"type": "email.bounced"}',
        b'{"type": "email.bounced", "data": {"email_id": "bad id"}}',
        b'{"type": "email.bounced", "data": {"email_id": "a", "to": {"x": 1}}}',
    ],
)
def test_an_unreadable_event_is_refused(body: bytes) -> None:
    with pytest.raises(UnreadableEvent):
        read_event(body)


def test_a_missing_or_odd_time_is_left_empty() -> None:
    body = json.loads(event("email.delivered"))
    body["created_at"] = "yesterday"
    found = read_event(json.dumps(body).encode())
    assert found is not None
    assert found.occurred_at is None


def test_delivery_only_moves_forward() -> None:
    assert moves_forward(None, "accepted")
    assert moves_forward("accepted", "delivered")
    assert moves_forward("delivered", "complained")
    assert not moves_forward("delivered", "accepted")
    assert not moves_forward("delivered", "delayed")
    assert not moves_forward("bounced", "failed")
    assert not moves_forward("complained", "bounced")


def message(**tags: str) -> EmailMessage:
    return EmailMessage(
        to="ada@example.com",
        subject="Your Finvest sign-in code",
        html="<p>123456</p>",
        text="123456",
        idempotency_key="sign-in-code:abc",
        tags=tags,
    )


def transport(tags: dict[str, str] | None) -> ResendTransport:
    return ResendTransport(
        api_key=SecretStr("re_test_not_a_real_key"),
        sender=Sender.parse("Finvest <no-reply@finvestug.com>"),
        tags=tags,
    )


def test_every_resend_email_carries_the_environment_tag() -> None:
    sent = transport({"fidiba_environment": "app_finvest_prod"}).payload(message(template="x"))
    assert sent["tags"] == [
        {"name": "template", "value": "x"},
        {"name": "fidiba_environment", "value": "app_finvest_prod"},
    ]
    # A message cannot claim another environment.
    sent = transport({"fidiba_environment": "app_finvest_prod"}).payload(
        message(fidiba_environment="app_other")
    )
    assert sent["tags"] == [{"name": "fidiba_environment", "value": "app_finvest_prod"}]


def test_a_bad_or_one_too_many_tag_is_refused() -> None:
    with pytest.raises(ValueError, match="tag"):
        transport({"fidiba_environment": "app finvest"})
    full = transport({"fidiba_environment": "app_finvest_prod"})
    with pytest.raises(InvalidEmail):
        full.payload(message(a="1", b="2", c="3", d="4", e="5"))


def test_the_environment_tag_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINVEST_EMAIL_BACKEND", "resend")
    monkeypatch.setenv("FINVEST_EMAIL_FROM", "no-reply@finvestug.com")
    monkeypatch.setenv("FINVEST_EMAIL_RESEND_API_KEY", "re_test_not_a_real_key")
    monkeypatch.setenv("FINVEST_EMAIL_FIDIBA_ENVIRONMENT", "")
    plain = EmailSettings().transport()
    assert isinstance(plain, ResendTransport)
    assert plain.tags == {}
    monkeypatch.setenv("FINVEST_EMAIL_FIDIBA_ENVIRONMENT", "app_finvest_prod")
    tagged = EmailSettings().transport()
    assert isinstance(tagged, ResendTransport)
    assert tagged.tags == {"fidiba_environment": "app_finvest_prod"}
    monkeypatch.setenv("FINVEST_EMAIL_FIDIBA_ENVIRONMENT", "app finvest")
    with pytest.raises(ValidationError):
        EmailSettings()


async def test_the_tag_reaches_resend() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "re_1"})

    tagged = ResendTransport(
        api_key=SecretStr("re_test_not_a_real_key"),
        sender=Sender.parse("Finvest <no-reply@finvestug.com>"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        tags={"fidiba_environment": "app_finvest_prod"},
    )
    await tagged.send(message())
    assert json.loads(seen[0].content)["tags"] == [
        {"name": "fidiba_environment", "value": "app_finvest_prod"}
    ]
