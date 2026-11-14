"""The Regula adapter against a fake Regula: answered, refused, timed out and unsure."""

import base64
import json
from collections.abc import Callable
from datetime import date

import httpx
import pytest
from pydantic import ValidationError

from app.infrastructure.storage import MemoryStorage, NewFile
from app.modules.kyc.domain.checks import DocumentType, IdentityDetails, PhotoKind
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
    decide,
)
from app.modules.kyc.regula import (
    FACE_MATCH_PASS,
    RegulaProvider,
    document_verdict,
    face_verdict,
    private_or_https,
)
from app.modules.kyc.settings import KycSettings

KEY = "check:1"
NUMBER = "CM94062210ABCD"
SELFIE = b"\xff\xd8\xff selfie"
FRONT = b"\xff\xd8\xff front"
BACK = b"\xff\xd8\xff back"
DOCUMENT_READER = "http://regula-docreader:8080"
FACE = "http://regula-face:41101"

Handler = Callable[[httpx.Request], httpx.Response]


def processed(
    *,
    security: int = 1,
    doc_type: int = 1,
    quality: int = 1,
    numbers: tuple[str, ...] = ("CM 94062210 ABCD",),
    finished: int = 1,
) -> dict[str, object]:
    """A Document Reader answer, trimmed to what the adapter reads."""
    return {
        "ProcessingFinished": finished,
        "ContainerList": {
            "Count": 2,
            "List": [
                {
                    "result_type": 33,
                    "Status": {
                        "overallStatus": min(security, doc_type, quality),
                        "optical": security,
                        "detailsOptical": {
                            "overallStatus": security,
                            "docType": doc_type,
                            "imageQA": quality,
                            "security": security,
                        },
                    },
                },
                {
                    "result_type": 36,
                    "Text": {
                        "status": 1,
                        "fieldList": [
                            {"fieldType": 8, "value": "NAKATO"},
                            *(
                                {"fieldType": 7, "valueList": [{"source": "VISUAL", "value": n}]}
                                for n in numbers
                            ),
                        ],
                    },
                },
            ],
        },
    }


def matched(similarity: object) -> dict[str, object]:
    return {"code": 0, "results": [{"firstIndex": 0, "secondIndex": 1, "similarity": similarity}]}


