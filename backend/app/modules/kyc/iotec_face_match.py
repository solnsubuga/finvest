"""The ioTec Verify face match for face confirmations. See docs/identity-checks.md.

The same `/api/face-match` request the hand-in sends, with the live capture as the first photo
and the selfie of the approved check as the second. It is sent in the background
(`isBlocking: false`) with the attempt's ID as its `referenceId`, and read back by the request
ID ioTec gives it. Nothing here is logged, and no error quotes what ioTec sent back.
"""

import httpx
from pydantic import SecretStr

from app.infrastructure import iotec_verify
from app.infrastructure.iotec_verify import IotecVerifyClient, Sent, VerifyUnavailable
from app.infrastructure.storage import FileStorage, StorageError
from app.modules.kyc.domain.face_confirmations import (
    MatchAnswer,
    MatchNotRun,
    MatchPending,
    MatchRefused,
    MatchSent,
)
from app.modules.kyc.domain.provider import PhotoRef, ProviderDeferred, ProviderUncertain
from app.modules.kyc.iotec import face_match_reading

_WAITING = frozenset({"Pending", "Processing"})


class IotecFaceMatcher:
    name = "iotec-verify"

    def __init__(
        self,
        *,
        client_id: str,
        api_key: SecretStr,
        storage: FileStorage,
        base_url: str = iotec_verify.BASE_URL,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
        retry_pause: float = 1.0,
    ) -> None:
        self._verify = IotecVerifyClient(
            client_id=client_id,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            http=client,
            retry_pause=retry_pause,
        )
        self._storage = storage

    async def send(self, key: str, live: PhotoRef, on_file: PhotoRef) -> MatchSent | MatchRefused:
        # Both photos are read first, so a storage problem sends nothing at all.
        live_bytes = await self._photo(live)
        on_file_bytes = await self._photo(on_file)
        result = await self._verify.send_form(
            "/api/face-match",
            {"ReferenceId": key, "IsBlocking": "false"},
            {
                "Photo1": ("live", live_bytes, live.content_type),
                "Photo2": ("on-file", on_file_bytes, on_file.content_type),
            },
        )
        if result.sent is Sent.ACCEPTED and result.request_id is not None:
            return MatchSent(result.request_id)
        if result.sent is Sent.NOT_RECEIVED:
            raise ProviderDeferred("ioTec Verify did not receive the face match")
        if result.sent is Sent.UNCERTAIN:
            raise ProviderUncertain("ioTec Verify may hold this face match")
        return MatchRefused("iotec_refused")

    async def result(self, request_id: str) -> MatchPending | MatchAnswer | MatchNotRun:
        if not iotec_verify.is_request_id(request_id):
            raise LookupError("Not an ioTec Verify request ID")
        try:
            answer = await self._verify.read(f"/api/face-match/{request_id}")
        except VerifyUnavailable:
            raise ProviderDeferred("ioTec Verify could not be read") from None
        if answer.get("status") in _WAITING:
            return MatchPending()
        reading = face_match_reading(answer)
        if reading is None:
            # Failed, expired or cancelled at ioTec, or an answer this adapter does not know.
            return MatchNotRun("match_not_run")
        score, identical = reading
        return MatchAnswer(score=score, identical=identical)

    async def _photo(self, photo: PhotoRef) -> bytes:
        try:
            return await self._storage.read(photo.storage_key, sha256=photo.sha256)
        except StorageError:
            raise ProviderDeferred("A photo could not be read from storage") from None
