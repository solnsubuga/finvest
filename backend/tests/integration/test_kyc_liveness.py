"""Liveness against real PostgreSQL, through the real job queue and worker and the real roles.

The test database is shared, so a worker here may also pick up jobs other tests left waiting.
Each test looks only at its own check.
"""

import asyncio
import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker, WorkerConfig
from app.infrastructure.storage import MemoryStorage, sha256_hex
from app.modules.audit.service import events_for_resource
from app.modules.kyc import liveness
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import CheckStatus, DocumentType, PhotoKind
from app.modules.kyc.domain.liveness import (
    MAX_ATTEMPTS,
    OPEN_FOR,
    Live,
    LivenessStatus,
    NotLive,
    NotStarted,
    Started,
    StillOpen,
)
from app.modules.kyc.domain.provider import (
    AutomaticCheck,
    CheckOutcome,
    ProviderDeferred,
    ProviderUncertain,
    Verdict,
)
from app.modules.kyc.liveness_jobs import LivenessJobs
from app.modules.kyc.providers import ManualProvider
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.kyc import SEALER, SHA256, FakeProvider, all_passed, live_selfie, ready_draft
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

CAPTURE = b"\xff\xd8\xff\xe0 a live face"
PAGE = "https://faces.iotec.io/auth?token=secret-token"


@dataclass
class FakeLiveness:
    """Answers like a liveness provider, as each test scripts it.

    The database is shared, so the worker may also hand it attempts other tests left waiting.
    It answers only the attempts its test registered in `mine`, and ends the others.
    """

    name: str = "fake-liveness"
    start_answer: Started | NotStarted | Exception | None = None
    results: list[StillOpen | Live | NotLive | Exception] = field(
        default_factory=lambda: [Live(CAPTURE, "image/jpeg")]
    )
    mine: set[str] = field(default_factory=set)
    started: list[str] = field(default_factory=list)
    read: list[str] = field(default_factory=list)
    _requests: set[str] = field(default_factory=set)

    async def start(self, key: str) -> Started | NotStarted:
        if key not in self.mine:
            return NotStarted("not_this_test")
        self.started.append(key)
        answer = self.start_answer or Started(str(uuid.uuid4()), PAGE)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, Started):
            self._requests.add(answer.request_id)
        return answer

    async def result(self, request_id: str) -> StillOpen | Live | NotLive:
        if request_id not in self._requests:
            return NotLive("not_this_test")
        self.read.append(request_id)
        answer = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


async def draft(database: Database, clock: FakeClock, **options: DocumentType) -> Actor:
    client = new_client()
    async with database.actor_transaction(client) as session:
        await ready_draft(session, client, clock, **options)
    return client


async def start(
    database: Database, client: Actor, clock: FakeClock, provider: FakeLiveness | None = None
) -> liveness.LivenessView:
    """The client starts an attempt. With `provider`, that provider will answer it."""
    async with database.actor_transaction(client) as session:
        view = await liveness.start(session, client, "fake-liveness", clock)
    if provider is not None:
        provider.mine.add(view.id)
    return view


async def state(database: Database, client: Actor, clock: FakeClock) -> liveness.Availability:
    async with database.actor_transaction(client) as session:
        return await liveness.availability(session, SEALER, client, clock, provider_on=True)


async def work(
    database: Database, clock: FakeClock, provider: FakeLiveness, storage: MemoryStorage
) -> None:
    """Run the worker until nothing is due."""
    handlers = LivenessJobs(database, SEALER, provider, storage, clock).handlers()
    worker = Worker(database, handlers, WorkerConfig(), clock)
    while await worker.run_once():
        pass


async def selfie(database: Database, client: Actor) -> kyc.PhotoView:
    async with database.actor_transaction(client) as session:
        view = await kyc.check_for_client(session, SEALER, client)
    assert view is not None
    return next(photo for photo in view.photos if photo.kind is PhotoKind.SELFIE)


