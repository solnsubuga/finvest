"""Screening through the real job queue and worker, and closing cases with two people.

The test database is shared, so each test looks only at its own clients' screens and cases.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, WorkerConfig, enqueue
from app.modules.screening import contract, service
from app.modules.screening.domain.cases import Decision
from app.modules.screening.domain.provider import (
    Match,
    MatchCategory,
    ProviderRefused,
    ProviderUnavailable,
    ScreeningResult,
    ScreeningSubject,
)
from app.modules.screening.domain.standing import RESCREEN_EVERY, Outcome, Reason, RunStatus
from app.modules.screening.jobs import ScreeningJobs
from app.shared.actors import SYSTEM, Actor
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api

pytestmark = pytest.mark.integration

SUBJECT = ScreeningSubject("NAKATO", "Amara", date(1994, 6, 22), "UG")
SANCTIONS = Match("ent-sanctions", "Amara Nakato", MatchCategory.SANCTIONS, 93, ("UN SC",))
PEP = Match("ent-pep", "Amara Nakato", MatchCategory.PEP, 81, ("Uganda PEPs",))


@dataclass
class FakeScreening:
    """Answers each screen as scripted, by client, and remembers who it was asked about."""

    name: str = "fake"
    answers: dict[str, list[ScreeningResult | Exception]] = field(default_factory=dict)
    asked: list[str] = field(default_factory=list)
    clients_by_run: dict[str, str] = field(default_factory=dict)

    async def screen(self, subject: ScreeningSubject, *, key: str) -> ScreeningResult:
        client_id = self.clients_by_run.get(key, "")
        self.asked.append(client_id)
        script = self.answers.get(client_id) or [ScreeningResult(())]
        answer = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


@dataclass
class Setup:
    provider: FakeScreening = field(default_factory=FakeScreening)
    # Clients the sweep sees as approved, and those with an identity check to screen.
    approved: list[str] = field(default_factory=list)
    subjects: dict[str, ScreeningSubject] = field(default_factory=dict)

    async def subject(self, session: AsyncSession, client_id: str) -> ScreeningSubject | None:
        return self.subjects.get(client_id)

    async def page(self, session: AsyncSession, after: str | None, limit: int) -> list[str]:
        ordered = sorted(self.approved)
        return [c for c in ordered if after is None or c > after][:limit]


async def work(
    database: Database, clock: FakeClock, setup: Setup, *, provider: bool = True
) -> None:
    """Run the screening worker until nothing is due."""
    async with database.actor_transaction(SYSTEM) as session:
        rows = await session.execute(
            text("SELECT id, client_id FROM screening_runs WHERE status = 'pending'")
        )
        setup.provider.clients_by_run.update({row.id: row.client_id for row in rows})
    jobs = ScreeningJobs(
        database,
        setup.provider if provider else None,
        setup.subject,
        setup.page,
        clock,
    )
    worker = Worker(database, jobs.handlers(), WorkerConfig(), clock)
    for _ in range(50):
        if not await worker.run_once():
            return


async def ask(database: Database, client_id: str, clock: FakeClock, key: str | None = None) -> str:
    async with database.actor_transaction(SYSTEM) as session:
        run_id = await contract.request_screening(
            session,
            client_id=client_id,
            reason=Reason.PAYOUT,
            idempotency_key=key or push_id(),
            now=clock.now(),
        )
    assert run_id is not None
    return run_id


async def run_of(database: Database, run_id: str) -> service.Run:
    async with database.actor_transaction(SYSTEM) as session:
        return await service._run(session, run_id)


async def standing_of(database: Database, client_id: str) -> service.ScreeningStanding | None:
    async with database.actor_transaction(SYSTEM) as session:
        return await contract.latest_outcome(session, client_id)


async def cases_of(database: Database, client_id: str) -> list[service.Case]:
    """As staff on the API's role: the worker reads no staff names."""
    async with database.actor_transaction(Actor("staff", push_id())) as session:
        return await service.list_cases(session, client_id=client_id)


def screened(setup: Setup) -> str:
    client_id = push_id()
    setup.subjects[client_id] = SUBJECT
    return client_id


