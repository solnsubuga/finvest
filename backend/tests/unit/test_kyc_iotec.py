"""The ioTec Verify adapter against a fake ioTec: accepted, refused, timed out and unsure."""

import json
from collections.abc import Callable
from datetime import date

import httpx
import pytest
from pydantic import SecretStr, ValidationError

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
    ProviderUncertain,
    Refused,
    Verdict,
    decide,
)
from app.modules.kyc.iotec import IotecVerifyProvider
from app.modules.kyc.settings import KycSettings

IDV = "11111111-1111-4111-8111-111111111111"
FACE = "22222222-2222-4222-8222-222222222222"
AML = "33333333-3333-4333-8333-333333333333"
KEY = "check:1"
SELFIE = b"\xff\xd8\xff selfie"
FRONT = b"\xff\xd8\xff front"

Handler = Callable[[httpx.Request], httpx.Response]


class FakeIotec:
    """Answers like ioTec Verify. Each test changes what one path says."""

    def __init__(self) -> None:
        self.sent: list[httpx.Request] = []
        self.posts: dict[str, Handler] = {
            "/api/idv/verify-person-information": lambda _: accepted(IDV),
            "/api/face-match": lambda _: accepted(FACE),
            "/api/aml-screening": lambda _: accepted(AML),
        }
        self.reads: dict[str, dict[str, object]] = {
            IDV: {"status": "Completed", "matchingStatus": "True", "cardStatus": "Valid"},
            FACE: {
                "status": "Completed",
                "faceMatchStatus": "Success",
                "isIdentical": True,
                "confidence": 0.934,
            },
            AML: {"status": "Completed", "verificationStatus": "NoHits", "totalHits": 0},
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        assert request.headers["Client-Id"] == "client-1"
        assert request.headers["X-Api-Key"] == "IO-key"
        if request.method == "POST":
            return self.posts[request.url.path](request)
        return httpx.Response(200, json=self.reads[request.url.path.rsplit("/", 1)[1]])


def accepted(request_id: str) -> httpx.Response:
    return httpx.Response(200, json={"id": request_id, "status": "Pending"})


def timed_out(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("no answer", request=request)


def unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


async def provider(fake: FakeIotec, storage: MemoryStorage | None = None) -> IotecVerifyProvider:
    return IotecVerifyProvider(
        client_id="client-1",
        api_key=SecretStr("IO-key"),
        storage=storage or MemoryStorage(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
        retry_pause=0,
    )


async def request(
    storage: MemoryStorage, document_type: DocumentType = DocumentType.NATIONAL_ID
) -> ProviderRequest:
    photos = {}
    for kind, data in ((PhotoKind.SELFIE, SELFIE), (PhotoKind.FRONT, FRONT)):
        stored = await storage.put(NewFile(f"kyc/check/{kind.value}", data, "image/jpeg"))
        photos[kind] = PhotoRef(stored.key, stored.sha256, "image/jpeg")
    return ProviderRequest(
        idempotency_key=KEY,
        document_type=document_type,
        document_country="UG",
        details=IdentityDetails(
            surname="NAKATO",
            given_names="Amara",
            document_number="CM94062210ABCD",
            date_of_birth=date(1994, 6, 22),
            document_expires_on=date(2030, 9, 18),
        ),
        photos=photos,
    )


async def submitted(
    fake: FakeIotec, document_type: DocumentType = DocumentType.NATIONAL_ID
) -> tuple[IotecVerifyProvider, Accepted | Refused]:
    storage = MemoryStorage()
    adapter = await provider(fake, storage)
    return adapter, await adapter.submit(await request(storage, document_type))


def verdicts(result: Pending | Completed) -> dict[AutomaticCheck, Verdict]:
    assert isinstance(result, Completed)
    return {outcome.check: outcome.verdict for outcome in result.outcomes}


async def test_a_clean_national_id_passes_all_three_checks_and_still_goes_to_a_person() -> None:
    fake = FakeIotec()
    adapter, answer = await submitted(fake)
    assert answer == Accepted(f"iotec:idv={IDV},face={FACE},aml={AML}")
    idv, face, aml = (
        json.loads(r.content) if r.url.path != "/api/face-match" else r for r in fake.sent
    )
    assert isinstance(idv, dict) and isinstance(aml, dict)
    assert idv == {
        "referenceId": KEY,
        "isBlocking": False,
        "captureMethod": "FormDataCapture",
        "nationalId": "CM94062210ABCD",
        "surname": "NAKATO",
        "givenNames": "Amara",
        "dateOfBirth": "1994-06-22",
        "dateOfExpiration": "2030-09-18",
    }
    assert (aml["category"], aml["names"], aml["dateOfBirth"]) == (
        "Person",
        "Amara NAKATO",
        "1994-06-22",
    )
    assert isinstance(face, httpx.Request)
    assert SELFIE in face.content and FRONT in face.content

    result = await adapter.result(answer.reference)
    assert isinstance(result, Completed)
    assert set(result.outcomes) == {
        CheckOutcome(AutomaticCheck.DOCUMENT_GENUINE, Verdict.PASS),
        CheckOutcome(AutomaticCheck.SELFIE_MATCH, Verdict.PASS, 93),
        CheckOutcome(AutomaticCheck.SCREENING, Verdict.PASS),
    }
    assert decide(result.outcomes).flags == ("automatic_checks_passed",)


async def test_a_result_still_running_is_pending() -> None:
    fake = FakeIotec()
    fake.reads[FACE] = {"status": "Processing", "faceMatchStatus": "Processing"}
    adapter, answer = await submitted(fake)
    assert isinstance(answer, Accepted)
    assert isinstance(await adapter.result(answer.reference), Pending)


async def test_a_list_hit_a_mismatch_and_a_weak_face_match_go_to_a_person_with_reasons() -> None:
    fake = FakeIotec()
    fake.reads[IDV] = {"status": "Completed", "matchingStatus": "False", "cardStatus": "Valid"}
    fake.reads[FACE] = {
        "status": "Completed",
        "faceMatchStatus": "Success",
        "isIdentical": True,
        "confidence": 0.61,
    }
    fake.reads[AML] = {"status": "Completed", "verificationStatus": "HasHits", "totalHits": 2}
    adapter, answer = await submitted(fake)
    assert isinstance(answer, Accepted)
    result = await adapter.result(answer.reference)
    assert isinstance(result, Completed)
    assert verdicts(result) == {
        AutomaticCheck.DOCUMENT_GENUINE: Verdict.FAIL,
        AutomaticCheck.SELFIE_MATCH: Verdict.UNSURE,
        AutomaticCheck.SCREENING: Verdict.FAIL,
    }
    assert set(decide(result.outcomes, automatic_approval=True).flags) == {
        "document_genuine_fail",
        "selfie_match_unsure",
        "screening_fail",
    }


async def test_an_answer_it_does_not_recognise_is_unsure_never_a_pass() -> None:
    fake = FakeIotec()
    fake.reads[IDV] = {"status": "Completed", "matchingStatus": "Partial", "cardStatus": "Valid"}
    fake.reads[FACE] = {"status": "Completed", "faceMatchStatus": "Success", "isIdentical": True}
    adapter, answer = await submitted(fake)
    assert isinstance(answer, Accepted)
    found = verdicts(await adapter.result(answer.reference))
    assert found[AutomaticCheck.DOCUMENT_GENUINE] is Verdict.UNSURE
    assert found[AutomaticCheck.SELFIE_MATCH] is Verdict.UNSURE


async def test_a_check_that_failed_at_iotec_shows_as_not_run() -> None:
    fake = FakeIotec()
    fake.reads[AML] = {"status": "Failed", "verificationStatus": "Failed"}
    adapter, answer = await submitted(fake)
    assert isinstance(answer, Accepted)
    result = await adapter.result(answer.reference)
    assert AutomaticCheck.SCREENING not in verdicts(result)
    assert isinstance(result, Completed)
    assert "screening_not_run" in decide(result.outcomes).flags


async def test_a_passport_is_not_looked_up_at_nira() -> None:
    fake = FakeIotec()
    adapter, answer = await submitted(fake, DocumentType.PASSPORT)
    assert answer == Accepted(f"iotec:idv=-,face={FACE},aml={AML}")
    assert "/api/idv/verify-person-information" not in [r.url.path for r in fake.sent]
    result = await adapter.result(answer.reference)
    assert isinstance(result, Completed)
    assert "document_genuine_not_run" in decide(result.outcomes).flags


async def test_one_check_refused_leaves_it_out_and_keeps_the_others() -> None:
    fake = FakeIotec()
    fake.posts["/api/face-match"] = lambda _: httpx.Response(400, json={"statusCode": 400})
    _, answer = await submitted(fake)
    assert answer == Accepted(f"iotec:idv={IDV},face=-,aml={AML}")


async def test_every_check_refused_is_refused() -> None:
    fake = FakeIotec()
    for path in fake.posts:
        fake.posts[path] = lambda _: httpx.Response(400, json={"statusCode": 400})
    _, answer = await submitted(fake)
    assert answer == Refused("iotec_refused")


async def test_nothing_received_is_safe_to_send_again() -> None:
    fake = FakeIotec()
    for path in fake.posts:
        fake.posts[path] = unreachable
    with pytest.raises(ProviderDeferred):
        await submitted(fake)
    # Wrong credentials and too many requests are refused before anything runs.
    for status in (401, 429):
        for path in fake.posts:
            fake.posts[path] = lambda _, status=status: httpx.Response(status)  # type: ignore[misc]
        with pytest.raises(ProviderDeferred):
            await submitted(fake)


async def test_a_timeout_with_nothing_accepted_is_uncertain_and_never_found() -> None:
    fake = FakeIotec()
    fake.posts["/api/idv/verify-person-information"] = timed_out
    fake.posts["/api/face-match"] = lambda _: httpx.Response(502)
    fake.posts["/api/aml-screening"] = unreachable
    adapter, _ = await submitted(FakeIotec())
    with pytest.raises(ProviderUncertain):
        await submitted(fake)
    # ioTec cannot be asked by our key, so the worker gives the check to a person.
    assert await adapter.find(KEY) is None


async def test_a_timeout_after_one_was_accepted_leaves_that_check_out() -> None:
    fake = FakeIotec()
    fake.posts["/api/aml-screening"] = timed_out
    _, answer = await submitted(fake)
    assert answer == Accepted(f"iotec:idv={IDV},face={FACE},aml=-")


async def test_a_photo_that_cannot_be_read_sends_nothing() -> None:
    fake = FakeIotec()
    adapter = await provider(fake, MemoryStorage())
    with pytest.raises(ProviderDeferred):
        await adapter.submit(await request(MemoryStorage()))
    assert fake.sent == []


async def test_reading_when_iotec_is_down_is_tried_again_later() -> None:
    fake = FakeIotec()
    adapter, answer = await submitted(fake)
    assert isinstance(answer, Accepted)
    broken = await provider(FakeIotec())
    broken._client = httpx.AsyncClient(transport=httpx.MockTransport(timed_out))
    with pytest.raises(ProviderDeferred):
        await broken.result(answer.reference)
    with pytest.raises(LookupError):
        await adapter.result("stub:pass:check:1")


def test_iotec_needs_its_credentials_and_never_approves_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "A" * 43
    with pytest.raises(ValidationError, match="FINVEST_IOTEC_VERIFY_CLIENT_ID"):
        KycSettings(identity_data_key=key, identity_provider="iotec")
    with pytest.raises(ValidationError, match="does not check liveness"):
        KycSettings(
            identity_data_key=key,
            identity_provider="iotec",
            iotec_verify_client_id="client-1",
            iotec_verify_api_key="IO-key",
            identity_automatic_approval=True,
        )
    settings = KycSettings(
        identity_data_key=key,
        identity_provider="iotec",
        iotec_verify_client_id="client-1",
        iotec_verify_api_key="IO-key",
    )
    assert isinstance(settings.provider(MemoryStorage()), IotecVerifyProvider)
    assert "IO-key" not in repr(settings)


async def test_a_request_that_never_arrived_is_tried_once_more() -> None:
    fake = FakeIotec()
    tries: list[int] = []

    def once_down(request: httpx.Request) -> httpx.Response:
        tries.append(1)
        return unreachable(request) if len(tries) == 1 else accepted(AML)

    fake.posts["/api/aml-screening"] = once_down
    _, answer = await submitted(fake)
    assert answer == Accepted(f"iotec:idv={IDV},face={FACE},aml={AML}")
    assert len(tries) == 2
    # Never again after it may have arrived.
    tries.clear()

    def counted_timeout(request: httpx.Request) -> httpx.Response:
        tries.append(1)
        return timed_out(request)

    fake.posts["/api/aml-screening"] = counted_timeout
    _, answer = await submitted(fake)
    assert answer == Accepted(f"iotec:idv={IDV},face={FACE},aml=-")
    assert len(tries) == 1


@pytest.mark.parametrize("confidence", [1.4, -0.2, 93, "0.9"])
async def test_a_confidence_outside_zero_to_one_is_unsure_with_no_score(confidence: object) -> None:
    fake = FakeIotec()
    fake.reads[FACE] = {
        "status": "Completed",
        "faceMatchStatus": "Success",
        "isIdentical": True,
        "confidence": confidence,
    }
    adapter, answer = await submitted(fake)
    assert isinstance(answer, Accepted)
    result = await adapter.result(answer.reference)
    assert isinstance(result, Completed)
    face = next(o for o in result.outcomes if o.check is AutomaticCheck.SELFIE_MATCH)
    assert (face.verdict, face.score) == (Verdict.UNSURE, None)


def test_iotec_is_only_reached_over_https() -> None:
    with pytest.raises(ValueError, match="https"):
        IotecVerifyProvider(
            client_id="client-1",
            api_key=SecretStr("IO-key"),
            storage=MemoryStorage(),
            base_url="http://verify-api.iotec.io",
        )
    with pytest.raises(ValidationError, match="https"):
        KycSettings(
            identity_data_key="A" * 43,
            identity_provider="iotec",
            iotec_verify_client_id="client-1",
            iotec_verify_api_key="IO-key",
            iotec_verify_base_url="http://verify-api.iotec.io",
        )
