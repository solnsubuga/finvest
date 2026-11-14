"""Face confirmation rules, and the ioTec face match adapter against a fake ioTec."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from app.infrastructure.storage import MemoryStorage, NewFile
from app.modules.kyc.domain.face_confirmations import (
    COUNTS_FOR,
    MAX_PER_DAY,
    OPEN_FOR,
    WAITING,
    ConfirmationStatus,
    MatchAnswer,
    MatchNotRun,
    MatchPending,
    MatchRefused,
    MatchSent,
    counts,
    match_passes,
    shown_status,
    start_problem,
)
from app.modules.kyc.domain.provider import PhotoRef, ProviderDeferred, ProviderUncertain
from app.modules.kyc.iotec_face_match import IotecFaceMatcher

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
FACE = "22222222-2222-4222-8222-222222222222"

# The rules -----------------------------------------------------------------------------------


@pytest.mark.parametrize("status", list(ConfirmationStatus))
def test_only_a_waiting_attempt_expires(status: ConfirmationStatus) -> None:
    late = NOW - OPEN_FOR
    expected = ConfirmationStatus.EXPIRED if status in WAITING else status
    assert shown_status(status, late, NOW) is expected
    assert shown_status(status, NOW - OPEN_FOR + timedelta(seconds=1), NOW) is status


@pytest.mark.parametrize("status", list(ConfirmationStatus))
def test_only_a_pass_confirms_a_change(status: ConfirmationStatus) -> None:
    assert counts(status, NOW, NOW) is (status is ConfirmationStatus.PASSED)


def test_a_pass_counts_for_ten_minutes_and_never_from_the_future() -> None:
    passed = ConfirmationStatus.PASSED
    assert counts(passed, NOW - COUNTS_FOR + timedelta(seconds=1), NOW)
    assert not counts(passed, NOW - COUNTS_FOR, NOW)
    assert not counts(passed, NOW + timedelta(seconds=1), NOW)
    assert not counts(passed, None, NOW)


@pytest.mark.parametrize(
    ("has_reference", "started_today", "problem"),
    [
        (True, 0, None),
        (True, MAX_PER_DAY - 1, None),
        (True, MAX_PER_DAY, "too_many_attempts"),
        (False, 0, "no_selfie_on_file"),
        (False, MAX_PER_DAY, "no_selfie_on_file"),
    ],
)
def test_when_an_attempt_may_start(
    has_reference: bool, started_today: int, problem: str | None
) -> None:
    assert start_problem(has_reference=has_reference, started_today=started_today) == problem


@pytest.mark.parametrize(
    ("score", "identical", "passes"),
    [
        (80, True, True),
        (100, True, True),
        (79, True, False),
        (95, False, False),
        (None, True, False),
        (0, False, False),
    ],
)
def test_a_match_passes_only_when_identical_and_high_enough(
    score: int | None, identical: bool, passes: bool
) -> None:
    assert match_passes(score, identical) is passes


# The adapter ---------------------------------------------------------------------------------

Handler = Callable[[httpx.Request], httpx.Response]


class FakeIotec:
    def __init__(self) -> None:
        self.sent: list[httpx.Request] = []
        self.post: Handler = lambda _: httpx.Response(200, json={"id": FACE, "status": "Pending"})
        self.read: dict[str, object] = {
            "status": "Completed",
            "faceMatchStatus": "Success",
            "isIdentical": True,
            "confidence": 0.912,
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        assert request.headers["X-Api-Key"] == "IO-key"
        if request.method == "POST":
            assert request.url.path == "/api/face-match"
            return self.post(request)
        assert request.url.path == f"/api/face-match/{FACE}"
        return httpx.Response(200, json=self.read)


async def matcher(fake: FakeIotec) -> tuple[IotecFaceMatcher, PhotoRef, PhotoRef]:
    storage = MemoryStorage()
    live = await storage.put(NewFile("kyc/c/face-confirmations/a", b"\xff\xd8 live", "image/jpeg"))
    on_file = await storage.put(NewFile("kyc/c/p", b"\xff\xd8 on file", "image/jpeg"))
    adapter = IotecFaceMatcher(
        client_id="client-1",
        api_key=SecretStr("IO-key"),
        storage=storage,
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
        retry_pause=0,
    )
    return (
        adapter,
        PhotoRef(live.key, live.sha256, "image/jpeg"),
        PhotoRef(on_file.key, on_file.sha256, "image/jpeg"),
    )


async def test_both_photos_are_sent_with_the_attempt_as_reference_and_read_back() -> None:
    fake = FakeIotec()
    adapter, live, on_file = await matcher(fake)
    assert await adapter.send("attempt-1", live, on_file) == MatchSent(FACE)
    body = fake.sent[0].content
    assert b"attempt-1" in body and b"live" in body and b"on file" in body
    assert await adapter.result(FACE) == MatchAnswer(score=91, identical=True)


async def test_a_waiting_match_is_pending_and_an_ended_one_was_not_run() -> None:
    fake = FakeIotec()
    adapter, _, _ = await matcher(fake)
    fake.read = {"status": "Processing"}
    assert await adapter.result(FACE) == MatchPending()
    fake.read = {"status": "Failed", "faceMatchStatus": "Failed"}
    assert await adapter.result(FACE) == MatchNotRun("match_not_run")


async def test_a_confidence_outside_zero_to_one_has_no_score() -> None:
    fake = FakeIotec()
    adapter, _, _ = await matcher(fake)
    fake.read = {
        "status": "Completed",
        "faceMatchStatus": "Success",
        "isIdentical": True,
        "confidence": 93,
    }
    answer = await adapter.result(FACE)
    assert answer == MatchAnswer(score=None, identical=True)


async def test_a_refusal_a_timeout_and_no_connection_are_told_apart() -> None:
    fake = FakeIotec()
    adapter, live, on_file = await matcher(fake)
    fake.post = lambda _: httpx.Response(400, json={"title": "bad photo"})
    assert await adapter.send("a", live, on_file) == MatchRefused("iotec_refused")

    def timed_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no answer", request=request)

    fake.post = timed_out
    with pytest.raises(ProviderUncertain):
        await adapter.send("a", live, on_file)

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    fake.post = unreachable
    with pytest.raises(ProviderDeferred):
        await adapter.send("a", live, on_file)


async def test_an_answer_it_does_not_know_is_never_a_pass() -> None:
    fake = FakeIotec()
    adapter, _, _ = await matcher(fake)
    fake.read = {}
    assert await adapter.result(FACE) == MatchNotRun("match_not_run")
    with pytest.raises(LookupError):
        await adapter.result("not-an-id")


async def test_a_photo_missing_from_storage_sends_nothing() -> None:
    fake = FakeIotec()
    adapter, live, _ = await matcher(fake)
    missing = PhotoRef("kyc/c/none", "ab" * 32, "image/jpeg")
    with pytest.raises(ProviderDeferred):
        await adapter.send("a", live, missing)
    assert fake.sent == []