async def test_a_client_asks_once_for_themselves_and_never_for_anyone_else(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client = Actor("client", push_id())
    async with app_database.actor_transaction(client) as session:
        first = await contract.request_screening(
            session,
            client_id=client.id or "",
            reason=Reason.ONBOARDING,
            idempotency_key=f"onboarding:{client.id}",
            now=fake_clock.now(),
        )
        again = await contract.request_screening(
            session,
            client_id=client.id or "",
            reason=Reason.ONBOARDING,
            idempotency_key=f"onboarding:{client.id}",
            now=fake_clock.now(),
        )
    assert first is not None and again is None
    run = await run_of(worker_database, first)
    assert run.status is RunStatus.PENDING and run.reason is Reason.ONBOARDING
    async with worker_database.actor_transaction(SYSTEM) as session:
        jobs = await session.scalar(
            text("SELECT count(*) FROM jobs WHERE kind = :kind AND idempotency_key = :key"),
            {"kind": service.SCREEN_KIND, "key": first},
        )
    assert jobs == 1
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(client) as session:
            await contract.request_screening(
                session,
                client_id=push_id(),
                reason=Reason.ONBOARDING,
                idempotency_key=push_id(),
                now=fake_clock.now(),
            )


async def test_a_clear_screen_and_a_match_that_opens_a_case(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    setup = Setup()
    clear, matched = screened(setup), screened(setup)
    setup.provider.answers[matched] = [ScreeningResult((SANCTIONS, PEP), reference="ref-1")]
    clear_run = await ask(worker_database, clear, fake_clock)
    matched_run = await ask(worker_database, matched, fake_clock)

    await work(worker_database, fake_clock, setup)

    assert (await run_of(worker_database, clear_run)).status is RunStatus.CLEAR
    assert (await standing_of(worker_database, clear)).outcome is Outcome.CLEAR  # type: ignore[union-attr]
    assert await cases_of(app_database, clear) == []
    run = await run_of(worker_database, matched_run)
    assert run.status is RunStatus.POSSIBLE_MATCH
    assert (run.match_count, run.new_match_count, run.provider_reference) == (2, 2, "ref-1")
    [case] = await cases_of(app_database, matched)
    assert case.run_id == matched_run and case.code == "possible_match"
    standing = await standing_of(worker_database, matched)
    assert standing is not None and standing.outcome is Outcome.POSSIBLE_MATCH
    assert standing.case_id == case.id


async def test_nothing_is_screened_without_a_provider_or_an_identity_check(
    worker_database: Database, fake_clock: FakeClock
) -> None:
    setup = Setup()
    no_provider = await ask(worker_database, screened(setup), fake_clock)
    await work(worker_database, fake_clock, setup, provider=False)
    assert (await run_of(worker_database, no_provider)).status is RunStatus.NOT_RUN

    nobody = push_id()
    no_subject = await ask(worker_database, nobody, fake_clock)
    await work(worker_database, fake_clock, setup)
    assert (await run_of(worker_database, no_subject)).status is RunStatus.NO_SUBJECT
    assert nobody not in setup.provider.asked
    assert (await standing_of(worker_database, nobody)).outcome is Outcome.NOT_RUN  # type: ignore[union-attr]


async def test_a_provider_that_is_down_is_asked_again_and_one_that_refuses_is_not(
    worker_database: Database, fake_clock: FakeClock
) -> None:
    setup = Setup()
    flaky, refused = screened(setup), screened(setup)
    setup.provider.answers[flaky] = [ProviderUnavailable(), ScreeningResult(())]
    setup.provider.answers[refused] = [ProviderRefused("name_not_searchable")]
    flaky_run = await ask(worker_database, flaky, fake_clock)
    refused_run = await ask(worker_database, refused, fake_clock)

    await work(worker_database, fake_clock, setup)
    assert (await run_of(worker_database, flaky_run)).status is RunStatus.PENDING
    fake_clock.advance(minutes=10)
    await work(worker_database, fake_clock, setup)

    assert (await run_of(worker_database, flaky_run)).status is RunStatus.CLEAR
    assert setup.provider.asked.count(flaky) == 2
    run = await run_of(worker_database, refused_run)
    assert (run.status, run.failure_code) == (RunStatus.UNAVAILABLE, "name_not_searchable")
    assert setup.provider.asked.count(refused) == 1


async def test_a_screen_the_provider_never_answers_is_given_up_and_never_finished_twice(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    client_id = push_id()
    run_id = await ask(worker_database, client_id, fake_clock)
    fake_clock.advance(hours=7)
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await service.give_up_on_stale(session, fake_clock.now()) >= 1
    run = await run_of(worker_database, run_id)
    assert (run.status, run.failure_code) == (RunStatus.UNAVAILABLE, "provider_no_answer")
    async with worker_database.actor_transaction(SYSTEM) as session:
        late = await service.record_result(
            session, run_id, provider="fake", result=ScreeningResult((SANCTIONS,)), clock=fake_clock
        )
    assert late is None
    assert await cases_of(app_database, client_id) == []
    with pytest.raises(DBAPIError):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "UPDATE screening_runs SET status = 'clear', match_count = 0, "
                    "new_match_count = 0, failure_code = NULL WHERE id = :id"
                ),
                {"id": run_id},
            )


async def test_the_sweep_screens_each_due_client_once_a_day(
    worker_database: Database, fake_clock: FakeClock
) -> None:
    setup = Setup()
    never, stale, fresh, waiting = (screened(setup) for _ in range(4))
    setup.approved = [never, stale, fresh, waiting]
    stale_run = await ask(worker_database, stale, fake_clock)
    await work(worker_database, fake_clock, setup)
    fake_clock.advance(seconds=RESCREEN_EVERY.total_seconds())
    await work(worker_database, fake_clock, setup)  # nothing due yet: the sweep has not run
    fresh_run = await ask(worker_database, fresh, fake_clock)
    await work(worker_database, fake_clock, setup)
    waiting_run = await ask(worker_database, waiting, fake_clock)  # still pending at sweep time

    jobs = ScreeningJobs(worker_database, setup.provider, setup.subject, setup.page, fake_clock)
    for _ in range(2):  # twice the same day
        async with worker_database.actor_transaction(SYSTEM) as session:
            await enqueue(
                session,
                JobRequest(kind=service.RESCREEN_KIND, idempotency_key=push_id(), payload={}),
                now=fake_clock.now(),
            )
        worker = Worker(worker_database, jobs.handlers(), WorkerConfig(), fake_clock)
        while await worker.run_once():
            pass

    async with worker_database.actor_transaction(SYSTEM) as session:
        rescreens = await session.execute(
            text(
                "SELECT client_id, count(*) AS n FROM screening_runs WHERE reason = 'rescreen' "
                "AND client_id = ANY(CAST(:ids AS text[])) GROUP BY client_id"
            ),
            {"ids": setup.approved},
        )
        counts = {row.client_id: row.n for row in rescreens}
    assert counts == {never: 1, stale: 1}
    assert stale_run and fresh_run and waiting_run


async def ruled_out(api: Api, case_id: str, outcome: str = "not_a_match") -> None:
    """Two members of compliance close the case."""
    maker = await api.staff("compliance")
    response = await api.http.post(
        f"/staff/compliance/cases/{case_id}/proposals",
        json={"outcome": outcome, "reason": "Different date of birth and nationality"},
    )
    assert response.status_code == 201, response.text
    proposal = response.json()["case"]["waiting"]["id"]
    await api.staff("compliance")
    response = await api.http.post(f"/staff/compliance/proposals/{proposal}/approve")
    assert response.status_code == 200, response.text
    assert maker


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


async def matched_case(
    app_database: Database,
    worker_database: Database,
    clock: FakeClock,
    setup: Setup,
    *matches: Match,
) -> tuple[str, str]:
    client_id = screened(setup)
    setup.provider.answers[client_id] = [ScreeningResult(matches or (SANCTIONS,))]
    await ask(worker_database, client_id, clock)
    await work(worker_database, clock, setup)
    [case] = await cases_of(app_database, client_id)
    return client_id, case.id


async def test_two_people_close_a_case_and_the_same_match_is_not_raised_again(
    api: Api, worker_database: Database
) -> None:
    setup = Setup()
    client_id, case_id = await matched_case(
        api.database, worker_database, api.clock, setup, SANCTIONS, PEP
    )
    await api.staff("compliance")
    detail = (await api.http.get(f"/staff/compliance/cases/{case_id}")).json()
    assert detail["can_propose"] is True and detail["can_decide"] is False
    assert [m["entity_id"] for m in detail["matches"]] == ["ent-sanctions", "ent-pep"]
    assert detail["outcomes"] == ["not_a_match", "pep_accepted", "sanctioned"]
    assert detail["standing"]["outcome"] == "possible_match"

    await ruled_out(api, case_id)

    [case] = await cases_of(api.database, client_id)
    assert (case.status.value, case.outcome.value) == ("closed", "not_a_match")  # type: ignore[union-attr]
    assert (await standing_of(worker_database, client_id)).outcome is Outcome.CLEAR  # type: ignore[union-attr]
    # The next screen finds the same two entities and one new one: only the new one counts.
    newcomer = Match("ent-new", "A. Nakato", MatchCategory.ADVERSE_MEDIA, 70)
    setup.provider.answers[client_id] = [ScreeningResult((SANCTIONS, PEP))]
    same = await ask(worker_database, client_id, api.clock)
    await work(worker_database, api.clock, setup)
    assert (await run_of(worker_database, same)).new_match_count == 0
    assert len(await cases_of(api.database, client_id)) == 1
    setup.provider.answers[client_id] = [ScreeningResult((SANCTIONS, newcomer))]
    await ask(worker_database, client_id, api.clock)
    await work(worker_database, api.clock, setup)
    assert len(await cases_of(api.database, client_id)) == 2


async def test_a_confirmed_sanctions_match_stands_after_later_clear_screens(
    api: Api, worker_database: Database
) -> None:
    setup = Setup()
    client_id, case_id = await matched_case(api.database, worker_database, api.clock, setup)
    await ruled_out(api, case_id, "sanctioned")
    setup.provider.answers[client_id] = [ScreeningResult(())]
    await ask(worker_database, client_id, api.clock)
    await work(worker_database, api.clock, setup)
    standing = await standing_of(worker_database, client_id)
    assert standing is not None and standing.outcome is Outcome.CONFIRMED_MATCH
    assert standing.case_id == case_id


async def test_a_match_already_on_an_open_case_opens_no_second_one(
    api: Api, worker_database: Database
) -> None:
    setup = Setup()
    client_id, case_id = await matched_case(api.database, worker_database, api.clock, setup)
    # Screened again before staff decide, as a payout or a member of staff would ask.
    setup.provider.answers[client_id] = [ScreeningResult((SANCTIONS,))]
    again = await ask(worker_database, client_id, api.clock)
    await work(worker_database, api.clock, setup)
    run = await run_of(worker_database, again)
    assert (run.status, run.match_count, run.new_match_count) == (
        RunStatus.POSSIBLE_MATCH,
        1,
        0,
    )
    assert [case.id for case in await cases_of(api.database, client_id)] == [case_id]
    standing = await standing_of(worker_database, client_id)
    assert standing is not None and standing.outcome is Outcome.POSSIBLE_MATCH
    assert standing.case_id == case_id


async def test_a_confirmed_sanctions_match_found_again_opens_no_new_case(
    api: Api, worker_database: Database
) -> None:
    setup = Setup()
    client_id, case_id = await matched_case(api.database, worker_database, api.clock, setup)
    await ruled_out(api, case_id, "sanctioned")
    setup.provider.answers[client_id] = [ScreeningResult((SANCTIONS,))]
    again = await ask(worker_database, client_id, api.clock)
    await work(worker_database, api.clock, setup)
    assert (await run_of(worker_database, again)).new_match_count == 0
    assert len(await cases_of(api.database, client_id)) == 1
    standing = await standing_of(worker_database, client_id)
    assert standing is not None and standing.outcome is Outcome.CONFIRMED_MATCH


async def propose(api: Api, case_id: str, outcome: str = "not_a_match") -> httpx.Response:
    return await api.http.post(
        f"/staff/compliance/cases/{case_id}/proposals",
        json={"outcome": outcome, "reason": "Not the listed person"},
    )


async def test_the_proposer_never_decides_and_only_they_withdraw(
    api: Api, worker_database: Database
) -> None:
    _, case_id = await matched_case(api.database, worker_database, api.clock, Setup())
    maker = await api.staff("compliance")
    assert (await propose(api, case_id, "no_concern")).status_code == 422
    proposal = (await propose(api, case_id)).json()["case"]["waiting"]["id"]
    assert (await propose(api, case_id)).json()["error"]["fields"][0]["code"] == "proposal_waiting"
    assert (
        await api.http.post(f"/staff/compliance/proposals/{proposal}/approve")
    ).status_code == 403
    sent = await api.http.post(
        f"/staff/compliance/proposals/{proposal}/send-back", json={"note": "Mine"}
    )
    assert sent.status_code == 403

    await api.staff("compliance")
    detail = (await api.http.get(f"/staff/compliance/cases/{case_id}")).json()
    assert detail["can_decide"] is True and detail["yours"] is False
    assert (
        await api.http.post(f"/staff/compliance/proposals/{proposal}/withdraw")
    ).status_code == 403
    no_note = await api.http.post(f"/staff/compliance/proposals/{proposal}/send-back", json={})
    assert no_note.status_code == 422
    sent = await api.http.post(
        f"/staff/compliance/proposals/{proposal}/send-back",
        json={"note": "Check the date of birth against the list first"},
    )
    assert sent.status_code == 200
    assert sent.json()["case"]["status"] == "open" and sent.json()["can_propose"] is True

    await api.as_staff(maker)
    again = (await propose(api, case_id)).json()["case"]["waiting"]["id"]
    withdrawn = await api.http.post(f"/staff/compliance/proposals/{again}/withdraw")
    assert withdrawn.status_code == 200
    assert [p["status"] for p in withdrawn.json()["proposals"]] == ["sent_back", "withdrawn"]


async def test_the_database_closes_a_case_only_with_a_second_persons_approval(
    api: Api, worker_database: Database
) -> None:
    _, case_id = await matched_case(api.database, worker_database, api.clock, Setup())
    maker = await api.staff("compliance")
    proposal = (await propose(api, case_id)).json()["case"]["waiting"]["id"]
    # Straight to the table, past the service: closing with no approval is refused.
    with pytest.raises(DBAPIError):
        async with api.database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE compliance_cases SET status = 'closed', outcome = 'not_a_match', "
                    "closed_at = now(), closed_by_proposal = :p WHERE id = :id"
                ),
                {"p": proposal, "id": case_id},
            )
    # The proposer approving their own proposal is refused.
    with pytest.raises(DBAPIError):
        async with api.database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE compliance_case_proposals SET status = 'approved', decided_by = :me, "
                    "decided_at = now(), decided_in = pg_current_xact_id() WHERE id = :id"
                ),
                {"me": maker.id, "id": proposal},
            )
    # Staff never open a case or finish a screen.
    with pytest.raises(DBAPIError):
        async with api.database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "INSERT INTO compliance_cases (id, client_id, source, code, dedupe_key, "
                    "opened_at) VALUES (:id, :client, 'monitoring', 'made_up', 'x', now())"
                ),
                {"id": push_id(), "client": push_id()},
            )


