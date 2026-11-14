"""The ioTec Verify adapter for identity checks. See docs/identity-checks.md#iotec-verify.

ioTec Verify has one API per check, so a submission is up to three requests:

- ID verification looks the details up at NIRA. Only for a Ugandan national ID.
- Face match compares the selfie with the photo of the document.
- AML screening checks the name and date of birth against sanctions and politically exposed
  person lists.

Each is sent in the background (`isBlocking: false`) with the submission's key as its
`referenceId`, and the three request IDs together are the reference the worker saves. A check
that ioTec refused, or that could not be sent, is left out, so it shows as not run and the
client goes to a person. Nothing here is logged, and no error quotes what ioTec sent back,
because its answers carry the client's details.
"""

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

import httpx
from pydantic import SecretStr

from app.infrastructure.storage import FileStorage, StorageError
from app.modules.kyc.domain.checks import DocumentType, PhotoKind
from app.modules.kyc.domain.provider import (
    Accepted,
    AutomaticCheck,
    CheckOutcome,
    Completed,
    Pending,
    PhotoRef,
    ProviderDeferred,
    ProviderRequest,
    ProviderUncertain,
    Refused,
    Verdict,
)

BASE_URL = "https://verify-api.iotec.io"
# A face match at or above this confidence, and called identical by ioTec, passes. Below it,
# or not identical, it is unsure rather than failed: a person looks either way.
FACE_MATCH_PASS = 80
# Spelling differences of one letter still count as a hit on a list.
FUZZY_DISTANCE = 1

_PREFIX = "iotec:"
_SKIPPED = "-"
_REFERENCE = re.compile(
    r"iotec:idv=(?P<idv>[0-9a-f-]{36}|-),face=(?P<face>[0-9a-f-]{36}|-),"
    r"aml=(?P<aml>[0-9a-f-]{36}|-)"
)
_REQUEST_ID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
# Request states shared by every ioTec Verify request.
_WAITING = frozenset({"Pending", "Processing"})
_DONE = frozenset({"Completed", "Success"})


class _Sent(StrEnum):
    ACCEPTED = "accepted"
    REFUSED = "refused"  # ioTec answered and will not run it
    NOT_RECEIVED = "not_received"  # certainly never reached ioTec
    UNCERTAIN = "uncertain"  # may have reached ioTec


@dataclass(frozen=True, slots=True)
class _Attempt:
    sent: _Sent
    request_id: str | None = None


