"""The ioTec Verify liveness adapter. See docs/identity-checks.md#liveness.

ioTec Verify's liveness detection (`/api/face-verification`) runs on ioTec Faces, a page of its
own. Creating a request returns that page's address, which the client app opens. ioTec can also
email the link to the client, but no email address is sent: the app opens the page itself, and
ioTec needs nothing about the client to run it. The attempt's ID is the `referenceId`.

When ioTec has seen a live person, the worker downloads the face it captured
(`profileImageUrl`), and that becomes the check's selfie. The address is only followed over
https to an ioTec host, so a changed answer cannot send the worker anywhere else.

Nothing here is logged, and no error quotes what ioTec sent back.
"""

import re
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from app.modules.kyc.domain.checks import photo_matches_type
from app.modules.kyc.domain.liveness import Live, NotLive, NotStarted, Started, StillOpen
from app.modules.kyc.domain.provider import ProviderDeferred, ProviderUncertain
from app.modules.kyc.iotec import BASE_URL

# ioTec's own hosts and their subdomains. The client is only sent to a page on one of them, and
# the captured face is only downloaded from one.
HOSTS = ("iotec.io",)
# The same limit as a photo the client sends.
MAX_IMAGE_BYTES = 10 * 1024 * 1024
NARRATION = "Finvest identity check"

_REQUEST_ID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
_WAITING = frozenset({"Pending", "Processing"})
# A request ioTec gave up on. It never turns into an answer, so it is not live.
_ENDED = frozenset({"Failed", "Expired", "Cancelled"})
_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")


class IotecLivenessProvider:
    name = "iotec-faces"

    def __init__(
        self,
        *,
        client_id: str,
        api_key: SecretStr,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
        hosts: tuple[str, ...] = HOSTS,
    ) -> None:
        if not client_id or not api_key.get_secret_value():
            raise ValueError("ioTec Verify needs a client ID and an API key")
        if not base_url.startswith("https://"):
            raise ValueError("ioTec Verify is only reached over https")
        self._client_id = client_id
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._api_host = urlsplit(self._base_url).hostname
        self._timeout = timeout
        self._client = client
        self._hosts = hosts

    async def start(self, key: str) -> Started | NotStarted:
        body = {"referenceId": key, "isBlocking": False, "narration": NARRATION}
        try:
            response = await self._request("POST", self._base_url + "/api/face-verification", body)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise ProviderDeferred("ioTec Verify did not receive the request") from None
        except httpx.HTTPError:
            raise ProviderUncertain("ioTec Verify may hold this request") from None
        status = response.status_code
        if status in (401, 403, 429):
            raise ProviderDeferred(f"ioTec Verify refused the request with {status}")
        if status >= 500 or status == 408:
            raise ProviderUncertain(f"ioTec Verify answered {status}")
        if not 200 <= status < 300:
            return NotStarted("iotec_refused")
        answer = _json(response)
        request_id = None if answer is None else _request_id(answer.get("id"))
        url = None if answer is None else answer.get("verificationUrl")
        if request_id is None:
            # It took the request, but there is nothing to read it back by.
            raise ProviderUncertain("ioTec Verify answered without a request ID")
        if not isinstance(url, str) or not self._iotec_host(url):
            # Paid for, perhaps, but not a page to send a client to.
            return NotStarted("iotec_no_page")
        return Started(request_id=request_id, url=url)

    async def result(self, request_id: str) -> StillOpen | Live | NotLive:
        if _request_id(request_id) is None:
            raise LookupError("Not an ioTec Verify request ID")
        try:
            response = await self._request(
                "GET", f"{self._base_url}/api/face-verification/{request_id}"
            )
        except httpx.HTTPError:
            raise ProviderDeferred("ioTec Verify is unreachable") from None
        if response.status_code != 200:
            raise ProviderDeferred(f"ioTec Verify answered {response.status_code}")
        answer = _json(response)
        if answer is None:
            raise ProviderDeferred("ioTec Verify answered with something that is not JSON")
        verdict = answer.get("faceVerificationStatus")
        if verdict == "Verified":
            return await self._capture(answer.get("profileImageUrl"))
        if verdict == "Failed":
            return NotLive("not_live")
        if answer.get("status") in _ENDED:
            return NotLive("iotec_ended")
        if verdict in _WAITING or answer.get("status") in _WAITING or verdict is None:
            return StillOpen()
        # A value this adapter does not know is never a pass.
        return NotLive("iotec_unclear")

    async def _capture(self, address: object) -> Live | NotLive:
        if not isinstance(address, str) or not self._iotec_host(address):
            # Live, but nothing we can make the selfie from: the client tries again.
            return NotLive("no_capture")
        try:
            response = await self._request("GET", address, auth=self._same_host(address))
        except httpx.HTTPError:
            raise ProviderDeferred("The captured face could not be downloaded") from None
        if response.status_code != 200:
            raise ProviderDeferred("The captured face could not be downloaded")
        data = response.content
        if not data or len(data) > MAX_IMAGE_BYTES:
            return NotLive("no_capture")
        # The type is read from the file itself, not from what the server says it is.
        for content_type in _IMAGE_TYPES:
            if photo_matches_type(content_type, data):
                return Live(photo=data, content_type=content_type)
        return NotLive("no_capture")

    def _iotec_host(self, address: str) -> bool:
        if not _https(address):
            return False
        host = (urlsplit(address).hostname or "").lower()
        return any(host == allowed or host.endswith("." + allowed) for allowed in self._hosts)

    def _same_host(self, address: str) -> bool:
        # The key is only sent to the API's own host, never to wherever an image is kept.
        return urlsplit(address).hostname == self._api_host

    async def _request(
        self,
        method: str,
        url: str,
        json: Mapping[str, object] | None = None,
        *,
        auth: bool = True,
    ) -> httpx.Response:
        headers = {"User-Agent": "finvest"}
        if auth:
            headers["Client-Id"] = self._client_id
            headers["X-Api-Key"] = self._api_key.get_secret_value()
        if self._client is not None:
            return await self._client.request(
                method, url, headers=headers, json=json, timeout=self._timeout
            )
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
            return await client.request(method, url, headers=headers, json=json)


def _https(address: str) -> bool:
    parts = urlsplit(address)
    return parts.scheme == "https" and bool(parts.hostname)


def _json(response: httpx.Response) -> Mapping[str, object] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _request_id(value: object) -> str | None:
    return value.lower() if isinstance(value, str) and _REQUEST_ID.fullmatch(value) else None
