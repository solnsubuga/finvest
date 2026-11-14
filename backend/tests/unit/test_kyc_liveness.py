"""Liveness rules, and the ioTec liveness adapter against a fake ioTec."""

import base64
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from app.modules.kyc.domain.checks import CheckStatus, DocumentType, PhotoKind
from app.modules.kyc.domain.liveness import (
    MAX_ATTEMPTS,
    OPEN_FOR,
    Live,
    LivenessStatus,
    NotLive,
    NotStarted,
    Started,
    StillOpen,
    selfie_editable,
    shown_status,
    start_problem,
)
from app.modules.kyc.domain.provider import (
    AutomaticCheck,
    CheckOutcome,
    ProviderDeferred,
    ProviderUncertain,
    Verdict,
    decide,
)
from app.modules.kyc.iotec_liveness import IotecLivenessProvider
from app.modules.kyc.providers import DevelopmentStubLiveness
from app.modules.kyc.settings import KycSettings

REQUEST = "44444444-4444-4444-8444-444444444444"
PAGE = "https://faces.iotec.io/auth?token=abc"
IMAGE = "https://media.iotec.io/faces/1.jpg"
JPEG = b"\xff\xd8\xff\xe0 face"
NOW = datetime(2026, 11, 14, 9, tzinfo=UTC)
KEY = "-Pattempt00000000001"

Handler = Callable[[httpx.Request], httpx.Response]


# The rules ----------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [LivenessStatus.STARTING, LivenessStatus.OPEN])
def test_a_waiting_attempt_expires_after_its_time(status: LivenessStatus) -> None:
    assert shown_status(status, NOW, NOW + OPEN_FOR - timedelta(seconds=1)) is status
    assert shown_status(status, NOW, NOW + OPEN_FOR) is LivenessStatus.EXPIRED


@pytest.mark.parametrize(
    "status", [LivenessStatus.PASSED, LivenessStatus.FAILED, LivenessStatus.NOT_STARTED]
)
def test_a_finished_attempt_keeps_its_status_forever(status: LivenessStatus) -> None:
    assert shown_status(status, NOW, NOW + timedelta(days=400)) is status


@pytest.mark.parametrize("status", list(CheckStatus))
def test_the_selfie_is_editable_in_a_draft_or_when_staff_asked_for_it(status: CheckStatus) -> None:
    asked = frozenset({PhotoKind.SELFIE})
    assert selfie_editable(status, asked) == (
        status in (CheckStatus.DRAFT, CheckStatus.AWAITING_CLIENT)
    )
    assert selfie_editable(status, frozenset({PhotoKind.BACK})) == (status is CheckStatus.DRAFT)


def test_starting_needs_an_editable_selfie_and_attempts_left() -> None:
    draft = CheckStatus.DRAFT
    none: frozenset[PhotoKind] = frozenset()
    for document in DocumentType:
        assert start_problem(draft, document, none, attempts=0) is None
    assert start_problem(draft, DocumentType.NATIONAL_ID, none, attempts=MAX_ATTEMPTS - 1) is None
    assert (
        start_problem(draft, DocumentType.NATIONAL_ID, none, attempts=MAX_ATTEMPTS)
        == "too_many_attempts"
    )
    assert (
        start_problem(CheckStatus.NEEDS_REVIEW, DocumentType.NATIONAL_ID, none, attempts=0)
        == "selfie_not_editable"
    )


def test_automatic_approval_needs_a_passed_liveness_check() -> None:
    others = tuple(
        CheckOutcome(check, Verdict.PASS)
        for check in AutomaticCheck
        if check is not AutomaticCheck.LIVENESS
    )
    assert decide(others, automatic_approval=True).flags == ("liveness_not_run",)
    failed = (*others, CheckOutcome(AutomaticCheck.LIVENESS, Verdict.FAIL))
    assert decide(failed, automatic_approval=True).flags == ("liveness_fail",)
    live = (*others, CheckOutcome(AutomaticCheck.LIVENESS, Verdict.PASS))
    assert decide(live, automatic_approval=True).approve


# The ioTec adapter ----------------------------------------------------------------------------


