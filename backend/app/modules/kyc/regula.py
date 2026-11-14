"""The Regula adapter for identity checks. See docs/identity-checks.md#regula.

Regula runs on Finvest's own servers, so the ID photos and the selfie never leave them. Two
services answer a submission, each in one request:

- Document Reader (`POST /api/process`, scenario `FullAuth`) reads the ID from the photos of
  its sides and checks its security features, which is how a forged or altered ID is caught.
  The number it reads must be the number the client typed.
- Face (`POST /api/match`) compares the selfie with the face printed on the ID.

Both answer straight away, and neither changes anything at Regula: a request that may have
arrived can be sent again with no harm, so every problem sending is `ProviderDeferred` and the
worker tries again. The two verdicts are kept in the reference the worker saves, and `result`
reads them back from it, so nothing is asked twice.

There is no ID number lookup at NIRA, and no screening: the screening module screens.

Nothing here is logged, and no error quotes what Regula sent back, because its answers carry
the client's details.
"""

import base64
import hashlib
import ipaddress
import re
from collections.abc import Iterator, Mapping, Sequence
from urllib.parse import urlsplit

import httpx

from app.infrastructure.storage import FileStorage, StorageError
from app.modules.kyc.domain.checks import PhotoKind
from app.modules.kyc.domain.provider import (
    Accepted,
    AutomaticCheck,
    CheckOutcome,
    Completed,
    Pending,
    PhotoRef,
    ProviderDeferred,
    ProviderRequest,
    Refused,
    Verdict,
)

# A face match at or above this similarity, in percent, passes. Below it the selfie is unsure,
# never failed: a person looks either way. To be tuned on Finvest's own results.
FACE_MATCH_PASS = 90

# Regula's answers for one check (CheckResult).
_ERROR, _OK, _NOT_DONE = 0, 1, 2
# Result types in a Document Reader answer.
_STATUS_RESULT, _TEXT_RESULT = 33, 36
# Text fields that can hold the number the client typed: the document number, and the
# personal number (on a Ugandan national ID, the NIN).
_NUMBER_FIELDS = frozenset({2, 7})
# The light the photos were taken in: ordinary white light from a phone camera.
_WHITE_LIGHT = 6
_FINISHED = 1
# Face image sources (ImageSource): a live selfie, and a face printed on a document.
_LIVE, _DOCUMENT_PRINTED = 3, 1

_PREFIX = "regula:"
_SKIPPED = "-"
_REFERENCE = re.compile(
    r"regula:[0-9a-f]{16}:doc=(?P<doc>pass|fail|unsure|-),"
    r"face=(?P<face>pass|fail|unsure|-)(?:@(?P<score>\d{1,3}))?"
)
_NOT_ALPHANUMERIC = re.compile(r"[^0-9A-Z]")


class RegulaProvider:
    name = "regula"
    automatic = True

    def __init__(
        self,
        *,
        document_reader_url: str,
        face_url: str,
        storage: FileStorage,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._document_reader_url = document_reader_url.rstrip("/")
        self._face_url = face_url.rstrip("/")
        self._storage = storage
        self._timeout = timeout
        self._client = client

    # Sending ---------------------------------------------------------------------------------

    async def submit(self, request: ProviderRequest) -> Accepted | Refused:
        front = request.photos.get(PhotoKind.FRONT)
        if front is None:
            return Refused("no_document_photo")
        # The photos are read first, so a storage problem sends nothing at all.
        sides = [await self._photo(front)]
        back = request.photos.get(PhotoKind.BACK)
        if back is not None:
            sides.append(await self._photo(back))
        selfie_ref = request.photos.get(PhotoKind.SELFIE)
        selfie = None if selfie_ref is None else await self._photo(selfie_ref)

        tag = hashlib.sha256(request.idempotency_key.encode()).hexdigest()
        answer = await self._post(
            self._document_reader_url + "/api/process",
            {
                "processParam": {"scenario": "FullAuth"},
                "List": [
                    {"ImageData": {"image": _b64(side)}, "light": _WHITE_LIGHT, "page_idx": page}
                    for page, side in enumerate(sides)
                ],
                "tag": tag[:32],
            },
        )
        if answer is None:
            return Refused("regula_refused")
        document = document_verdict(answer, request.details.document_number)

        face, score = _SKIPPED, None
        if selfie is not None:
            matched = await self._post(
                self._face_url + "/api/match",
                {
                    "images": [
                        {"index": 0, "type": _LIVE, "data": _b64(selfie)},
                        {"index": 1, "type": _DOCUMENT_PRINTED, "data": _b64(sides[0])},
                    ]
                },
            )
            if matched is not None:
                verdict, score = face_verdict(matched)
                face = verdict.value
        doc = _SKIPPED if document is None else document.value
        reference = f"{_PREFIX}{tag[:16]}:doc={doc},face={face}"
        if score is not None:
            reference += f"@{score}"
        return Accepted(reference)

    async def find(self, idempotency_key: str) -> str | None:
        # Every send is sorted as deferred or answered, never uncertain, so there is nothing to
        # look up: Regula keeps no result we could ask for again.
        return None

    async def _photo(self, photo: PhotoRef) -> bytes:
        try:
            return await self._storage.read(photo.storage_key, sha256=photo.sha256)
        except StorageError:
            raise ProviderDeferred("A photo could not be read from storage") from None

    async def _post(self, url: str, body: Mapping[str, object]) -> Mapping[str, object] | None:
        """Regula's answer, or None if it refused the request. Anything else is deferred.

        Neither service changes anything when it answers, so a request that timed out or met a
        server error is safe to send again. A refusal (400 or 422) would be refused again.
        """
        try:
            if self._client is not None:
                response = await self._client.post(url, json=body, timeout=self._timeout)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=body)
        except httpx.HTTPError:
            raise ProviderDeferred("Regula could not be reached") from None
        if response.status_code in (400, 422):
            return None
        if response.status_code != 200:
            # 403 is a licence that is missing or ran out: fixable, so tried again.
            raise ProviderDeferred("Regula did not answer the request")
        try:
            answer = response.json()
        except ValueError:
            raise ProviderDeferred("Regula's answer could not be read") from None
        if not isinstance(answer, dict):
            raise ProviderDeferred("Regula's answer could not be read")
        return answer

    # Reading ---------------------------------------------------------------------------------

    async def result(self, reference: str) -> Pending | Completed:
        match = _REFERENCE.fullmatch(reference)
        if match is None:
            raise LookupError("Not a Regula reference")
        outcomes: list[CheckOutcome] = []
        if (doc := match.group("doc")) != _SKIPPED:
            outcomes.append(CheckOutcome(AutomaticCheck.DOCUMENT_GENUINE, Verdict(doc)))
        if (face := match.group("face")) != _SKIPPED:
            score = match.group("score")
            outcomes.append(
                CheckOutcome(
                    AutomaticCheck.SELFIE_MATCH,
                    Verdict(face),
                    None if score is None else int(score),
                )
            )
        return Completed(tuple(outcomes))


