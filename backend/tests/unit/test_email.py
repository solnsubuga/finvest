import base64
import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.infrastructure.email import (
    Attachment,
    DisabledTransport,
    EmailDeferred,
    EmailMessage,
    EmailRejected,
    EmailSettings,
    InvalidEmail,
    ResendTransport,
    Sender,
)
from app.modules.notifications.domain.templates import InvalidTemplate, render

API_KEY = "re_test_not_a_real_key"
ADDRESS = "ada@example.com"


def message(**changes: object) -> EmailMessage:
    values: dict[str, object] = {
        "to": ADDRESS,
        "subject": "Your Finvest sign-in code",
        "html": "<p>123456</p>",
        "text": "123456",
        "idempotency_key": "sign-in-code:abc",
    }
    return EmailMessage(**{**values, **changes})  # type: ignore[arg-type]


def resend(handler: httpx.MockTransport) -> ResendTransport:
    return ResendTransport(
        api_key=SecretStr(API_KEY),
        sender=Sender.parse("Finvest <no-reply@finvestug.com>"),
        client=httpx.AsyncClient(transport=handler),
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"to": "not an address"},
        {"to": "ada@example.com\nBcc: x@example.com"},
        {"to": "ädä@example.com"},
        {"subject": "Hello\r\nBcc: x@example.com"},
        {"subject": ""},
        {"html": " "},
        {"text": ""},
        {"idempotency_key": "has spaces"},
        {"tags": {"bad tag": "x"}},
    ],
)
def test_a_message_that_could_inject_or_break_is_refused(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidEmail):
        message(**changes)


PDF = b"%PDF-1.7 statement"


@pytest.mark.parametrize(
    "file",
    [
        {"file_name": "statement.exe", "content": PDF},
        {"file_name": "../statement.pdf", "content": PDF},
        {"file_name": "a\nBcc.pdf", "content": PDF},
        {"file_name": " statement.pdf", "content": PDF},
        {"file_name": "statement.pdf", "content": b""},
        {"file_name": "statement.pdf", "content": PDF, "content_type": "text/html"},
    ],
)
def test_an_attachment_that_is_not_a_plain_pdf_is_refused(file: dict[str, object]) -> None:
    with pytest.raises(InvalidEmail):
        Attachment(**file)  # type: ignore[arg-type]


def test_a_message_takes_a_few_small_attachments_only() -> None:
    one = Attachment("Finvest statement October 2026.pdf", PDF)
    assert message(attachments=(one,)).attachments == (one,)
    with pytest.raises(InvalidEmail, match="too many"):
        message(attachments=(one,) * 4)
    with pytest.raises(InvalidEmail, match="too large"):
        message(attachments=(Attachment("big.pdf", b"x" * (10 * 1024 * 1024 + 1)),))
    assert "PDF-1.7" not in repr(one)


async def test_resend_gets_an_attachment_as_base64() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "re_124"})

    file = Attachment("Finvest receipt RC-1.pdf", PDF)
    await resend(httpx.MockTransport(handler)).send(message(attachments=(file,)))
    (sent,) = json.loads(seen[0].content)["attachments"]
    assert sent == {
        "filename": "Finvest receipt RC-1.pdf",
        "content": base64.b64encode(PDF).decode(),
        "content_type": "application/pdf",
    }


def test_a_message_does_not_show_its_content_when_printed() -> None:
    shown = repr(message())
    assert "123456" not in shown
    assert ADDRESS not in shown


async def test_resend_gets_the_message_and_the_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "re_123"})

    result = await resend(httpx.MockTransport(handler)).send(message(tags={"template": "x"}))
    assert (result.provider, result.provider_message_id) == ("resend", "re_123")
    request = seen[0]
    assert str(request.url) == "https://api.resend.com/emails"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["Idempotency-Key"] == "sign-in-code:abc"
    assert json.loads(request.content) == {
        "from": "Finvest <no-reply@finvestug.com>",
        "to": [ADDRESS],
        "subject": "Your Finvest sign-in code",
        "html": "<p>123456</p>",
        "text": "123456",
        "tags": [{"name": "template", "value": "x"}],
    }


