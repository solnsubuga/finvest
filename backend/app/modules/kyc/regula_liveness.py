"""Regula's liveness check, the first step of the guided selfie.

See docs/identity-checks.md#regula-liveness. The owner's choice (2026-11-14): Regula's liveness
plus the guided poses. Regula's capture is the straight-on selfie, and the client then takes the
four turns with it. Regula's liveness runs in the app, against Regula, with Regula's own capture
and guidance; no page is opened. The attempt's ID is the session's tag, so `start` asks nothing
of Regula: the "page" it hands the app is `regula-liveness:<tag>`, which tells the app to run
Regula's liveness with that tag.

When the session ends the app sends its transaction ID (`liveness.report_transaction`). The
worker then reads `GET /api/v2/liveness?transactionId=` from Regula itself, never taking the
app's word: the session must carry the attempt's tag, and its status must be confirmed. The
captured face becomes the check's selfie, so the selfie match compares the live capture with
the ID. Regula saves a session's data a moment after it ends, so a transaction it does not
know yet is read again.

Nothing here is logged, and no error quotes what Regula sent back.
"""

import re
from collections.abc import Mapping
from urllib.parse import urljoin, urlsplit

import httpx

from app.modules.kyc.domain.checks import photo_matches_type
from app.modules.kyc.domain.liveness import (
    TRANSACTION_ID,
    TRANSACTION_SEPARATOR,
    Live,
    NotLive,
    NotStarted,
    Started,
    StillOpen,
)
from app.modules.kyc.domain.provider import ProviderDeferred

PAGE_PREFIX = "regula-liveness:"
# The same limit as a photo the client sends.
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_CONFIRMED, _NOT_CONFIRMED = 0, 1
_TAG = re.compile(r"[A-Za-z0-9_-]{1,127}")
_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")


class RegulaLivenessProvider:
    name = "regula"

    def __init__(
        self,
        *,
        face_url: str,
        capture_hosts: tuple[str, ...] = (),
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._face_url = face_url.rstrip("/")
        # The captured face is downloaded only over https from the Face service's own host,
        # or a host named in the settings, such as Regula's storage for the cloud.
        own = (urlsplit(self._face_url).hostname or "").lower()
        self._capture_hosts = frozenset({own, *(h.lower() for h in capture_hosts)} - {""})
        self._timeout = timeout
        self._client = client

    async def start(self, key: str) -> Started | NotStarted:
        if _TAG.fullmatch(key) is None:
            return NotStarted("regula_bad_tag")
        return Started(request_id=key, url=PAGE_PREFIX + key)

    async def result(self, request_id: str) -> StillOpen | Live | NotLive:
        tag, _, transaction = request_id.partition(TRANSACTION_SEPARATOR)
        if _TAG.fullmatch(tag) is None:
            raise LookupError("Not a Regula liveness tag")
        if not transaction:
            # The app has not finished Regula's session yet.
            return StillOpen()
        if TRANSACTION_ID.fullmatch(transaction) is None:
            return NotLive("regula_unclear")
        response = await self._get(
            f"{self._face_url}/api/v2/liveness", params={"transactionId": transaction}
        )
        if response.status_code == 404:
            # Saved a moment after the session ends: read again.
            return StillOpen()
        if response.status_code != 200:
            raise ProviderDeferred("Regula did not answer the liveness read")
        answer = _json(response)
        if answer is None:
            raise ProviderDeferred("Regula's liveness answer could not be read")
        if answer.get("tag") != tag:
            # Someone else's session, or one started for another attempt.
            return NotLive("regula_wrong_session")
        status = answer.get("status")
        if status == _NOT_CONFIRMED:
            return NotLive("not_live")
        if status != _CONFIRMED or isinstance(status, bool):
            # A value this adapter does not know is never a pass.
            return NotLive("regula_unclear")
        return await self._capture(answer.get("portrait"))

    async def _capture(self, link: object) -> Live | NotLive:
        if not isinstance(link, str) or not link:
            return NotLive("no_capture")
        address = urljoin(self._face_url + "/", link)
        parts = urlsplit(address)
        own = urlsplit(self._face_url)
        same_origin = (parts.scheme, parts.hostname) == (own.scheme, own.hostname)
        allowed = (parts.hostname or "").lower() in self._capture_hosts and (
            parts.scheme == "https" or same_origin
        )
        if not allowed:
            # Live, but nothing we may make the selfie from: the client tries again.
            return NotLive("no_capture")
        response = await self._get(address)
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

    async def _get(self, url: str, params: Mapping[str, str] | None = None) -> httpx.Response:
        try:
            if self._client is not None:
                return await self._client.get(url, params=params, timeout=self._timeout)
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
                return await client.get(url, params=params)
        except httpx.HTTPError:
            # Reading changes nothing at Regula, so the worker reads again later.
            raise ProviderDeferred("Regula could not be reached") from None


def _json(response: httpx.Response) -> Mapping[str, object] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None