class IotecVerifyProvider:
    name = "iotec-verify"
    automatic = True

    def __init__(
        self,
        *,
        client_id: str,
        api_key: SecretStr,
        storage: FileStorage,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
        retry_pause: float = 1.0,
    ) -> None:
        if not client_id or not api_key.get_secret_value():
            raise ValueError("ioTec Verify needs a client ID and an API key")
        if not base_url.startswith("https://"):
            # The key and the client's details travel in every request.
            raise ValueError("ioTec Verify is only reached over https")
        self._client_id = client_id
        self._api_key = api_key
        self._storage = storage
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client
        self._retry_pause = retry_pause

    # Sending ---------------------------------------------------------------------------------

    async def submit(self, request: ProviderRequest) -> Accepted | Refused:
        # The photos are read first, so a storage problem sends nothing at all.
        selfie = request.photos.get(PhotoKind.SELFIE)
        front = request.photos.get(PhotoKind.FRONT)
        face_photos = None
        if selfie is not None and front is not None:
            face_photos = (await self._photo(selfie), await self._photo(front))
        attempts = {
            "idv": await self._send_idv(request),
            "face": await self._send_face(request, face_photos),
            "aml": await self._send_aml(request),
        }
        sent = {attempt.sent for attempt in attempts.values()}
        if _Sent.ACCEPTED not in sent:
            if _Sent.UNCERTAIN in sent:
                raise ProviderUncertain("ioTec Verify may hold this submission")
            if _Sent.NOT_RECEIVED in sent:
                raise ProviderDeferred("ioTec Verify did not receive this submission")
            return Refused("iotec_refused")
        # Something was accepted. Whatever was not is left out and shows as not run: sending
        # again would repeat the checks that did arrive.
        parts = ",".join(
            f"{name}={attempt.request_id if attempt.sent is _Sent.ACCEPTED else _SKIPPED}"
            for name, attempt in attempts.items()
        )
        return Accepted(_PREFIX + parts)

    async def find(self, idempotency_key: str) -> str | None:
        # ioTec Verify cannot look a request up by our reference, so it never claims to hold
        # one. After an uncertain send the client goes to a person (provider_not_received).
        return None

    async def _photo(self, photo: PhotoRef) -> tuple[bytes, str]:
        try:
            return await self._storage.read(photo.storage_key, sha256=photo.sha256), (
                photo.content_type
            )
        except StorageError:
            raise ProviderDeferred("A photo could not be read from storage") from None

    async def _send_idv(self, request: ProviderRequest) -> _Attempt:
        # NIRA holds Ugandan national IDs only. Other documents are checked by a person.
        if request.document_type is not DocumentType.NATIONAL_ID or (
            request.document_country != "UG"
        ):
            return _Attempt(_Sent.REFUSED)
        details = request.details
        body: dict[str, object] = {
            "referenceId": request.idempotency_key,
            "isBlocking": False,
            "captureMethod": "FormDataCapture",
            "nationalId": details.document_number,
            "surname": details.surname,
            "givenNames": details.given_names,
            "dateOfBirth": details.date_of_birth.isoformat(),
        }
        if details.document_expires_on is not None:
            body["dateOfExpiration"] = details.document_expires_on.isoformat()
        return await self._post("/api/idv/verify-person-information", json=body)

    async def _send_face(
        self,
        request: ProviderRequest,
        photos: tuple[tuple[bytes, str], tuple[bytes, str]] | None,
    ) -> _Attempt:
        if photos is None:
            return _Attempt(_Sent.REFUSED)
        (selfie, selfie_type), (front, front_type) = photos
        return await self._post(
            "/api/face-match",
            data={"ReferenceId": request.idempotency_key, "IsBlocking": "false"},
            files={
                "Photo1": ("selfie", selfie, selfie_type),
                "Photo2": ("document", front, front_type),
            },
        )

    async def _send_aml(self, request: ProviderRequest) -> _Attempt:
        details = request.details
        return await self._post(
            "/api/aml-screening",
            json={
                "referenceId": request.idempotency_key,
                "isBlocking": False,
                "category": "Person",
                "names": f"{details.given_names} {details.surname}",
                "dateOfBirth": details.date_of_birth.isoformat(),
                "fuzzySearch": FUZZY_DISTANCE,
            },
        )

    async def _post(
        self,
        path: str,
        *,
        json: Mapping[str, object] | None = None,
        data: Mapping[str, str] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
    ) -> _Attempt:
        attempt = await self._post_once(path, json=json, data=data, files=files)
        if attempt.sent is _Sent.NOT_RECEIVED:
            # Certainly never arrived, so one more try is safe. Without it a dropped connection
            # would leave a check, such as the screening, out of the submission.
            await asyncio.sleep(self._retry_pause)
            attempt = await self._post_once(path, json=json, data=data, files=files)
        return attempt

    async def _post_once(
        self,
        path: str,
        *,
        json: Mapping[str, object] | None,
        data: Mapping[str, str] | None,
        files: Mapping[str, tuple[str, bytes, str]] | None,
    ) -> _Attempt:
        try:
            response = await self._request("POST", path, json=json, data=data, files=files)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return _Attempt(_Sent.NOT_RECEIVED)
        except httpx.HTTPError:
            # A read timeout or a dropped connection: it may have arrived.
            return _Attempt(_Sent.UNCERTAIN)
        status = response.status_code
        if 200 <= status < 300:
            request_id = _request_id(response)
            # Accepted, but with nothing to read it back by: treat it as possibly held.
            return (
                _Attempt(_Sent.UNCERTAIN)
                if request_id is None
                else _Attempt(_Sent.ACCEPTED, request_id)
            )
        if status in (401, 403, 429):
            # Refused before it ran: wrong credentials, or too many requests.
            return _Attempt(_Sent.NOT_RECEIVED)
        if status >= 500 or status == 408:
            return _Attempt(_Sent.UNCERTAIN)
        return _Attempt(_Sent.REFUSED)

    # Reading ---------------------------------------------------------------------------------

    async def result(self, reference: str) -> Pending | Completed:
        match = _REFERENCE.fullmatch(reference)
        if match is None:
            raise LookupError("Not an ioTec Verify reference")
        outcomes: list[CheckOutcome] = []
        for name, path, read in (
            ("idv", "/api/idv/verify-person-information/", _idv_outcome),
            ("face", "/api/face-match/", _face_outcome),
            ("aml", "/api/aml-screening/", _aml_outcome),
        ):
            request_id = match.group(name)
            if request_id == _SKIPPED:
                continue
            answer = await self._read(path + request_id)
            if answer.get("status") in _WAITING:
                return Pending()
            outcome = read(answer)
            if outcome is not None:
                outcomes.append(outcome)
        return Completed(tuple(outcomes))

    async def _read(self, path: str) -> Mapping[str, object]:
        try:
            response = await self._request("GET", path)
        except httpx.HTTPError:
            # Reading changes nothing at ioTec, so the worker simply reads again later.
            raise ProviderDeferred("ioTec Verify is unreachable") from None
        if response.status_code != 200:
            raise ProviderDeferred(f"ioTec Verify answered {response.status_code}")
        body = _json(response)
        if body is None:
            raise ProviderDeferred("ioTec Verify answered with something that is not JSON")
        return body

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, object] | None = None,
        data: Mapping[str, str] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
    ) -> httpx.Response:
        headers = {
            "Client-Id": self._client_id,
            "X-Api-Key": self._api_key.get_secret_value(),
            "User-Agent": "finvest",
        }
        url = self._base_url + path
        if self._client is not None:
            return await self._client.request(
                method,
                url,
                headers=headers,
                json=json,
                data=data,
                files=files,
                timeout=self._timeout,
            )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.request(
                method, url, headers=headers, json=json, data=data, files=files
            )