@pytest.mark.parametrize(
    ("status", "kind", "code"),
    [
        (401, EmailRejected, "email_auth_failed"),
        (403, EmailRejected, "email_auth_failed"),
        (422, EmailRejected, "email_rejected"),
        (409, EmailDeferred, "email_deferred"),
        (429, EmailDeferred, "email_deferred"),
        (500, EmailDeferred, "email_deferred"),
        (503, EmailDeferred, "email_deferred"),
    ],
)
async def test_resend_failures_are_sorted_and_say_nothing_private(
    status: int, kind: type[Exception], code: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"name": "validation_error", "message": f"Bad address {ADDRESS} with {API_KEY}"}
        return httpx.Response(status, json=body)

    with pytest.raises(kind) as raised:
        await resend(httpx.MockTransport(handler)).send(message())
    error = raised.value
    assert error.code == code  # type: ignore[attr-defined]
    assert "validation_error" in str(error)
    assert ADDRESS not in str(error)
    assert API_KEY not in str(error)


async def test_a_network_failure_is_safe_to_retry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(EmailDeferred, match="ReadTimeout"):
        await resend(httpx.MockTransport(handler)).send(message())


async def test_with_no_provider_nothing_is_sent() -> None:
    with pytest.raises(EmailDeferred) as raised:
        await DisabledTransport().send(message())
    assert raised.value.code == "email_not_configured"


def test_email_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("BACKEND", "FROM", "RESEND_API_KEY"):
        monkeypatch.delenv(f"FINVEST_EMAIL_{name}", raising=False)
    assert isinstance(EmailSettings().transport(), DisabledTransport)
    monkeypatch.setenv("FINVEST_EMAIL_BACKEND", "resend")
    with pytest.raises(ValidationError):
        EmailSettings()
    monkeypatch.setenv("FINVEST_EMAIL_FROM", "no-reply@finvestug.com")
    monkeypatch.setenv("FINVEST_EMAIL_RESEND_API_KEY", "")
    with pytest.raises(ValidationError):
        EmailSettings()
    monkeypatch.setenv("FINVEST_EMAIL_RESEND_API_KEY", API_KEY)
    settings = EmailSettings()
    assert API_KEY not in repr(settings)
    transport = settings.transport()
    assert isinstance(transport, ResendTransport)
    assert transport.sender.header() == "Finvest <no-reply@finvestug.com>"


def test_a_sign_in_code_email() -> None:
    rendered = render("sign_in_code", {"code": "482913", "minutes": 10})
    assert rendered.subject == "Your Finvest sign-in code"
    assert "482913" in rendered.text
    assert "482913" in rendered.html
    assert "10 minutes" in rendered.text
    assert "never ask you for this code" in rendered.text


def test_the_enrolment_email_links_once_and_escapes_the_name() -> None:
    link = "https://admin.finvestug.com/enrol#abc"
    rendered = render(
        "staff_enrolment", {"name": '<script>alert("x")</script>', "link": link, "hours": 72}
    )
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert rendered.html.count(f'<a href="{link}">') == 1
    assert link in rendered.text


@pytest.mark.parametrize(
    ("template", "variables"),
    [
        ("unknown", {}),
        ("sign_in_code", {"code": "1"}),
        ("sign_in_code", {"code": "1", "minutes": 10, "extra": "x"}),
        ("sign_in_code", {"code": "", "minutes": 10}),
        ("sign_in_code", {"code": "9" * 2001, "minutes": 10}),
    ],
)
def test_a_template_refuses_the_wrong_variables(
    template: str, variables: dict[str, object]
) -> None:
    with pytest.raises(InvalidTemplate):
        render(template, variables)


def test_a_link_must_point_to_a_finvest_site() -> None:
    def enrolment(link: str) -> dict[str, object]:
        return {"name": "Ada", "link": link, "hours": 72}

    console = ["https://admin.finvestug.com"]
    good = "https://admin.finvestug.com/enrol#token"
    assert good in render("staff_enrolment", enrolment(good), link_origins=console).text
    for link in (
        "https://admin.finvestug.com.evil.example/enrol#token",
        "https://evil.example/enrol#token",
        "http://admin.finvestug.com/enrol#token",
        "https://admin.finvestug.com",
    ):
        with pytest.raises(InvalidTemplate, match="Finvest site"):
            render("staff_enrolment", enrolment(link), link_origins=console)
    with pytest.raises(InvalidTemplate):
        render("staff_enrolment", enrolment(good), link_origins=[])
