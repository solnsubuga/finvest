"""Face confirmations against real PostgreSQL, through the real job queue, worker and roles.

The test database is shared, so a worker here may also pick up jobs other tests left waiting.
The fakes answer only the attempts their own test registered.
"""

import asyncio
import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker, WorkerConfig
from app.infrastructure.storage import MemoryStorage
from app.modules.audit.service import events_for_resource
from app.modules.kyc import claims
from app.modules.kyc import face_confirmations as confirmations
from app.modules.kyc.domain.checks import PhotoKind
from app.modules.kyc.domain.face_confirmations import (
    COUNTS_FOR,
    MAX_PER_DAY,
    OPEN_FOR,
    ConfirmationStatus,
    MatchAnswer,
    MatchNotRun,
    MatchPending,
    MatchRefused,
    MatchSent,
)
from app.modules.kyc.domain.liveness import Live, NotLive, NotStarted, Started, StillOpen
from app.modules.kyc.domain.provider import PhotoRef, ProviderUncertain
from app.modules.kyc.face_confirmation_jobs import FaceConfirmationJobs
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.integration.test_kyc_claims import carried_over
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.kyc import SEALER, SHA256, verify
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

CAPTURE = b"\xff\xd8\xff\xe0 a live face"
PAGE = "https://faces.iotec.io/auth?token=secret-token"


@dataclass
class FakeLiveness:
    name: str = "fake-liveness"
    start_answer: Started | NotStarted | Exception | None = None
    results: list[StillOpen | Live | NotLive] = field(
        default_factory=lambda: [Live(CAPTURE, "image/jpeg")]
    )
    mine: set[str] = field(default_factory=set)
    _requests: set[str] = field(default_factory=set)

    async def start(self, key: str) -> Started | NotStarted:
        if key not in self.mine:
            return NotStarted("not_this_test")
        answer = self.start_answer or Started(str(uuid.uuid4()), PAGE)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, Started):
            self._requests.add(answer.request_id)
        return answer

    async def result(self, request_id: str) -> StillOpen | Live | NotLive:
        if request_id not in self._requests:
            return NotLive("not_this_test")
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


@dataclass
class FakeMatcher:
    name: str = "fake-matcher"
    send_answer: MatchSent | MatchRefused | Exception | None = None
    results: list[MatchPending | MatchAnswer | MatchNotRun] = field(
        default_factory=lambda: [MatchAnswer(score=93, identical=True)]
    )
    mine: set[str] = field(default_factory=set)
    sent: list[tuple[str, PhotoRef, PhotoRef]] = field(default_factory=list)
    _requests: set[str] = field(default_factory=set)

    async def send(self, key: str, live: PhotoRef, on_file: PhotoRef) -> MatchSent | MatchRefused:
        if key not in self.mine:
            return MatchRefused("not_this_test")
        self.sent.append((key, live, on_file))
        answer = self.send_answer or MatchSent(str(uuid.uuid4()))
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, MatchSent):
            self._requests.add(answer.request_id)
        return answer

    async def result(self, request_id: str) -> MatchPending | MatchAnswer | MatchNotRun:
        if request_id not in self._requests:
            return MatchNotRun("not_this_test")
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


@dataclass
class Setup:
    app: Database
    worker: Database
    clock: FakeClock
    liveness: FakeLiveness
    matcher: FakeMatcher
    storage: MemoryStorage

    async def client(self, *, verified: bool = True) -> Actor:
        client = new_client()
        if verified:
            await verify(self.app, client, self.clock)
        return client

    async def start(self, client: Actor, session_id: str) -> confirmations.ConfirmationView:
        async with self.app.actor_transaction(client) as session:
            view = await confirmations.start(
                session, client, session_id, "fake-liveness", "fake-matcher", self.clock
            )
        self.liveness.mine.add(view.id)
        self.matcher.mine.add(view.id)
        return view

    async def work(self) -> None:
        handlers = FaceConfirmationJobs(
            self.worker, SEALER, self.liveness, self.matcher, self.storage, self.clock
        ).handlers()
        worker = Worker(self.worker, handlers, WorkerConfig(), self.clock)
        while await worker.run_once():
            pass

    async def settle(self) -> None:
        """Run the worker, then step the clock past each poll until nothing is waiting."""
        for _ in range(6):
            await self.work()
            self.clock.advance(seconds=confirmations.POLL_EVERY.total_seconds())
        await self.work()

    async def view(self, client: Actor, attempt_id: str) -> confirmations.ConfirmationView:
        async with self.app.actor_transaction(client) as session:
            return await confirmations.attempt(session, SEALER, client, attempt_id, self.clock)

    async def confirmed(self, client: Actor, session_id: str) -> bool:
        async with self.app.actor_transaction(client) as session:
            return await confirmations.confirmed(session, str(client.id), session_id, self.clock)

    async def needed(self, client: Actor, *, on: bool = True) -> bool:
        async with self.app.actor_transaction(client) as session:
            return await confirmations.needed(session, str(client.id), on=on)


