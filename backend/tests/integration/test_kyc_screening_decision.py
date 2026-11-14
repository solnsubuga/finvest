"""Automatic approval with the screening module on: screening is its answer, and waits for it.

Regula, like any provider that does not screen, sends no screening outcome. The screening
module screens every client who hands in their check, and the decision uses where the client
stands there, not what the identity provider said.
"""

import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker, WorkerConfig
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import CheckStatus
from app.modules.kyc.domain.provider import AutomaticCheck, CheckOutcome, Completed, Verdict
from app.modules.kyc.jobs import ProviderJobs
from app.modules.screening import service as screening_service
from app.modules.screening.contract import Reason
from app.modules.screening.domain.provider import Match, MatchCategory, ScreeningResult
from app.shared.actors import SYSTEM, Actor
from tests.support.clock import FakeClock
from tests.support.kyc import SEALER, FakeProvider, all_passed, live_selfie, ready_draft
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

# What Regula answers for a genuine ID and a matching selfie: no screening, no liveness.
REGULA_PASSED = (
    CheckOutcome(AutomaticCheck.DOCUMENT_GENUINE, Verdict.PASS),
    CheckOutcome(AutomaticCheck.SELFIE_MATCH, Verdict.PASS, 96),
)


@pytest.fixture(autouse=True)
def screening_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINVEST_SCREENING_PROVIDER", "development-stub")


async def handed_in(
    app: Database, worker: Database, clock: FakeClock, provider: FakeProvider
) -> tuple[Actor, str]:
    """A client with a live selfie hands in their check, which asks for a screen."""
    client = new_client()
    async with app.actor_transaction(client) as session:
        await ready_draft(session, client, clock)
    await live_selfie(app, worker, client, clock)
    async with app.actor_transaction(client) as session:
        view = await kyc.submit(session, SEALER, client, provider, clock)
    return client, view.id


async def screened(worker: Database, client: Actor, clock: FakeClock, *matches: Match) -> None:
    """The screening module's worker records what the screening provider found."""
    async with worker.actor_transaction(SYSTEM) as session:
        runs = await screening_service.runs_for_client(session, client.id or "")
        (run,) = [r for r in runs if r.reason is Reason.ONBOARDING]
        await screening_service.record_result(
            session,
            run.id,
            provider="development-stub",
            result=ScreeningResult(tuple(matches)),
            clock=clock,
        )


async def work(worker: Database, clock: FakeClock, provider: FakeProvider) -> None:
    jobs = ProviderJobs(worker, SEALER, provider, clock, automatic_approval=True)
    runner = Worker(worker, jobs.handlers(), WorkerConfig(), clock)
    while await runner.run_once():
        pass


async def check(app: Database, check_id: str) -> kyc.CheckView:
    async with app.actor_transaction(new_staff()) as session:
        return await kyc.check_for_staff(session, SEALER, check_id)


async def read_error(worker: Database, check_id: str) -> str | None:
    async with worker.actor_transaction(SYSTEM) as session:
        return (
            await session.execute(
                text(
                    "SELECT last_error_code FROM jobs WHERE kind = :kind "
                    "AND payload->>'check_id' = :id"
                ),
                {"kind": kyc.READ_RESULT, "id": check_id},
            )
        ).scalar_one_or_none()


async def test_a_clean_screen_and_clean_regula_results_approve_with_no_person(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    provider = FakeProvider(result_answer=Completed(REGULA_PASSED))
    client, check_id = await handed_in(app_database, worker_database, fake_clock, provider)

    # The screen is still running, so the result waits for it.
    await work(worker_database, fake_clock, provider)
    assert (await check(app_database, check_id)).status is CheckStatus.SUBMITTED
    assert await read_error(worker_database, check_id) == "screening_pending"

    await screened(worker_database, client, fake_clock)
    fake_clock.advance(minutes=5)
    await work(worker_database, fake_clock, provider)
    view = await check(app_database, check_id)
    assert view.status is CheckStatus.APPROVED
    assert view.account_number is not None


async def test_a_possible_match_sends_the_client_to_a_person(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    provider = FakeProvider(result_answer=Completed(REGULA_PASSED))
    client, check_id = await handed_in(app_database, worker_database, fake_clock, provider)
    match = Match("entity-1", "A Listed Name", MatchCategory.SANCTIONS, 91, ("OFAC",))
    await screened(worker_database, client, fake_clock, match)
    await work(worker_database, fake_clock, provider)
    view = await check(app_database, check_id)
    assert view.status is CheckStatus.NEEDS_REVIEW
    assert view.review_flags == ("screening_fail",)


async def test_the_screening_modules_answer_replaces_the_providers(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    # A provider that says screening passed is not believed over a screen still running.
    provider = FakeProvider()
    _, check_id = await handed_in(app_database, worker_database, fake_clock, provider)
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await kyc.screening_running(session, check_id)
        view = await kyc.record_provider_result(
            session,
            SEALER,
            check_id,
            1,
            "fake-reference",
            all_passed(),
            fake_clock,
            automatic_approval=True,
        )
    assert view.status is CheckStatus.NEEDS_REVIEW
    assert view.review_flags == ("screening_unsure",)
    checks = {o.check: o.verdict for o in view.automatic_checks}
    assert checks[AutomaticCheck.SCREENING] is Verdict.UNSURE


async def test_with_screening_off_nothing_waits_and_the_providers_answer_stands(
    app_database: Database,
    worker_database: Database,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINVEST_SCREENING_PROVIDER", "none")
    provider = FakeProvider()
    _, check_id = await handed_in(app_database, worker_database, fake_clock, provider)
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert not await kyc.screening_running(session, check_id)
    await work(worker_database, fake_clock, provider)
    assert (await check(app_database, check_id)).status is CheckStatus.APPROVED

    # A provider that does not screen, such as Regula, then never approves on its own.
    regula = FakeProvider(result_answer=Completed(REGULA_PASSED))
    _, unscreened = await handed_in(app_database, worker_database, fake_clock, regula)
    await work(worker_database, fake_clock, regula)
    view = await check(app_database, unscreened)
    assert view.status is CheckStatus.NEEDS_REVIEW
    assert view.review_flags == ("screening_not_run",)