# Turning answers into verdicts. Unknown or missing values are unsure, never a pass. ---------


def document_verdict(answer: Mapping[str, object], typed_number: str) -> Verdict | None:
    """Whether the ID is genuine and is the one the client described. None if not processed.

    Fails when a security feature failed, which is how an altered or forged ID shows, or when
    Regula did not recognise the document at all. Passes only when the security features, the
    document type and the image quality were all checked and fine, and the number Regula read
    is the number the client typed. Anything else is unsure, and a person looks.
    """
    if answer.get("ProcessingFinished") != _FINISHED:
        return None
    status = _status(answer)
    if status is None:
        return Verdict.UNSURE
    optical = status.get("detailsOptical")
    if not isinstance(optical, Mapping):
        return Verdict.UNSURE
    security, doc_type, quality = (
        optical.get("security"),
        optical.get("docType"),
        optical.get("imageQA"),
    )
    if security == _ERROR or doc_type == _ERROR:
        return Verdict.FAIL
    if security != _OK or doc_type != _OK or quality != _OK:
        return Verdict.UNSURE
    typed = _plain(typed_number)
    if not typed or typed not in set(_read_numbers(answer)):
        return Verdict.UNSURE
    return Verdict.PASS


def face_verdict(answer: Mapping[str, object]) -> tuple[Verdict, int | None]:
    """The selfie match and its similarity as a whole percentage.

    Passes at `FACE_MATCH_PASS` or above. A lower similarity, or none, is unsure: a match
    never fails a client on its own.
    """
    results = answer.get("results")
    similarity = None
    if isinstance(results, Sequence) and results and isinstance(results[0], Mapping):
        value = results[0].get("similarity")
        if isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 100:
            similarity = int(value)  # rounded down, so 89.9 does not pass as 90
    if similarity is not None and similarity >= FACE_MATCH_PASS:
        return Verdict.PASS, similarity
    return Verdict.UNSURE, similarity


def _containers(answer: Mapping[str, object]) -> Iterator[Mapping[str, object]]:
    container_list = answer.get("ContainerList")
    items = container_list.get("List") if isinstance(container_list, Mapping) else None
    if isinstance(items, Sequence):
        yield from (item for item in items if isinstance(item, Mapping))


def _status(answer: Mapping[str, object]) -> Mapping[str, object] | None:
    for item in _containers(answer):
        if item.get("result_type") == _STATUS_RESULT and isinstance(item.get("Status"), Mapping):
            status = item["Status"]
            assert isinstance(status, Mapping)
            return status
    return None


def _read_numbers(answer: Mapping[str, object]) -> Iterator[str]:
    for item in _containers(answer):
        text = item.get("Text") if item.get("result_type") == _TEXT_RESULT else None
        fields = text.get("fieldList") if isinstance(text, Mapping) else None
        if not isinstance(fields, Sequence):
            continue
        for field in fields:
            if not isinstance(field, Mapping) or field.get("fieldType") not in _NUMBER_FIELDS:
                continue
            values = [field.get("value")]
            listed = field.get("valueList")
            if isinstance(listed, Sequence):
                values += [v.get("value") for v in listed if isinstance(v, Mapping)]
            yield from (_plain(v) for v in values if isinstance(v, str) and _plain(v))


def _plain(number: str) -> str:
    return _NOT_ALPHANUMERIC.sub("", number.upper())


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def private_or_https(url: str) -> bool:
    """Whether a Regula address is safe to send ID photos to.

    https anywhere, or plain http to a service on Finvest's own private network: a container
    name, localhost or a private address. Never plain http over the internet.
    """
    parts = urlsplit(url)
    if parts.scheme == "https":
        return bool(parts.hostname)
    if parts.scheme != "http" or not parts.hostname:
        return False
    host = parts.hostname
    if host == "localhost" or "." not in host:
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False