async def test_two_approvers_at_once_close_the_case_once(
    api: Api, worker_database: Database
) -> None:
    _, case_id = await matched_case(api.database, worker_database, api.clock, Setup())
    await api.staff("compliance")
    proposal = (await propose(api, case_id)).json()["case"]["waiting"]["id"]
    first, second = await api.staff("compliance"), await api.staff("compliance")

    async def approve(staff: Actor) -> str:
        try:
            async with api.database.actor_transaction(staff) as session:
                await service.decide(session, staff, proposal, Decision.APPROVED, None, api.clock)
        except Exception as error:
            return type(error).__name__
        return "approved"

    results = await asyncio.gather(approve(first), approve(second))
    assert sorted(results) == ["ApplicationError", "approved"]
    async with api.database.actor_transaction(Actor("staff", push_id())) as session:
        audits = await session.scalar(
            text(
                "SELECT count(*) FROM audit_events WHERE resource_id = :id "
                "AND action = 'compliance.case_proposal_approved'"
            ),
            {"id": case_id},
        )
    assert audits == 1


async def test_staff_without_compliance_see_nothing_and_cases_reach_the_inbox(
    api: Api, worker_database: Database
) -> None:
    client_id, case_id = await matched_case(api.database, worker_database, api.clock, Setup())
    await api.staff("support")
    assert (await api.http.get("/staff/compliance/cases")).status_code == 403
    await api.staff("compliance")
    listed = (await api.http.get("/staff/compliance/cases")).json()
    assert case_id in [case["id"] for case in listed]
    proposal = (await propose(api, case_id)).json()["case"]["waiting"]["id"]
    await api.staff("compliance")
    inbox = (await api.http.get("/staff/approvals")).json()
    [item] = [i for i in inbox["items"] if i["id"] == proposal]
    assert (item["kind"], item["subject"], item["can_decide"]) == (
        "compliance_case",
        "not_a_match",
        True,
    )
    page = (await api.http.get(f"/staff/compliance/clients/{client_id}")).json()
    assert page["standing"]["outcome"] == "possible_match" and len(page["runs"]) == 1
    key = push_id()
    asked = await api.http.post(
        f"/staff/compliance/clients/{client_id}/screenings", headers={"Idempotency-Key": key}
    )
    again = await api.http.post(
        f"/staff/compliance/clients/{client_id}/screenings", headers={"Idempotency-Key": key}
    )
    assert asked.status_code == 201 and asked.json()["id"] == again.json()["id"]
    assert asked.json()["reason"] == "staff" and asked.json()["status"] == "pending"
    summary = (await api.http.get("/staff/compliance/summary")).json()
    assert summary["screening_on"] is False and summary["open_cases"] >= 1
