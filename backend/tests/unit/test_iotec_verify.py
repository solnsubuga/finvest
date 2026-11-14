"""The shared ioTec Verify connection: JSON and form requests, sorted the same way."""

import httpx
import pytest
from pydantic import SecretStr

from app.infrastructure.iotec_verify import IotecVerifyClient, Sent, VerifyUnavailable

REQUEST_ID = "11111111-1111-4111-8111-111111111111"


def client(handler: httpx.MockTransport) -> IotecVerifyClient:
    return IotecVerifyClient(
        client_id="client-1",
        api_key=SecretStr("IO-key"),
        http=httpx.AsyncClient(transport=handler),
        retry_pause=0,
    )


async def test_a_form_carries_its_fields_and_files_and_the_two_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": REQUEST_ID.upper()})

    result = await client(httpx.MockTransport(handler)).send_form(
        "/api/face-match",
        {"ReferenceId": "check:1"},
        {"Photo1": ("selfie", b"one", "image/jpeg"), "Photo2": ("document", b"two", "image/png")},
    )
    assert (result.sent, result.request_id) == (Sent.ACCEPTED, REQUEST_ID)
    (request,) = seen
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert (request.headers["Client-Id"], request.headers["X-Api-Key"]) == ("client-1", "IO-key")
    assert b'name="ReferenceId"' in request.content and b"check:1" in request.content
    assert b"one" in request.content and b"two" in request.content


async def test_a_form_that_never_arrived_is_sent_once_more_and_never_after_it_may_have() -> None:
    tries: list[int] = []

    def once_down(request: httpx.Request) -> httpx.Response:
        tries.append(1)
        if len(tries) == 1:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json={"id": REQUEST_ID})

    verify = client(httpx.MockTransport(once_down))
    result = await verify.send_form("/api/face-match", {}, {"Photo1": ("a", b"a", "image/jpeg")})
    assert result.sent is Sent.ACCEPTED and len(tries) == 2

    tries.clear()

    def lost(request: httpx.Request) -> httpx.Response:
        tries.append(1)
        raise httpx.ReadTimeout("lost", request=request)

    lost_client = client(httpx.MockTransport(lost))
    result = await lost_client.send_form("/api/face-match", {}, {"Photo1": ("a", b"a", "x/y")})
    assert result.sent is Sent.UNCERTAIN and len(tries) == 1


async def test_a_read_that_fails_is_unavailable_without_quoting_the_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="NAKATO Amara CM9406")

    with pytest.raises(VerifyUnavailable) as raised:
        await client(httpx.MockTransport(handler)).read("/api/face-match/x")
    assert "NAKATO" not in str(raised.value)