class FakeRegula:
    """Answers like the two Regula services. Each test changes what one path says."""

    def __init__(self) -> None:
        self.sent: list[httpx.Request] = []
        self.paths: dict[str, Handler] = {
            "/api/process": lambda _: httpx.Response(200, json=processed()),
            "/api/match": lambda _: httpx.Response(200, json=matched(96.4)),
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        assert request.method == "POST"
        host = {"regula-docreader": "/api/process", "regula-face": "/api/match"}
        assert host[request.url.host] == request.url.path
        return self.paths[request.url.path](request)

    def body(self, path: str) -> dict[str, object]:
        sent = [r for r in self.sent if r.url.path == path]
        assert len(sent) == 1
        body = json.loads(sent[0].content)
        assert isinstance(body, dict)
        return body


def timed_out(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("no answer", request=request)


def unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


def answering(status: int, body: object = None) -> Handler:
    return lambda _: httpx.Response(status, json=body or {})


async def request(
    storage: MemoryStorage, kinds: tuple[PhotoKind, ...] = tuple(PhotoKind)
) -> ProviderRequest:
    photos = {}
    data = {PhotoKind.SELFIE: SELFIE, PhotoKind.FRONT: FRONT, PhotoKind.BACK: BACK}
    for kind in kinds:
        stored = await storage.put(NewFile(f"kyc/check/{kind.value}", data[kind], "image/jpeg"))
        photos[kind] = PhotoRef(stored.key, stored.sha256, "image/jpeg")
    return ProviderRequest(
        idempotency_key=KEY,
        document_type=DocumentType.NATIONAL_ID,
        document_country="UG",
        details=IdentityDetails(
            surname="NAKATO",
            given_names="Amara",
            document_number=NUMBER,
            date_of_birth=date(1994, 6, 22),
            document_expires_on=date(2030, 9, 18),
        ),
        photos=photos,
    )


async def submitted(
    fake: FakeRegula, kinds: tuple[PhotoKind, ...] = tuple(PhotoKind)
) -> tuple[RegulaProvider, Accepted | Refused]:
    storage = MemoryStorage()
    adapter = RegulaProvider(
        document_reader_url=DOCUMENT_READER + "/",
        face_url=FACE,
        storage=storage,
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
    )
    return adapter, await adapter.submit(await request(storage, kinds))


async def outcomes(fake: FakeRegula) -> dict[AutomaticCheck, CheckOutcome]:
    adapter, answer = await submitted(fake)
    assert isinstance(answer, Accepted)
    result = await adapter.result(answer.reference)
    assert isinstance(result, Completed)
    return {outcome.check: outcome for outcome in result.outcomes}


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def test_a_genuine_id_and_a_matching_selfie_both_pass() -> None:
    fake = FakeRegula()
    found = await outcomes(fake)
    assert found == {
        AutomaticCheck.DOCUMENT_GENUINE: CheckOutcome(
            AutomaticCheck.DOCUMENT_GENUINE, Verdict.PASS
        ),
        AutomaticCheck.SELFIE_MATCH: CheckOutcome(AutomaticCheck.SELFIE_MATCH, Verdict.PASS, 96),
    }
    # Both sides of the ID, checked for security features, under a tag that is not the key.
    process = fake.body("/api/process")
    assert process["processParam"] == {"scenario": "FullAuth"}
    assert process["List"] == [
        {"ImageData": {"image": b64(FRONT)}, "light": 6, "page_idx": 0},
        {"ImageData": {"image": b64(BACK)}, "light": 6, "page_idx": 1},
    ]
    assert isinstance(process["tag"], str) and KEY not in process["tag"]
    # The live selfie against the face printed on the front of the ID.
    assert fake.body("/api/match") == {
        "images": [
            {"index": 0, "type": 3, "data": b64(SELFIE)},
            {"index": 1, "type": 1, "data": b64(FRONT)},
        ]
    }


async def test_the_reference_holds_no_details_and_the_same_submission_gets_the_same_one() -> None:
    _, first = await submitted(FakeRegula())
    _, second = await submitted(FakeRegula())
    assert isinstance(first, Accepted) and first == second
    assert NUMBER not in first.reference and "NAKATO" not in first.reference
    assert len(first.reference) <= 200  # kyc_checks.provider_reference


async def test_regula_never_screens_so_clean_results_alone_do_not_approve() -> None:
    found = await outcomes(FakeRegula())
    assert AutomaticCheck.SCREENING not in found
    assert AutomaticCheck.LIVENESS not in found
    decision = decide(tuple(found.values()), automatic_approval=True)
    assert not decision.approve
    assert set(decision.flags) == {"screening_not_run", "liveness_not_run"}
    # With liveness and screening added by the service, it approves.
    full = (
        *found.values(),
        CheckOutcome(AutomaticCheck.LIVENESS, Verdict.PASS),
        CheckOutcome(AutomaticCheck.SCREENING, Verdict.PASS),
    )
    assert decide(full, automatic_approval=True).approve


@pytest.mark.parametrize(
    ("answer", "verdict"),
    [
        # A security feature failed: an altered or forged ID.
        (processed(security=0), Verdict.FAIL),
        # Not a document Regula knows.
        (processed(doc_type=0), Verdict.FAIL),
        # Not checked, or a poor photo: a person looks.
        (processed(security=2), Verdict.UNSURE),
        (processed(quality=0), Verdict.UNSURE),
        (processed(quality=2), Verdict.UNSURE),
        # A genuine ID, but not the number the client typed.
        (processed(numbers=("CM00000000XXXX",)), Verdict.UNSURE),
        (processed(numbers=()), Verdict.UNSURE),
        # The number in any field that can hold it, spaced or not.
        (processed(numbers=("CM00000000XXXX", "cm94062210abcd")), Verdict.PASS),
        ({"ProcessingFinished": 1}, Verdict.UNSURE),
        (
            {"ProcessingFinished": 1, "ContainerList": {"List": [{"result_type": 33}]}},
            Verdict.UNSURE,
        ),
        ({"ProcessingFinished": 1, "ContainerList": "nonsense"}, Verdict.UNSURE),
    ],
)
def test_the_document_verdict(answer: dict[str, object], verdict: Verdict) -> None:
    assert document_verdict(answer, NUMBER) is verdict


def test_a_document_not_processed_has_no_verdict() -> None:
    assert document_verdict(processed(finished=2), NUMBER) is None
    assert document_verdict(processed(finished=0), NUMBER) is None
    assert document_verdict({}, NUMBER) is None


def test_an_empty_typed_number_never_passes() -> None:
    assert document_verdict(processed(numbers=("",)), " - ") is Verdict.UNSURE


@pytest.mark.parametrize(
    ("answer", "verdict", "score"),
    [
        (matched(FACE_MATCH_PASS), Verdict.PASS, FACE_MATCH_PASS),
        (matched(100), Verdict.PASS, 100),
        # Rounded down, so just under the line does not pass.
        (matched(FACE_MATCH_PASS - 0.1), Verdict.UNSURE, FACE_MATCH_PASS - 1),
        (matched(40), Verdict.UNSURE, 40),
        # A fraction, as if the unit were not percent, is too low to pass: never a guess.
        (matched(0.98), Verdict.UNSURE, 0),
        (matched(101), Verdict.UNSURE, None),
        (matched(-1), Verdict.UNSURE, None),
        (matched(True), Verdict.UNSURE, None),
        (matched("96"), Verdict.UNSURE, None),
        ({"code": 0, "results": []}, Verdict.UNSURE, None),
        ({"code": 0}, Verdict.UNSURE, None),
    ],
)
def test_the_face_verdict(answer: dict[str, object], verdict: Verdict, score: int | None) -> None:
    assert face_verdict(answer) == (verdict, score)


async def test_a_low_match_is_unsure_with_its_score() -> None:
    fake = FakeRegula()
    fake.paths["/api/match"] = answering(200, matched(71.8))
    found = await outcomes(fake)
    assert found[AutomaticCheck.SELFIE_MATCH] == CheckOutcome(
        AutomaticCheck.SELFIE_MATCH, Verdict.UNSURE, 71
    )


async def test_a_forged_id_fails_the_document_check() -> None:
    fake = FakeRegula()
    fake.paths["/api/process"] = answering(200, processed(security=0))
    found = await outcomes(fake)
    assert found[AutomaticCheck.DOCUMENT_GENUINE].verdict is Verdict.FAIL
    # A failed check sends the client to a person; it never rejects them.
    assert not decide(tuple(found.values()), automatic_approval=True).approve


async def test_with_no_selfie_the_match_is_not_run() -> None:
    fake = FakeRegula()
    adapter, answer = await submitted(fake, (PhotoKind.FRONT,))
    assert isinstance(answer, Accepted)
    assert [r.url.path for r in fake.sent] == ["/api/process"]
    result = await adapter.result(answer.reference)
    assert isinstance(result, Completed)
    assert [o.check for o in result.outcomes] == [AutomaticCheck.DOCUMENT_GENUINE]
    assert len(fake.body("/api/process")["List"]) == 1  # type: ignore[arg-type]


async def test_a_document_regula_did_not_finish_shows_as_not_run() -> None:
    fake = FakeRegula()
    fake.paths["/api/process"] = answering(200, processed(finished=2))
    found = await outcomes(fake)
    assert AutomaticCheck.DOCUMENT_GENUINE not in found
    assert found[AutomaticCheck.SELFIE_MATCH].verdict is Verdict.PASS


async def test_with_no_front_nothing_is_sent() -> None:
    fake = FakeRegula()
    _, answer = await submitted(fake, (PhotoKind.SELFIE,))
    assert answer == Refused("no_document_photo")
    assert fake.sent == []


async def test_a_refused_document_is_refused_and_a_refused_match_is_not_run() -> None:
    fake = FakeRegula()
    fake.paths["/api/process"] = answering(400, {"message": "NAKATO CM94062210ABCD"})
    _, answer = await submitted(fake)
    assert answer == Refused("regula_refused")
    assert [r.url.path for r in fake.sent] == ["/api/process"]

    fake = FakeRegula()
    fake.paths["/api/match"] = answering(422)
    found = await outcomes(fake)
    assert set(found) == {AutomaticCheck.DOCUMENT_GENUINE}


@pytest.mark.parametrize("path", ["/api/process", "/api/match"])
@pytest.mark.parametrize(
    "handler",
    [
        timed_out,
        unreachable,
        answering(500),
        answering(503),
        answering(429),
        answering(403),  # no licence, or it ran out
        lambda _: httpx.Response(200, content=b"<html>not json</html>"),
        answering(200, ["not", "an", "object"]),
    ],
)
async def test_anything_but_an_answer_is_deferred_with_no_details(
    path: str, handler: Handler
) -> None:
    # Regula changes nothing when it answers, so the worker simply sends it again later.
    fake = FakeRegula()
    fake.paths[path] = handler
    with pytest.raises(ProviderDeferred) as raised:
        await submitted(fake)
    assert raised.value.__cause__ is None
    assert NUMBER not in str(raised.value)


async def test_a_photo_missing_from_storage_sends_nothing() -> None:
    fake = FakeRegula()
    adapter = RegulaProvider(
        document_reader_url=DOCUMENT_READER,
        face_url=FACE,
        storage=MemoryStorage(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
    )
    with pytest.raises(ProviderDeferred):
        await adapter.submit(await request(MemoryStorage()))
    assert fake.sent == []


async def test_it_holds_nothing_to_look_up_and_reads_only_its_own_references() -> None:
    adapter, _ = await submitted(FakeRegula())
    assert await adapter.find(KEY) is None
    for reference in ("stub:pass:check:1", "iotec:idv=-,face=-,aml=-", "regula:xyz:doc=pass"):
        with pytest.raises(LookupError):
            await adapter.result(reference)
    empty = await adapter.result("regula:0123456789abcdef:doc=-,face=-")
    assert empty == Completed(())
    assert not isinstance(empty, Pending)


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://regula.example.com", True),
        ("http://regula-docreader:8080", True),
        ("http://localhost:8080", True),
        ("http://10.110.0.5:8080", True),
        ("http://192.168.1.4", True),
        ("http://regula.example.com", False),
        ("http://8.8.8.8:8080", False),
        ("ftp://regula-docreader", False),
        ("regula-docreader:8080", False),
        ("https://", False),
    ],
)
def test_photos_go_only_over_https_or_the_private_network(url: str, allowed: bool) -> None:
    assert private_or_https(url) is allowed


def test_regula_needs_its_addresses_and_approves_clean_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FINVEST_IDENTITY_AUTOMATIC_APPROVAL", raising=False)
    key = "A" * 43
    with pytest.raises(ValidationError, match="FINVEST_REGULA_DOCUMENT_READER_URL"):
        KycSettings(identity_data_key=key, identity_provider="regula")
    with pytest.raises(ValidationError, match="private network"):
        KycSettings(
            identity_data_key=key,
            identity_provider="regula",
            regula_document_reader_url="http://regula.example.com",
            regula_face_url=FACE,
        )
    settings = KycSettings(
        identity_data_key=key,
        identity_provider="regula",
        regula_document_reader_url=DOCUMENT_READER,
        regula_face_url=FACE,
    )
    assert isinstance(settings.provider(MemoryStorage()), RegulaProvider)
    # The owner's decision: on with Regula unless switched off.
    assert settings.automatic_approval
    off = KycSettings(
        identity_data_key=key,
        identity_provider="regula",
        regula_document_reader_url=DOCUMENT_READER,
        regula_face_url=FACE,
        identity_automatic_approval=False,
    )
    assert not off.automatic_approval
    # Liveness stays with the camera selfie until Regula's liveness is wired.
    assert settings.liveness() is None and settings.face_matcher(MemoryStorage()) is None


def test_regulas_demo_services_are_refused_in_production() -> None:
    key = "A" * 43
    for document_reader, face in (
        ("https://api.regulaforensics.com", "https://faceapi.example.com"),
        ("https://docreader.example.com", "https://NIGHTLY-FACEAPI.regulaforensics.com/"),
    ):
        with pytest.raises(ValidationError, match="demo services"):
            KycSettings(
                identity_data_key=key,
                identity_provider="regula",
                regula_document_reader_url=document_reader,
                regula_face_url=face,
                environment="production",
            )
        # Trying it out elsewhere is fine.
        KycSettings(
            identity_data_key=key,
            identity_provider="regula",
            regula_document_reader_url=document_reader,
            regula_face_url=face,
            environment="staging",
        )