async def selfies(database: Database, check_id: str) -> list[tuple[str, bool]]:
    """Every selfie of the check, oldest first: its source and whether it is retired."""
    async with database.actor_transaction(new_staff()) as session:
        rows = await session.execute(
            text(
                "SELECT source, replaced_at IS NOT NULL AS retired FROM kyc_photos "
                "WHERE check_id = :id AND kind = 'selfie' ORDER BY created_at, id"
            ),
            {"id": check_id},
        )
        return [(row.source, row.retired) for row in rows.all()]


async def submit(database: Database, client: Actor, clock: FakeClock) -> kyc.CheckView:
    async with database.actor_transaction(client) as session:
        return await kyc.submit(session, SEALER, client, FakeProvider(), clock)


async def decide_clean(database: Database, check_id: str, clock: FakeClock) -> kyc.CheckView:
    async with database.actor_transaction(SYSTEM) as session:
        return await kyc.record_provider_result(
            session, SEALER, check_id, 1, "ref", all_passed(), clock
        )


# The whole attempt ------------------------------------------------------------------------------


async def test_a_live_capture_becomes_the_selfie_and_the_check_passes_liveness(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client = await draft(app_database, fake_clock)
    provider = FakeLiveness(results=[StillOpen(), StillOpen(), Live(CAPTURE, "image/jpeg")])
    storage = MemoryStorage()

    attempt = await start(app_database, client, fake_clock, provider)
    assert attempt.status is LivenessStatus.STARTING
    assert attempt.url is None
    await work(worker_database, fake_clock, provider, storage)
    assert provider.started == [attempt.id]

    opened = await state(app_database, client, fake_clock)
    assert opened.latest is not None
    assert opened.latest.status is LivenessStatus.OPEN
    # The page is the client's own, and is stored sealed.
    assert opened.latest.url == PAGE
    async with worker_database.actor_transaction(SYSTEM) as session:
        sealed = (
            await session.execute(
                text("SELECT url_sealed FROM kyc_liveness WHERE id = :id"), {"id": attempt.id}
            )
        ).scalar_one()
    assert b"secret-token" not in sealed

    # Read every POLL_EVERY until the provider answers.
    for _ in range(3):
        fake_clock.advance(seconds=liveness.POLL_EVERY.total_seconds())
        await work(worker_database, fake_clock, provider, storage)
    assert len(provider.read) == 3

    done = await state(app_database, client, fake_clock)
    assert done.latest is not None
    assert done.latest.status is LivenessStatus.PASSED
    assert done.latest.photo_used
    assert done.latest.url is None
    face = await selfie(app_database, client)
    assert face.live
    assert storage.files[face.storage_key][0] == CAPTURE
    assert await selfies(app_database, attempt.check_id) == [("client", True), ("liveness", False)]

    view = await submit(app_database, client, fake_clock)
    async with worker_database.actor_transaction(SYSTEM) as session:
        request = await kyc.provider_request(session, SEALER, view.id, view.submission)
    # The identity provider compares the live capture with the document.
    assert request is not None
    assert request.photos[PhotoKind.SELFIE].storage_key == face.storage_key
    assert request.photos[PhotoKind.SELFIE].sha256 == sha256_hex(CAPTURE)
    decided = await decide_clean(worker_database, view.id, fake_clock)
    assert decided.review_flags == ("automatic_checks_passed",)
    assert CheckOutcome(AutomaticCheck.LIVENESS, Verdict.PASS) in decided.automatic_checks

    async with app_database.actor_transaction(new_staff()) as session:
        events = await events_for_resource(session, liveness.RESOURCE, attempt.id)
    assert [event.action for event in reversed(events)] == [
        "kyc.liveness.started",
        "kyc.liveness.passed",
    ]


async def test_a_camera_selfie_after_a_failed_attempt_fails_liveness_for_staff(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client = await draft(app_database, fake_clock)
    provider = FakeLiveness(results=[NotLive("not_live")])
    attempt = await start(app_database, client, fake_clock, provider)
    await work(worker_database, fake_clock, provider, MemoryStorage())
    fake_clock.advance(seconds=liveness.POLL_EVERY.total_seconds())
    await work(worker_database, fake_clock, provider, MemoryStorage())

    failed = await state(app_database, client, fake_clock)
    assert failed.latest is not None
    assert (failed.latest.status, failed.latest.failure_code) == (LivenessStatus.FAILED, "not_live")
    assert failed.problem is None  # they may try again
    assert failed.attempts_left == MAX_ATTEMPTS - 1
    assert not (await selfie(app_database, client)).live
    assert await selfies(app_database, attempt.check_id) == [("client", False)]

    view = await submit(app_database, client, fake_clock)
    decided = await decide_clean(worker_database, view.id, fake_clock)
    assert decided.status is CheckStatus.NEEDS_REVIEW
    assert decided.review_flags == ("liveness_fail",)


async def test_a_camera_selfie_with_no_attempt_shows_liveness_not_run(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client = await draft(app_database, fake_clock)
    view = await submit(app_database, client, fake_clock)
    async with worker_database.actor_transaction(SYSTEM) as session:
        decided = await kyc.record_provider_result(
            session, SEALER, view.id, 1, "ref", all_passed(), fake_clock, automatic_approval=True
        )
    # Even with approval switched on, a selfie with no liveness behind it goes to a person.
    assert decided.status is CheckStatus.NEEDS_REVIEW
    assert decided.review_flags == ("liveness_not_run",)
    # A provider's own liveness answer is ignored: it comes from the selfie handed in.
    assert all(o.check is not AutomaticCheck.LIVENESS for o in decided.automatic_checks)


async def test_a_capture_that_arrives_after_hand_in_is_not_used_and_its_file_is_deleted(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client = await draft(app_database, fake_clock)
    provider = FakeLiveness()
    storage = MemoryStorage()
    attempt = await start(app_database, client, fake_clock, provider)
    await work(worker_database, fake_clock, provider, storage)
    view = await submit(app_database, client, fake_clock)
    fake_clock.advance(seconds=liveness.POLL_EVERY.total_seconds())
    await work(worker_database, fake_clock, provider, storage)

    async with app_database.actor_transaction(client) as session:
        latest = (
            await liveness.availability(session, SEALER, client, fake_clock, provider_on=True)
        ).latest
    assert latest is not None
    assert (latest.status, latest.photo_used) == (LivenessStatus.PASSED, False)
    assert latest.failure_code == "check_moved_on"
    assert await selfies(app_database, view.id) == [("client", False)]
    assert not any(key.startswith(f"kyc/{attempt.check_id}/") for key in storage.files)


async def test_staff_asking_for_a_new_selfie_opens_liveness_again(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client = await draft(app_database, fake_clock)
    async with app_database.actor_transaction(client) as session:
        view = await kyc.submit(session, SEALER, client, ManualProvider(), fake_clock)
    with pytest.raises(ApplicationError) as error:
        await start(app_database, client, fake_clock)
    assert error.value.code == "conflict"
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        await kyc.ask_for_photos(
            session, SEALER, staff, view.id, frozenset({PhotoKind.BACK}), "Blurred", fake_clock
        )
    # Only the back was asked for, so the selfie stays as it is.
    assert (await state(app_database, client, fake_clock)).problem == "selfie_not_editable"
    with pytest.raises(ApplicationError):
        await start(app_database, client, fake_clock)


async def test_a_retake_of_the_selfie_can_be_live(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client = await draft(app_database, fake_clock)
    async with app_database.actor_transaction(client) as session:
        view = await kyc.submit(session, SEALER, client, ManualProvider(), fake_clock)
    staff = new_staff()
    async with app_database.actor_transaction(staff) as session:
        await kyc.ask_for_photos(
            session,
            SEALER,
            staff,
            view.id,
            frozenset({PhotoKind.SELFIE}),
            "Face hidden",
            fake_clock,
        )
    fake_clock.advance(seconds=1)
    await live_selfie(app_database, worker_database, client, fake_clock)
    async with app_database.actor_transaction(client) as session:
        again = await kyc.check_for_client(session, SEALER, client)
    assert again is not None
    assert again.retaken == frozenset({PhotoKind.SELFIE})


# Starting, expiring and refusing ----------------------------------------------------------------


async def test_a_new_attempt_retires_the_waiting_one(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client = await draft(app_database, fake_clock)
    first = await start(app_database, client, fake_clock)
    second = await start(app_database, client, fake_clock)
    provider = FakeLiveness(mine={first.id, second.id})
    await work(worker_database, fake_clock, provider, MemoryStorage())
    # Only the newest attempt was sent to the provider.
    assert provider.started == [second.id]
    async with worker_database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text("SELECT id, status FROM kyc_liveness WHERE check_id = :id"),
            {"id": first.check_id},
        )
        statuses = {row.id: row.status for row in rows.all()}
    assert statuses == {first.id: "expired", second.id: "open"}


async def test_an_attempt_left_open_expires_and_stops_being_read(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client = await draft(app_database, fake_clock)
    provider = FakeLiveness(results=[StillOpen()])
    await start(app_database, client, fake_clock, provider)
    await work(worker_database, fake_clock, provider, MemoryStorage())
    fake_clock.advance(seconds=OPEN_FOR.total_seconds())
    shown = await state(app_database, client, fake_clock)
    assert shown.latest is not None
    assert shown.latest.status is LivenessStatus.EXPIRED
    assert shown.latest.url is None
    reads = len(provider.read)
    for _ in range(3):
        fake_clock.advance(seconds=liveness.POLL_EVERY.total_seconds())
        await work(worker_database, fake_clock, provider, MemoryStorage())
    assert len(provider.read) == reads


async def test_a_start_the_provider_refused_or_never_confirmed_ends_as_not_started(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    for answer, code in (
        (NotStarted("iotec_refused"), "iotec_refused"),
        (ProviderUncertain(), "provider_uncertain"),
    ):
        client = await draft(app_database, fake_clock)
        provider = FakeLiveness(start_answer=answer)
        await start(app_database, client, fake_clock, provider)
        await work(worker_database, fake_clock, provider, MemoryStorage())
        latest = (await state(app_database, client, fake_clock)).latest
        assert latest is not None
        assert (latest.status, latest.failure_code) == (LivenessStatus.NOT_STARTED, code)
        # Sent once: an uncertain start is never sent again on a guess.
        assert len(provider.started) == 1


async def test_a_start_that_did_not_arrive_is_tried_again(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client = await draft(app_database, fake_clock)
    provider = FakeLiveness(start_answer=ProviderDeferred())
    attempt = await start(app_database, client, fake_clock, provider)
    await work(worker_database, fake_clock, provider, MemoryStorage())
    provider.start_answer = None
    fake_clock.advance(minutes=1)
    await work(worker_database, fake_clock, provider, MemoryStorage())
    assert provider.started == [attempt.id, attempt.id]
    latest = (await state(app_database, client, fake_clock)).latest
    assert latest is not None
    assert latest.status is LivenessStatus.OPEN


async def test_a_client_has_a_limited_number_of_attempts(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = await draft(app_database, fake_clock)
    for _ in range(MAX_ATTEMPTS):
        await start(app_database, client, fake_clock)
    shown = await state(app_database, client, fake_clock)
    assert (shown.problem, shown.attempts_left) == ("too_many_attempts", 0)
    with pytest.raises(ApplicationError) as error:
        await start(app_database, client, fake_clock)
    assert error.value.code == "validation_failed"


async def test_two_starts_racing_leave_one_waiting_attempt(
    app_database: Database, test_database: object, fake_clock: FakeClock
) -> None:
    client = await draft(app_database, fake_clock)
    barrier = AsyncBarrier(2)

    async def racer() -> str:
        await barrier.wait()
        return (await start(app_database, client, fake_clock)).id

    first, second = await asyncio.gather(racer(), racer())
    async with app_database.actor_transaction(client) as session:
        waiting = (
            await session.execute(
                text(
                    "SELECT count(*) FROM kyc_liveness WHERE id = ANY(:ids) "
                    "AND status IN ('starting', 'open')"
                ),
                {"ids": [first, second]},
            )
        ).scalar_one()
    assert waiting == 1


async def race_capture_and_hand_in(app: Database, worker: Database, clock: FakeClock) -> None:
    client = await draft(app, clock)
    provider = FakeLiveness()
    storage = MemoryStorage()
    await start(app, client, clock, provider)
    await work(worker, clock, provider, storage)
    clock.advance(seconds=liveness.POLL_EVERY.total_seconds())
    barrier = AsyncBarrier(2)

    async def hand_in() -> kyc.CheckView:
        await barrier.wait()
        return await submit(app, client, clock)

    async def capture() -> None:
        await barrier.wait()
        await work(worker, clock, provider, storage)

    view, _ = await asyncio.gather(hand_in(), capture())
    async with worker.actor_transaction(SYSTEM) as session:
        request = await kyc.provider_request(session, SEALER, view.id, view.submission)
    assert request is not None
    sent = request.photos[PhotoKind.SELFIE]
    # Whichever came first, what was handed in is what the provider is sent.
    current = await selfies(app, view.id)
    assert current[-1][1] is False
    assert (sent.sha256 == sha256_hex(CAPTURE)) == (current[-1][0] == "liveness")
    assert sent.sha256 in {SHA256, sha256_hex(CAPTURE)}


async def test_a_capture_racing_a_hand_in_never_changes_a_handed_in_selfie(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    for _ in range(3):
        await race_capture_and_hand_in(app_database, worker_database, fake_clock)


# What the database refuses ------------------------------------------------------------------


async def test_a_client_cannot_add_a_live_selfie_or_decide_an_attempt(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = await draft(app_database, fake_clock)
    attempt = await start(app_database, client, fake_clock)
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text(
                    "INSERT INTO kyc_photos (id, check_id, kind, storage_key, content_type, "
                    "byte_size, sha256, source, created_at) VALUES ('-Pfake000000000000001', "
                    ":check_id, 'selfie', 'kyc/x/y', 'image/jpeg', 10, :sha, 'liveness', now())"
                ),
                {"check_id": attempt.check_id, "sha": SHA256},
            )
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(client) as session:
            await session.execute(
                text(
                    "UPDATE kyc_liveness SET status = 'passed', finished_at = now() WHERE id = :id"
                ),
                {"id": attempt.id},
            )


async def test_the_worker_adds_only_live_selfies_and_a_finished_attempt_never_changes(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client = await draft(app_database, fake_clock)
    attempt_id = await live_selfie(app_database, worker_database, client, fake_clock)
    face = await selfie(app_database, client)
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "INSERT INTO kyc_photos (id, check_id, kind, storage_key, content_type, "
                    "byte_size, sha256, source, created_at) VALUES ('-Pfake000000000000002', "
                    ":check_id, 'front', 'kyc/x/z', 'image/jpeg', 10, :sha, 'client', now())"
                ),
                {"check_id": face.storage_key.split("/")[1], "sha": SHA256},
            )
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE kyc_liveness SET status = 'failed' WHERE id = :id"),
                {"id": attempt_id},
            )


async def test_the_api_answers_unavailable_without_a_provider_and_staff_cannot_start(
    app_database: Database, fake_clock: FakeClock
) -> None:
    client = await draft(app_database, fake_clock)
    async with app_database.actor_transaction(client) as session:
        shown = await liveness.availability(session, SEALER, client, fake_clock, provider_on=False)
    assert (shown.available, shown.problem, shown.latest) == (False, None, None)
    staff = new_staff()
    with pytest.raises(ApplicationError) as error:
        async with app_database.actor_transaction(staff) as session:
            await liveness.start(session, staff, "fake-liveness", fake_clock)
    assert error.value.code == "forbidden"


async def test_a_passport_takes_a_live_selfie_too(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client = await draft(app_database, fake_clock, document_type=DocumentType.PASSPORT)
    await live_selfie(app_database, worker_database, client, fake_clock)
    assert (await selfie(app_database, client)).live
