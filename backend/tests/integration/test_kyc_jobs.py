"""Identity checks and the provider, through the real job queue and worker.

The test database is shared, so a worker here may also pick up submissions other tests left
waiting. Each test looks only at its own check.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, WorkerConfig, enqueue
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import CheckStatus
from app.modules.kyc.domain.provider import (
    Accepted,
    Pending,
    ProviderDeferred,
    ProviderRequest,
    ProviderUncertain,
    Refused,
)
from app.modules.kyc.jobs import ProviderJobs, sweep_unanswered
from app.modules.kyc.providers import ManualProvider
from app.shared.actors import SYSTEM
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.kyc import SEALER, FakeProvider, ready_draft
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration


async def hand_in(database: Database, clock: FakeClock, provider: object) -> tuple[str, str]:
    """A client hands in a check. Returns its id and its key at the provider."""
    client = new_client()
    async with database.actor_transaction(client) as session:
        await ready_draft(session, client, clock)
        view = await kyc.submit(session, SEALER, client, provider, clock)  # type: ignore[arg-type]
    return view.id, kyc.submission_key(view.id, view.submission)


async def work(
    database: Database, clock: FakeClock, provider: object, *, automatic_approval: bool = False
) -> None:
    """Run the worker until nothing is due."""
    jobs = ProviderJobs(
        database,
        SEALER,
        provider,  # type: ignore[arg-type]
        clock,
        automatic_approval=automatic_approval,
    )
    handlers = {**jobs.handlers(), kyc.SWEEP_UNANSWERED: sweep_unanswered(clock)}
    worker = Worker(database, handlers, WorkerConfig(), clock)
    while await worker.run_once():
        pass


async def check(database: Database, check_id: str) -> kyc.CheckView:
    async with database.actor_transaction(new_staff()) as session:
        return await kyc.check_for_staff(session, SEALER, check_id)


async def jobs_for(database: Database, key: str) -> dict[str, tuple[str, str | None]]:
    async with database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text(
                "SELECT kind, status, last_error_code FROM jobs WHERE idempotency_key = :key "
                "AND kind LIKE 'kyc.%'"
            ),
            {"key": key},
        )
        return {row.kind: (row.status, row.last_error_code) for row in rows.all()}


def sends(provider: FakeProvider, key: str) -> int:
    return sum(request.idempotency_key == key for request in provider.submitted)


@dataclass
class LostAnswer(FakeProvider):
    """Receives the submission, then the answer is lost on the way back."""

    async def submit(self, request: ProviderRequest) -> Accepted | Refused:
        self.submitted.append(request)
        raise ProviderUncertain


@dataclass
class Unreachable(FakeProvider):
    """Nothing can be learned: the send is uncertain and so is asking about it."""

    async def submit(self, request: ProviderRequest) -> Accepted | Refused:
        self.submitted.append(request)
        raise ProviderUncertain

    async def find(self, idempotency_key: str) -> str | None:
        raise ProviderUncertain


async def test_a_submission_is_sent_read_and_given_to_staff_as_passed(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    provider = FakeProvider()
    check_id, key = await hand_in(app_database, fake_clock, provider)
    assert await jobs_for(worker_database, key) == {kyc.SEND_CHECK: ("pending", None)}

    await work(worker_database, fake_clock, provider)

    view = await check(app_database, check_id)
    assert view.status is CheckStatus.NEEDS_REVIEW
    assert view.review_flags == ("automatic_checks_passed",)
    assert view.provider_reference == "fake-reference"
    assert view.account_number is None
    assert sends(provider, key) == 1
    sent = next(r for r in provider.submitted if r.idempotency_key == key)
    assert sent.details == view.details
    assert set(sent.photos.values()) == {photo.storage_key for photo in view.photos}
    assert await jobs_for(worker_database, key) == {
        kyc.SEND_CHECK: ("succeeded", None),
        kyc.READ_RESULT: ("succeeded", None),
    }


async def test_with_automatic_approval_on_a_clean_result_approves(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    provider = FakeProvider()
    check_id, _ = await hand_in(app_database, fake_clock, provider)
    await work(worker_database, fake_clock, provider, automatic_approval=True)
    view = await check(app_database, check_id)
    assert view.status is CheckStatus.APPROVED
    assert view.account_number is not None


async def test_the_manual_provider_queues_nothing(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    check_id, key = await hand_in(app_database, fake_clock, ManualProvider())
    assert (await check(app_database, check_id)).status is CheckStatus.NEEDS_REVIEW
    assert await jobs_for(worker_database, key) == {}


async def test_a_refused_submission_goes_to_a_person(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    provider = FakeProvider(submit_answer=Refused("unsupported_document"))
    check_id, key = await hand_in(app_database, fake_clock, provider)
    await work(worker_database, fake_clock, provider)
    view = await check(app_database, check_id)
    assert view.status is CheckStatus.NEEDS_REVIEW
    assert view.review_flags == ("provider_refused",)
    assert kyc.READ_RESULT not in await jobs_for(worker_database, key)


async def test_a_send_that_did_not_arrive_is_tried_again(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    provider = FakeProvider(submit_answer=ProviderDeferred())
    check_id, key = await hand_in(app_database, fake_clock, provider)
    await work(worker_database, fake_clock, provider)
    assert (await check(app_database, check_id)).status is CheckStatus.SUBMITTED
    assert await jobs_for(worker_database, key) == {
        kyc.SEND_CHECK: ("pending", "provider_deferred")
    }

    provider.submit_answer = Accepted("fake-reference")
    fake_clock.advance(seconds=30)
    await work(worker_database, fake_clock, provider)
    assert (await check(app_database, check_id)).status is CheckStatus.NEEDS_REVIEW
    assert sends(provider, key) == 1


async def test_after_an_uncertain_send_the_provider_is_asked_and_nothing_is_sent_twice(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    provider = LostAnswer()
    check_id, key = await hand_in(app_database, fake_clock, provider)
    await work(worker_database, fake_clock, provider)
    view = await check(app_database, check_id)
    # The provider held it, so the result was read as if the send had answered.
    assert view.status is CheckStatus.NEEDS_REVIEW
    assert view.review_flags == ("automatic_checks_passed",)
    assert view.provider_reference == "fake-reference"
    assert sends(provider, key) == 1


async def test_an_uncertain_send_the_provider_does_not_hold_goes_to_a_person(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    provider = FakeProvider(submit_answer=ProviderUncertain())
    check_id, key = await hand_in(app_database, fake_clock, provider)
    await work(worker_database, fake_clock, provider)
    view = await check(app_database, check_id)
    assert view.status is CheckStatus.NEEDS_REVIEW
    assert view.review_flags == ("provider_not_received",)
    assert await jobs_for(worker_database, key) == {kyc.SEND_CHECK: ("succeeded", None)}


async def test_a_send_nobody_can_confirm_stops_and_the_sweep_gives_it_to_a_person(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    provider = Unreachable()
    check_id, key = await hand_in(app_database, fake_clock, provider)
    await work(worker_database, fake_clock, provider)
    assert (await check(app_database, check_id)).status is CheckStatus.SUBMITTED
    assert await jobs_for(worker_database, key) == {kyc.SEND_CHECK: ("failed", "uncertain_outcome")}

    async def sweep() -> None:
        async with worker_database.actor_transaction(SYSTEM) as session:
            await enqueue(
                session,
                JobRequest(kind=kyc.SWEEP_UNANSWERED, idempotency_key=push_id(), payload={}),
                now=fake_clock.now(),
            )
        await work(worker_database, fake_clock, provider)

    fake_clock.advance(minutes=29)
    await sweep()
    assert (await check(app_database, check_id)).status is CheckStatus.SUBMITTED
    fake_clock.advance(minutes=2)
    await sweep()
    view = await check(app_database, check_id)
    assert view.status is CheckStatus.NEEDS_REVIEW
    assert view.review_flags == ("provider_no_answer",)
    # It was sent once and never again, on any run of the worker.
    assert sends(provider, key) == 1


async def test_a_result_that_is_not_ready_is_asked_for_again(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    provider = FakeProvider(result_answer=Pending())
    check_id, key = await hand_in(app_database, fake_clock, provider)
    await work(worker_database, fake_clock, provider)
    assert (await check(app_database, check_id)).status is CheckStatus.SUBMITTED
    assert (await jobs_for(worker_database, key))[kyc.READ_RESULT] == (
        "pending",
        "provider_pending",
    )

    provider.result_answer = FakeProvider().result_answer
    fake_clock.advance(seconds=30)
    await work(worker_database, fake_clock, provider)
    assert (await check(app_database, check_id)).status is CheckStatus.NEEDS_REVIEW


async def test_a_send_for_a_check_that_moved_on_does_nothing(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    provider = FakeProvider()
    check_id, key = await hand_in(app_database, fake_clock, provider)
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await kyc.give_to_person(session, check_id, 1, "provider_no_answer", fake_clock)
    await work(worker_database, fake_clock, provider)
    assert sends(provider, key) == 0
    assert (await check(app_database, check_id)).review_flags == ("provider_no_answer",)