@pytest.fixture
def setup(app_database: Database, worker_database: Database) -> Setup:
    return Setup(
        app_database, worker_database, FakeClock(), FakeLiveness(), FakeMatcher(), MemoryStorage()
    )


async def test_a_live_matching_face_confirms_this_session_for_ten_minutes(setup: Setup) -> None:
    client = await setup.client()
    here, elsewhere = push_id(), push_id()
    assert await setup.needed(client)
    assert not await setup.needed(client, on=False)
    view = await setup.start(client, here)
    assert view.status is ConfirmationStatus.STARTING

    await setup.work()
    opened = await setup.view(client, view.id)
    assert opened.status is ConfirmationStatus.OPEN and opened.url == PAGE

    await setup.settle()
    passed = await setup.view(client, view.id)
    assert passed.status is ConfirmationStatus.PASSED and passed.url is None
    assert passed.counts_until is not None
    assert await setup.confirmed(client, here)
    assert not await setup.confirmed(client, elsewhere)

    # The capture was compared with the selfie on the approved check, and is kept.
    (key, live, on_file) = setup.matcher.sent[0]
    assert key == view.id
    assert live.storage_key.endswith(f"/face-confirmations/{view.id}")
    assert await setup.storage.read(live.storage_key, sha256=live.sha256) == CAPTURE
    assert on_file.storage_key != live.storage_key

    setup.clock.advance(seconds=COUNTS_FOR.total_seconds())
    assert not await setup.confirmed(client, here)
    async with setup.app.actor_transaction(new_staff()) as session:
        events = await events_for_resource(session, confirmations.RESOURCE, view.id)
    actions = [event.action for event in reversed(events)]
    assert actions == ["kyc.face_confirmation.started", "kyc.face_confirmation.passed"]


async def test_a_face_that_does_not_match_fails_and_confirms_nothing(setup: Setup) -> None:
    client = await setup.client()
    here = push_id()
    setup.matcher.results = [MatchPending(), MatchAnswer(score=91, identical=False)]
    view = await setup.start(client, here)
    await setup.settle()
    failed = await setup.view(client, view.id)
    assert failed.status is ConfirmationStatus.FAILED
    assert failed.failure_code == "face_not_matched"
    assert not await setup.confirmed(client, here)


async def test_a_low_score_fails_even_when_called_identical(setup: Setup) -> None:
    client = await setup.client()
    setup.matcher.results = [MatchAnswer(score=79, identical=True)]
    view = await setup.start(client, push_id())
    await setup.settle()
    assert (await setup.view(client, view.id)).status is ConfirmationStatus.FAILED


async def test_no_live_person_fails_before_any_face_match(setup: Setup) -> None:
    client = await setup.client()
    setup.liveness.results = [StillOpen(), NotLive("not_live")]
    view = await setup.start(client, push_id())
    await setup.settle()
    failed = await setup.view(client, view.id)
    assert failed.status is ConfirmationStatus.FAILED and failed.failure_code == "not_live"
    assert setup.matcher.sent == []


async def test_an_uncertain_face_match_ends_the_attempt_and_is_never_sent_again(
    setup: Setup,
) -> None:
    client = await setup.client()
    setup.matcher.send_answer = ProviderUncertain("may hold it")
    view = await setup.start(client, push_id())
    await setup.settle()
    failed = await setup.view(client, view.id)
    assert failed.status is ConfirmationStatus.FAILED
    assert failed.failure_code == "provider_uncertain"
    assert len(setup.matcher.sent) == 1


async def test_a_client_with_no_selfie_on_file_is_not_asked_and_cannot_start(
    setup: Setup,
) -> None:
    client = await setup.client(verified=False)
    assert not await setup.needed(client)
    with pytest.raises(ApplicationError) as refused:
        await setup.start(client, push_id())
    assert refused.value.fields[0].code == "no_selfie_on_file"


