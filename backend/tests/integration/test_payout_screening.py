"""Payouts wait for the client to be screened clear, through the real worker and database."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker, WorkerConfig
from app.modules.payouts import payouts
from app.modules.payouts.domain.payouts import PayoutEvent, PayoutStatus
from app.modules.payouts.jobs import release_screened
from app.modules.screening import service as screening
from app.modules.screening.domain.provider import (
    Match,
    MatchCategory,
    ProviderRefused,
    ScreeningResult,
)
from app.modules.screening.domain.standing import Reason, RunStatus
from app.modules.screening.jobs import ScreeningJobs
from app.shared.actors import SYSTEM, Actor
from app.shared.push_ids import push_id
from tests.integration.test_screening import SUBJECT, Setup
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.jobs import run_due
from tests.support.payouts import World

pytestmark = pytest.mark.integration

LISTED = Match("ent-payout", "Sarah Nakato", MatchCategory.SANCTIONS, 91, ("UN SC",))
BIG = 12_000_000  # above the UGX 10,000,000 review amount seeded by migration 0031


@pytest.fixture
def screening_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINVEST_SCREENING_PROVIDER", "development-stub")


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


async def world(
    api: Api, worker_database: Database, setup: Setup, funds: int = 20_000_000
) -> World:
    built = await World(api.database, worker_database, api.clock).build(funds, mobile=False)
    setup.subjects[built.client_id] = SUBJECT
    return built


async def work(database: Database, clock: FakeClock, setup: Setup) -> None:
    """Screen whoever is waiting, then release what the screens allow."""
    async with database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text("SELECT id, client_id FROM screening_runs WHERE status = 'pending'")
        )
        setup.provider.clients_by_run.update({row.id: row.client_id for row in rows})
    jobs = ScreeningJobs(database, setup.provider, setup.subject, setup.page, clock)
    worker = Worker(
        database, {**jobs.handlers(), **release_screened(database, clock)}, WorkerConfig(), clock
    )
    await run_due(worker)


async def status_of(database: Database, payout_id: str) -> PayoutStatus:
    async with database.actor_transaction(SYSTEM) as session:
        return (await payouts.get_payout(session, payout_id)).status


async def runs_for(database: Database, client_id: str) -> list[screening.Run]:
    async with database.actor_transaction(SYSTEM) as session:
        return await screening.runs_for_client(session, client_id)


async def send_jobs(database: Database, payout_id: str) -> int:
    async with database.actor_transaction(SYSTEM) as session:
        return int(
            await session.scalar(
                text("SELECT count(*) FROM jobs WHERE kind = :kind AND idempotency_key = :id"),
                {"kind": payouts.SEND_KIND, "id": payout_id},
            )
            or 0
        )


async def two_people_close(api: Api, case_id: str, outcome: str) -> None:
    await api.staff("compliance")
    response = await api.http.post(
        f"/staff/compliance/cases/{case_id}/proposals",
        json={"outcome": outcome, "reason": "Checked against the list entry"},
    )
    proposal = response.json()["case"]["waiting"]["id"]
    await api.staff("compliance")
    assert (await api.http.post(f"/staff/compliance/proposals/{proposal}/approve")).is_success


async def test_without_a_provider_a_payout_goes_ahead_unless_a_case_holds_the_client(
    api: Api, worker_database: Database
) -> None:
    setup = Setup()
    free = await world(api, worker_database, setup)
    assert (await free.request(1_000)).status is PayoutStatus.QUEUED
    assert await runs_for(worker_database, free.client_id) == []

    held = await world(api, worker_database, setup)
    setup.provider.answers[held.client_id] = [ScreeningResult((LISTED,))]
    async with worker_database.actor_transaction(SYSTEM) as session:
        await screening.request_screening(
            session,
            client_id=held.client_id,
            reason=Reason.ONBOARDING,
            idempotency_key=push_id(),
            now=api.clock.now(),
        )
    await work(worker_database, api.clock, setup)
    payout = await held.request(1_000)
    assert payout.status is PayoutStatus.SCREENING
    # Straight to the table, past the service: a queued payout for this client is refused.
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE payouts SET status = 'queued' WHERE id = :id"), {"id": payout.id}
            )


@pytest.mark.usefixtures("screening_on")
async def test_a_payout_waits_for_a_fresh_screen_then_goes_to_the_queue_or_to_review(
    api: Api, worker_database: Database
) -> None:
    setup = Setup()
    client = await world(api, worker_database, setup)
    small, big = await client.request(1_000), await client.request(BIG)
    assert (small.status, big.status) == (PayoutStatus.SCREENING, PayoutStatus.SCREENING)
    [run] = await runs_for(worker_database, client.client_id)
    assert (run.reason, run.status) == (Reason.PAYOUT, RunStatus.PENDING)
    assert await send_jobs(worker_database, small.id) == 0

    await work(worker_database, api.clock, setup)

    assert await status_of(worker_database, small.id) is PayoutStatus.QUEUED
    assert await status_of(worker_database, big.id) is PayoutStatus.HELD
    # A bank payout waits for staff to pay it from the trust account, so no send job.
    assert await send_jobs(worker_database, small.id) == 0
    # Screened clear moments ago: the next payout goes straight on, with no new screen.
    assert (await client.request(1_000)).status is PayoutStatus.QUEUED
    assert len(await runs_for(worker_database, client.client_id)) == 1
    # A day later, a new payout asks for a new screen.
    api.clock.advance(hours=25)
    assert (await client.request(1_000)).status is PayoutStatus.SCREENING
    assert len(await runs_for(worker_database, client.client_id)) == 2
    await client.no_drift()


@pytest.mark.usefixtures("screening_on")
async def test_a_possible_match_holds_the_payout_until_two_people_rule_it_out(
    api: Api, worker_database: Database
) -> None:
    setup = Setup()
    client = await world(api, worker_database, setup)
    setup.provider.answers[client.client_id] = [ScreeningResult((LISTED,))]
    payout = await client.request(1_000)
    await work(worker_database, api.clock, setup)
    assert await status_of(worker_database, payout.id) is PayoutStatus.SCREENING

    # The worker may not release it while the case is open.
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await payouts.apply_event(
                session, SYSTEM, payout.id, PayoutEvent.SCREENING_CLEARED, api.clock
            )
    await api.staff("compliance")
    waiting = (await api.http.get("/staff/compliance/payouts")).json()
    [listed] = [item for item in waiting if item["id"] == payout.id]
    assert listed["standing"]["outcome"] == "possible_match"
    case_id = listed["standing"]["case_id"]

    await two_people_close(api, case_id, "not_a_match")
    await work(worker_database, api.clock, setup)

    assert await status_of(worker_database, payout.id) is PayoutStatus.QUEUED


@pytest.mark.usefixtures("screening_on")
async def test_a_sanctioned_client_is_never_paid_and_staff_may_decline_the_payout(
    api: Api, worker_database: Database
) -> None:
    setup = Setup()
    client = await world(api, worker_database, setup, funds=5_000)
    setup.provider.answers[client.client_id] = [ScreeningResult((LISTED,))]
    payout = await client.request(5_000)
    await work(worker_database, api.clock, setup)
    async with worker_database.actor_transaction(SYSTEM) as session:
        [case] = (
            (
                await session.execute(
                    text("SELECT id FROM compliance_cases WHERE client_id = :c"),
                    {"c": client.client_id},
                )
            )
            .scalars()
            .all()
        )
    await two_people_close(api, case, "sanctioned")
    await work(worker_database, api.clock, setup)
    assert await status_of(worker_database, payout.id) is PayoutStatus.SCREENING
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await payouts.release_screened(session, client.client_id, api.clock) == []
    # A new payout for them never starts on its way either.
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text("UPDATE payouts SET status = 'held' WHERE id = :id"), {"id": payout.id}
            )

    staff = await api.staff("operations")
    declined = await api.http.post(
        f"/staff/payouts/{payout.id}/decline",
        json={"reason": "Client confirmed on a sanctions list"},
        headers={"Idempotency-Key": push_id()},
    )
    assert declined.status_code == 200, declined.text
    assert await status_of(worker_database, payout.id) is PayoutStatus.CANCELLED
    assert await client.balance(client.holding) == 5_000
    assert staff == Actor("staff", staff.id)
    await client.no_drift()


@pytest.mark.usefixtures("screening_on")
async def test_a_payout_whose_screen_could_not_run_is_screened_again_a_day_later(
    api: Api, worker_database: Database
) -> None:
    setup = Setup()
    client = await world(api, worker_database, setup)
    setup.provider.answers[client.client_id] = [ProviderRefused("subject_rejected")]
    payout = await client.request(1_000)
    await work(worker_database, api.clock, setup)
    [run] = await runs_for(worker_database, client.client_id)
    assert run.status is RunStatus.UNAVAILABLE
    assert await status_of(worker_database, payout.id) is PayoutStatus.SCREENING

    # Within the day the screen that could not run still speaks: nothing new is asked for.
    async with worker_database.actor_transaction(SYSTEM) as session:
        found = await payouts.retry_screening(session, api.clock)
    # Other tests' clients may be waiting too: the database is shared.
    assert payout.id not in found.still_waiting_long
    assert len(await runs_for(worker_database, client.client_id)) == 1

    # A day later the hourly pass asks for a fresh screen, the provider answers, and the
    # payout moves on with no person involved.
    api.clock.advance(hours=25)
    setup.provider.answers[client.client_id] = [ScreeningResult(())]
    async with worker_database.actor_transaction(SYSTEM) as session:
        found = await payouts.retry_screening(session, api.clock)
    assert payout.id in found.still_waiting_long
    assert len(await runs_for(worker_database, client.client_id)) == 2
    await work(worker_database, api.clock, setup)
    assert await status_of(worker_database, payout.id) is PayoutStatus.QUEUED
    await client.no_drift()


async def test_the_retry_pass_is_a_worker_job(worker_database: Database) -> None:
    assert payouts.RETRY_SCREENING_KIND in release_screened(worker_database, FakeClock())


async def test_a_client_asks_about_no_one_else(app_database: Database) -> None:
    client = Actor("client", push_id())
    async with app_database.actor_transaction(client) as session:
        own = await session.scalar(
            text("SELECT screening_payout_clearance(:c, now())"), {"c": client.id}
        )
    assert own == "screen"
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(client) as session:
            await session.scalar(
                text("SELECT screening_payout_clearance(:c, now())"), {"c": push_id()}
            )
