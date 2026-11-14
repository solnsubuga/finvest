"""Regula's liveness adapter against a fake Regula Face service."""

import json
from collections.abc import Callable

import httpx
import pytest

from app.modules.kyc.domain.liveness import Live, NotLive, NotStarted, Started, StillOpen
from app.modules.kyc.domain.provider import ProviderDeferred
from app.modules.kyc.regula_liveness import PAGE_PREFIX, RegulaLivenessProvider

FACE = "https://faceapi.example.com"
TAG = "0Kt9xQabcDEF123"
TRANSACTION = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
CAPTURE = b"\xff\xd8\xff a live face"


def adapter(
    answer: Callable[[httpx.Request], httpx.Response], *, hosts: tuple[str, ...] = ()
) -> tuple[RegulaLivenessProvider, list[httpx.Request]]:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return answer(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RegulaLivenessProvider(face_url=FACE, capture_hosts=hosts, client=client), sent


def regula(
    *, status: object = 0, tag: object = TAG, portrait: object = "/api/v2/liveness/portrait/1"
) -> Callable[[httpx.Request], httpx.Response]:
    """Answers the liveness read, and serves the capture at the portrait link."""

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/liveness":
            body = {"transactionId": TRANSACTION, "tag": tag, "status": status}
            if portrait is not None:
                body["portrait"] = portrait
            return httpx.Response(200, content=json.dumps(body))
        return httpx.Response(200, content=CAPTURE)

    return answer


def reported(transaction: str = TRANSACTION) -> str:
    return f"{TAG}|{transaction}"


async def test_start_asks_nothing_of_regula_and_hands_the_app_the_tag() -> None:
    provider, sent = adapter(regula())
    assert await provider.start(TAG) == Started(request_id=TAG, url=PAGE_PREFIX + TAG)
    assert await provider.start("not a tag!") == NotStarted("regula_bad_tag")
    assert sent == []


async def test_nothing_is_read_until_the_app_reports_its_session() -> None:
    provider, sent = adapter(regula())
    assert await provider.result(TAG) == StillOpen()
    assert sent == []
    with pytest.raises(LookupError):
        await provider.result("bad tag|" + TRANSACTION)
    assert await provider.result(reported("not-a-uuid")) == NotLive("regula_unclear")
    assert sent == []


async def test_a_confirmed_session_of_this_attempt_is_live_with_its_capture() -> None:
    provider, sent = adapter(regula())
    assert await provider.result(reported()) == Live(photo=CAPTURE, content_type="image/jpeg")
    assert sent[0].url.params["transactionId"] == TRANSACTION
    assert str(sent[1].url) == FACE + "/api/v2/liveness/portrait/1"


async def test_the_app_word_is_never_taken() -> None:
    # Another attempt's session, or one with no tag.
    for tag in ("someone-else", None, 7):
        provider, _ = adapter(regula(tag=tag))
        assert await provider.result(reported()) == NotLive("regula_wrong_session")
    provider, _ = adapter(regula(status=1))
    assert await provider.result(reported()) == NotLive("not_live")
    # A value the adapter does not know, or a bool posing as 0, is never a pass.
    for status in (2, None, "0", False):
        provider, _ = adapter(regula(status=status))
        assert await provider.result(reported()) == NotLive("regula_unclear")


async def test_a_session_regula_has_not_saved_yet_is_read_again() -> None:
    provider, _ = adapter(lambda _: httpx.Response(404))
    assert await provider.result(reported()) == StillOpen()


@pytest.mark.parametrize(
    "answer",
    [
        lambda _: httpx.Response(500),
        lambda _: httpx.Response(200, content=b"not json"),
        lambda _: httpx.Response(200, content=b"[]"),
    ],
)
async def test_an_answer_that_cannot_be_used_is_deferred(
    answer: Callable[[httpx.Request], httpx.Response],
) -> None:
    provider, _ = adapter(answer)
    with pytest.raises(ProviderDeferred):
        await provider.result(reported())


async def test_regula_out_of_reach_is_deferred() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    provider, _ = adapter(down)
    with pytest.raises(ProviderDeferred):
        await provider.result(reported())


async def test_the_capture_comes_only_from_allowed_hosts_over_https() -> None:
    for link in (
        None,
        "",
        "https://elsewhere.example.com/p.jpg",
        "http://storage.example.com/p.jpg",
        "file:///etc/passwd",
    ):
        provider, sent = adapter(regula(portrait=link), hosts=("storage.example.com",))
        assert await provider.result(reported()) == NotLive("no_capture")
        assert len(sent) == 1
    provider, sent = adapter(
        regula(portrait="https://storage.example.com/p.jpg"), hosts=("Storage.Example.com",)
    )
    assert isinstance(await provider.result(reported()), Live)
    assert sent[1].url.host == "storage.example.com"


async def test_the_capture_must_be_an_image_of_a_sensible_size() -> None:
    for content in (b"", b"<html>not a face</html>", b"\xff\xd8\xff" + b"0" * (10 * 1024 * 1024)):

        def answer(request: httpx.Request, content: bytes = content) -> httpx.Response:
            if request.url.path == "/api/v2/liveness":
                return regula()(request)
            return httpx.Response(200, content=content)

        provider, _ = adapter(answer)
        assert await provider.result(reported()) == NotLive("no_capture")


async def test_a_capture_that_cannot_be_downloaded_is_read_again() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/liveness":
            return regula()(request)
        return httpx.Response(503)

    provider, _ = adapter(answer)
    with pytest.raises(ProviderDeferred):
        await provider.result(reported())