class FakeIotec:
    def __init__(self) -> None:
        self.sent: list[httpx.Request] = []
        self.start: Handler = lambda _: httpx.Response(
            200, json={"id": REQUEST, "status": "Pending", "verificationUrl": PAGE}
        )
        self.answer: dict[str, object] = {"status": "Pending", "faceVerificationStatus": "Pending"}
        self.images: dict[str, Handler] = {IMAGE: lambda _: httpx.Response(200, content=JPEG)}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        url = str(request.url)
        if url in self.images:
            return self.images[url](request)
        assert request.headers["Client-Id"] == "client-1"
        assert request.headers["X-Api-Key"] == "IO-key"
        if request.method == "POST":
            assert request.url.path == "/api/face-verification"
            return self.start(request)
        assert request.url.path == f"/api/face-verification/{REQUEST}"
        return httpx.Response(200, json=self.answer)


def adapter(fake: FakeIotec) -> IotecLivenessProvider:
    return IotecLivenessProvider(
        client_id="client-1",
        api_key=SecretStr("IO-key"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
    )


def raises(error: type[httpx.RequestError]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error("no", request=request)

    return handler


async def test_a_start_sends_no_personal_details_and_returns_the_page() -> None:
    fake = FakeIotec()
    assert await adapter(fake).start(KEY) == Started(REQUEST, PAGE)
    body = json.loads(fake.sent[0].content)
    # No email, phone or name: the client opens the page in the app.
    assert body == {"referenceId": KEY, "isBlocking": False, "narration": "Finvest identity check"}


@pytest.mark.parametrize(
    ("handler", "error"),
    [
        (raises(httpx.ConnectError), ProviderDeferred),
        (raises(httpx.ConnectTimeout), ProviderDeferred),
        (lambda _: httpx.Response(401), ProviderDeferred),
        (lambda _: httpx.Response(429), ProviderDeferred),
        (raises(httpx.ReadTimeout), ProviderUncertain),
        (lambda _: httpx.Response(502), ProviderUncertain),
        (lambda _: httpx.Response(200, json={"status": "Pending"}), ProviderUncertain),
    ],
)
async def test_a_start_that_did_not_or_may_not_have_arrived(
    handler: Handler, error: type[Exception]
) -> None:
    fake = FakeIotec()
    fake.start = handler
    with pytest.raises(error):
        await adapter(fake).start(KEY)


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (httpx.Response(400, json={"title": "bad"}), "iotec_refused"),
        (httpx.Response(200, json={"id": REQUEST}), "iotec_no_page"),
        (
            httpx.Response(200, json={"id": REQUEST, "verificationUrl": "http://faces.iotec.io/a"}),
            "iotec_no_page",
        ),
        (
            httpx.Response(200, json={"id": REQUEST, "verificationUrl": "https://evil.example/a"}),
            "iotec_no_page",
        ),
        (
            httpx.Response(200, json={"id": REQUEST, "verificationUrl": "https://iotec.io.evil/a"}),
            "iotec_no_page",
        ),
    ],
)
async def test_a_client_is_only_ever_sent_to_an_iotec_page(
    answer: httpx.Response, code: str
) -> None:
    fake = FakeIotec()
    fake.start = lambda _: answer
    assert await adapter(fake).start(KEY) == NotStarted(code)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"status": "Pending", "faceVerificationStatus": "Pending"}, StillOpen()),
        ({"status": "Processing", "faceVerificationStatus": "Processing"}, StillOpen()),
        ({"status": "Pending"}, StillOpen()),
        ({"status": "Completed", "faceVerificationStatus": "Failed"}, NotLive("not_live")),
        ({"status": "Expired", "faceVerificationStatus": "Pending"}, NotLive("iotec_ended")),
        ({"status": "Cancelled"}, NotLive("iotec_ended")),
        ({"status": "Completed", "faceVerificationStatus": "Maybe"}, NotLive("iotec_unclear")),
    ],
)
async def test_an_answer_short_of_verified_is_never_live(
    answer: dict[str, object], expected: object
) -> None:
    fake = FakeIotec()
    fake.answer = answer
    assert await adapter(fake).result(REQUEST) == expected