# Turning answers into outcomes. Unknown or missing values are unsure, never a pass. ----------


def _idv_outcome(answer: Mapping[str, object]) -> CheckOutcome | None:
    if answer.get("status") not in _DONE:
        return None  # failed, expired or cancelled at ioTec: not run
    matching = _lower(answer.get("matchingStatus"))
    card = _lower(answer.get("cardStatus"))
    if matching in ("false", "notmatching", "not_matching", "mismatch"):
        verdict = Verdict.FAIL
    elif matching in ("true", "matching", "match") and card == "valid":
        verdict = Verdict.PASS
    else:
        verdict = Verdict.UNSURE
    return CheckOutcome(AutomaticCheck.DOCUMENT_GENUINE, verdict)


def _face_outcome(answer: Mapping[str, object]) -> CheckOutcome | None:
    if answer.get("faceMatchStatus") != "Success":
        return None
    confidence = answer.get("confidence")
    score = None
    if (
        isinstance(confidence, int | float)
        and not isinstance(confidence, bool)
        and 0 <= confidence <= 1
    ):
        # A confidence from 0.0 to 1.0, shown to staff as a whole percentage. Anything else
        # is not understood, so it has no score and cannot pass.
        score = round(confidence * 100)
    identical = answer.get("isIdentical") is True
    verdict = (
        Verdict.PASS
        if identical and score is not None and score >= FACE_MATCH_PASS
        else (Verdict.UNSURE)
    )
    return CheckOutcome(AutomaticCheck.SELFIE_MATCH, verdict, score)


def _aml_outcome(answer: Mapping[str, object]) -> CheckOutcome | None:
    found = answer.get("verificationStatus")
    if found == "NoHits":
        return CheckOutcome(AutomaticCheck.SCREENING, Verdict.PASS)
    if found == "HasHits":
        # A possible match on a list. A person compares it; it never rejects on its own.
        return CheckOutcome(AutomaticCheck.SCREENING, Verdict.FAIL)
    return None


def _lower(value: object) -> str | None:
    return value.strip().lower() if isinstance(value, str) else None


def _json(response: httpx.Response) -> Mapping[str, object] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _request_id(response: httpx.Response) -> str | None:
    body = _json(response)
    found = None if body is None else body.get("id")
    return found.lower() if isinstance(found, str) and _REQUEST_ID.fullmatch(found) else None