async def test_a_new_attempt_retires_the_waiting_one_and_a_day_allows_five(
    setup: Setup,
) -> None:
    client = await setup.client()
    here = push_id()
    first = await setup.start(client, here)
    second = await setup.start(client, here)
    assert (await setup.view(client, first.id)).status is ConfirmationStatus.EXPIRED
    assert (await setup.view(client, second.id)).status is ConfirmationStatus.STARTING
    for _ in range(MAX_PER_DAY - 2):
        await setup.start(client, here)
    with pytest.raises(ApplicationError) as refused:
        await setup.start(client, here)
    assert refused.value.fields[0].code == "too_many_attempts"
    setup.clock.advance(hours=24)
    await setup.start(client, here)


async def test_starts_at_the_same_moment_take_turns(setup: Setup) -> None:
    client = await setup.client()
    here = push_id()
    barrier = AsyncBarrier(2)

    async def start() -> str:
        async with setup.app.actor_transaction(client) as session:
            await barrier.wait()
            view = await confirmations.start(
                session, client, here, "fake-liveness", "fake-matcher", setup.clock
            )
            return view.id

    ids = await asyncio.gather(start(), start())
    async with setup.app.actor_transaction(client) as session:
        waiting = (
            (
                await session.execute(
                    text(
                        "SELECT id FROM kyc_face_confirmations WHERE client_id = :id "
                        "AND status IN ('starting', 'open', 'matching')"
                    ),
                    {"id": client.id},
                )
            )
            .scalars()
            .all()
        )
    assert len(waiting) == 1 and waiting[0] in ids


async def test_an_attempt_left_too_long_expires(setup: Setup) -> None:
    client = await setup.client()
    setup.liveness.results = [StillOpen()]
    view = await setup.start(client, push_id())
    await setup.work()
    setup.clock.advance(seconds=OPEN_FOR.total_seconds())
    assert (await setup.view(client, view.id)).status is ConfirmationStatus.EXPIRED
    await setup.settle()
    assert setup.matcher.sent == []


async def test_only_the_worker_records_an_answer(setup: Setup) -> None:
    client = await setup.client()
    view = await setup.start(client, push_id())
    with pytest.raises(DBAPIError):
        async with setup.app.actor_transaction(client) as session:
            await session.execute(
                text(
                    "UPDATE kyc_face_confirmations SET status = 'passed', finished_at = now() "
                    "WHERE id = :id"
                ),
                {"id": view.id},
            )
    with pytest.raises(DBAPIError):
        async with setup.app.actor_transaction(client) as session:
            await session.execute(
                text("UPDATE kyc_face_confirmations SET score = 99 WHERE id = :id"),
                {"id": view.id},
            )
    other = new_client()
    with pytest.raises(ApplicationError):
        await setup.view(other, view.id)


async def test_a_finished_attempt_never_changes(setup: Setup) -> None:
    client = await setup.client()
    view = await setup.start(client, push_id())
    await setup.settle()
    with pytest.raises(DBAPIError):
        async with setup.worker.transaction() as session:
            await session.execute(text("SELECT set_config('finvest.actor_kind', 'system', true)"))
            await session.execute(
                text("UPDATE kyc_face_confirmations SET status = 'failed' WHERE id = :id"),
                {"id": view.id},
            )


async def test_a_carried_over_client_is_compared_with_their_matched_claim_selfie(
    setup: Setup,
) -> None:
    client, _ = await carried_over(setup.worker, setup.clock)
    # Carried over with no photos, and no claim matched yet: nothing to compare with.
    assert not await setup.needed(client)
    async with setup.app.actor_transaction(client) as session:
        await claims.start(session, client, setup.clock)
        for kind in (PhotoKind.FRONT, PhotoKind.SELFIE):
            await claims.add_photo(
                session,
                client,
                kind,
                content_type="image/jpeg",
                byte_size=2048,
                sha256=SHA256,
                clock=setup.clock,
            )
        claim = await claims.submit(session, client, setup.clock)
    assert not await setup.needed(client)
    staff = new_staff()
    async with setup.app.actor_transaction(staff) as session:
        await claims.decide(
            session, staff, claim.id, matched=True, reason="Same face as the ID", clock=setup.clock
        )
    assert await setup.needed(client)

    here = push_id()
    view = await setup.start(client, here)
    await setup.settle()
    assert (await setup.view(client, view.id)).status is ConfirmationStatus.PASSED
    (_, _, on_file) = setup.matcher.sent[0]
    assert on_file.storage_key.startswith(f"kyc-claims/{claim.id}/")
    assert await setup.confirmed(client, here)