async def test_a_verified_answer_brings_the_captured_face_without_sending_the_key_away() -> None:
    fake = FakeIotec()
    fake.answer = {
        "status": "Completed",
        "faceVerificationStatus": "Verified",
        "profileImageUrl": IMAGE,
    }
    assert await adapter(fake).result(REQUEST) == Live(JPEG, "image/jpeg")
    download = fake.sent[-1]
    assert str(download.url) == IMAGE
    # The image is on another host, so the API key is not sent with it.
    assert "X-Api-Key" not in download.headers
    assert "Client-Id" not in download.headers


@pytest.mark.parametrize(
    "address",
    [None, 7, "http://media.iotec.io/x.jpg", "https://evil.example/x.jpg", "file:///etc/passwd"],
)
async def test_a_capture_from_anywhere_but_iotec_is_not_fetched(address: object) -> None:
    fake = FakeIotec()
    fake.answer = {"faceVerificationStatus": "Verified", "profileImageUrl": address}
    assert await adapter(fake).result(REQUEST) == NotLive("no_capture")
    assert len(fake.sent) == 1


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        (httpx.Response(200, content=b"<html>not an image</html>"), NotLive("no_capture")),
        (httpx.Response(200, content=b""), NotLive("no_capture")),
        (
            httpx.Response(200, content=b"\x89PNG\r\n\x1a\n face"),
            Live(b"\x89PNG\r\n\x1a\n face", "image/png"),
        ),
    ],
)
async def test_the_captures_type_is_read_from_its_bytes(
    image: httpx.Response, expected: object
) -> None:
    fake = FakeIotec()
    fake.answer = {"faceVerificationStatus": "Verified", "profileImageUrl": IMAGE}
    fake.images[IMAGE] = lambda _: image
    assert await adapter(fake).result(REQUEST) == expected


@pytest.mark.parametrize("image", [raises(httpx.ConnectError), lambda _: httpx.Response(404)])
async def test_a_capture_that_cannot_be_downloaded_now_is_read_again_later(image: Handler) -> None:
    fake = FakeIotec()
    fake.answer = {"faceVerificationStatus": "Verified", "profileImageUrl": IMAGE}
    fake.images[IMAGE] = image
    with pytest.raises(ProviderDeferred):
        await adapter(fake).result(REQUEST)


async def test_reading_while_iotec_is_down_is_deferred() -> None:
    fake = FakeIotec()
    fake.answer = {}
    for handler in (raises(httpx.ConnectError), lambda _: httpx.Response(500)):
        broken = IotecLivenessProvider(
            client_id="client-1",
            api_key=SecretStr("IO-key"),
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(ProviderDeferred):
            await broken.result(REQUEST)
    with pytest.raises(LookupError):
        await adapter(fake).result("not-an-id")


def test_the_adapter_needs_credentials_and_https() -> None:
    with pytest.raises(ValueError, match="client ID"):
        IotecLivenessProvider(client_id="", api_key=SecretStr("k"))
    with pytest.raises(ValueError, match="https"):
        IotecLivenessProvider(
            client_id="c", api_key=SecretStr("k"), base_url="http://verify-api.iotec.io"
        )


# Settings -------------------------------------------------------------------------------------


def settings(**values: object) -> KycSettings:
    # A fresh key every run: no key is written into the repository.
    key = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    return KycSettings.model_validate({"identity_data_key": key, **values})


def test_liveness_follows_the_identity_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("IDENTITY_PROVIDER", "IDENTITY_AUTOMATIC_APPROVAL", "ENVIRONMENT"):
        monkeypatch.delenv(f"FINVEST_{name}", raising=False)
    assert KycSettings.model_validate({}).liveness() is None
    assert isinstance(
        settings(identity_provider="development-stub").liveness(), DevelopmentStubLiveness
    )
    iotec = settings(
        identity_provider="iotec",
        iotec_verify_client_id="client-1",
        iotec_verify_api_key="IO-key",
    ).liveness()
    assert isinstance(iotec, IotecLivenessProvider)


async def test_the_development_stub_passes_with_a_stand_in_capture() -> None:
    stub = DevelopmentStubLiveness()
    started = await stub.start(KEY)
    assert isinstance(started, Started)
    result = await stub.result(started.request_id)
    assert isinstance(result, Live)
    assert result.content_type == "image/jpeg"
