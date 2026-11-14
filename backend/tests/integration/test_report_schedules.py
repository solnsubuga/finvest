"""A report schedule is proposed by one person and approved by another, sends each recipient a
link once per period, and sends nothing once stopped."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.jobs import JobRequest, Worker, WorkerConfig, enqueue
from app.modules.audit import service as audit
from app.modules.reports import jobs as report_jobs
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.push_ids import push_id
from tests.support.database import TestDatabase
from tests.support.email import RecordingTransport
from tests.support.http import STAFF_ORIGIN, Api, running_api

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        running.clock.current = datetime.now(UTC)
        yield running


async def email_of(database: Database, staff: Actor) -> str:
    async with database.transaction() as session:
        found = await session.execute(
            text("SELECT email FROM identity_staff WHERE id = :id"), {"id": staff.id}
        )
        return str(found.scalar_one())


async def propose(api: Api, **body: object) -> dict[str, object]:
    response = await api.http.post(
        "/staff/report-schedules",
        json={"reason": "Finance reads it every morning", **body},
        headers={"Idempotency-Key": push_id()},
    )
    assert response.status_code == 201, response.text
    found: dict[str, object] = response.json()
    return found


async def decide(api: Api, schedule_id: object, action: str) -> tuple[int, dict[str, object]]:
    headers = {"Idempotency-Key": push_id()} if action == "approve" else {}
    response = await api.http.post(
        f"/staff/report-schedules/{schedule_id}/{action}",
        json={"reason": "Checked the list of people"},
        headers=headers,
    )
    return response.status_code, response.json()


async def run_reports(
    worker_database: Database, api: Api, transport: RecordingTransport, *, queue: bool = True
) -> None:
    jobs = report_jobs.ReportJobs(worker_database, api.clock, email=transport, console=STAFF_ORIGIN)
    if queue:
        async with worker_database.actor_transaction(SYSTEM) as session:
            await enqueue(
                session,
                JobRequest(
                    kind=report_jobs.QUEUE_KIND,
                    idempotency_key=f"reports.send_scheduled@{push_id()}",
                    payload={"scheduled_for": api.clock.now().isoformat()},
                    queue=report_jobs.QUEUE,
                ),
                now=api.clock.now(),
            )
    runner = Worker(
        worker_database, jobs.handlers(), WorkerConfig(queue=report_jobs.QUEUE), api.clock
    )
    while await runner.run_once():
        pass


async def test_a_schedule_takes_a_second_person_and_emails_a_link_once_per_period(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    reader = await api.staff("finance", name="Brian Okello")
    reader_email = await email_of(app_database, reader)
    proposer = await api.staff("admin")
    listed = await api.http.get("/staff/report-schedules")
    assert listed.status_code == 200, listed.text
    assert listed.json()["frequencies"]["daily_position"] == ["daily"]
    assert any(
        p["id"] == reader.id and "daily_position" in p["reports"] for p in listed.json()["people"]
    )

    schedule = await propose(
        api, report_key="daily_position", frequency="daily", currency="UGX", recipients=[reader.id]
    )
    assert schedule["status"] == "proposed"
    assert schedule["proposed_by"] == proposer.id
    assert schedule["recipient_names"] == ["Brian Okello"]
    status, body = await decide(api, schedule["id"], "approve")
    assert status == 403, body

    approver = await api.staff("admin", name="Joan Akello")
    inbox = await api.http.get("/staff/approvals")
    assert any(
        item["kind"] == "report_schedule" and item["id"] == schedule["id"] and item["can_decide"]
        for item in inbox.json()["items"]
    )
    status, body = await decide(api, schedule["id"], "approve")
    assert status == 200, body
    assert body["status"] == "active"
    assert body["decided_by"] == approver.id

    transport = RecordingTransport()
    await run_reports(worker_database, api, transport)
    await run_reports(worker_database, api, transport)
    sent = transport.to(reader_email)
    assert len(sent) == 1
    message = sent[0]
    assert not message.attachments
    yesterday = business_date(api.clock.now()) - timedelta(days=1)
    link = (
        f"{STAFF_ORIGIN}/reports?report=daily_position&from={yesterday.isoformat()}"
        f"&to={yesterday.isoformat()}&currency=UGX"
    )
    assert link.replace("&", "&amp;") in message.html
    assert link in message.text
    assert "Daily position (UGX)" in message.subject

    async with api.database.actor_transaction(approver) as session:
        events = await audit.events_for_resource(session, "report_schedule", str(schedule["id"]))
    assert {e.action for e in events} >= {
        "reports.schedule_proposed",
        "reports.schedule_approved",
    }


async def test_a_stopped_schedule_sends_nothing_more(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    reader = await api.staff("compliance", name="Ruth Namata")
    reader_email = await email_of(app_database, reader)
    await api.staff("admin")
    schedule = await propose(
        api, report_key="identity_and_risk", frequency="daily", recipients=[reader.id]
    )
    assert schedule["currency"] is None
    await api.staff("admin", name="Joan Akello")
    status, _ = await decide(api, schedule["id"], "approve")
    assert status == 200

    # Queued, then stopped before the email went.
    transport = RecordingTransport()
    jobs = report_jobs.ReportJobs(worker_database, api.clock, email=transport)
    async with worker_database.actor_transaction(SYSTEM) as session:
        await enqueue(
            session,
            JobRequest(
                kind=report_jobs.QUEUE_KIND,
                idempotency_key=f"reports.send_scheduled@{push_id()}",
                payload={"scheduled_for": api.clock.now().isoformat()},
                queue=report_jobs.QUEUE,
            ),
            now=api.clock.now(),
        )
    queuer = Worker(
        worker_database,
        {report_jobs.QUEUE_KIND: jobs.handlers()[report_jobs.QUEUE_KIND]},
        WorkerConfig(queue=report_jobs.QUEUE),
        api.clock,
    )
    while await queuer.run_once():
        pass
    status, body = await decide(api, schedule["id"], "stop")
    assert status == 200, body
    assert body["status"] == "stopped"
    await run_reports(worker_database, api, transport, queue=False)
    assert transport.to(reader_email) == []
    async with worker_database.actor_transaction(SYSTEM) as session:
        found = await session.execute(
            text("SELECT status, failure_code FROM report_sends WHERE schedule_id = :id"),
            {"id": schedule["id"]},
        )
        assert [tuple(row) for row in found.all()] == [("skipped", "schedule_stopped")]

    status, body = await decide(api, schedule["id"], "stop")
    assert status == 409, body


async def test_a_schedule_is_refused_for_someone_who_may_not_download_it(api: Api) -> None:
    support = await api.staff("support")
    await api.staff("admin")
    response = await api.http.post(
        "/staff/report-schedules",
        json={
            "report_key": "daily_position",
            "frequency": "daily",
            "recipients": [support.id],
            "reason": "Keep them in the loop",
        },
        headers={"Idempotency-Key": push_id()},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["fields"][0]["code"] == "recipient_may_not_download"

    response = await api.http.post(
        "/staff/report-schedules",
        json={
            "report_key": "daily_position",
            "frequency": "weekly",
            "recipients": [support.id],
            "reason": "Weekly instead",
        },
        headers={"Idempotency-Key": push_id()},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["fields"][0]["code"] == "frequency_not_allowed"


async def test_the_proposer_withdraws_and_only_a_second_person_declines(api: Api) -> None:
    reader = await api.staff("finance")
    proposer = await api.staff("admin")
    first = await propose(
        api, report_key="interest_and_tax", frequency="monthly", recipients=[reader.id]
    )
    status, body = await decide(api, first["id"], "decline")
    assert status == 403, body
    status, body = await decide(api, first["id"], "withdraw")
    assert status == 200 and body["status"] == "withdrawn", body

    second = await propose(
        api, report_key="interest_and_tax", frequency="monthly", recipients=[reader.id]
    )
    await api.staff("admin", name="Joan Akello")
    status, body = await decide(api, second["id"], "withdraw")
    assert status == 403, body
    status, body = await decide(api, second["id"], "decline")
    assert status == 200 and body["status"] == "declined", body
    await api.as_staff(proposer)
    status, body = await decide(api, second["id"], "approve")
    assert status in (403, 409), body


async def test_the_database_refuses_an_approval_by_the_proposer(
    api: Api, app_database: Database
) -> None:
    reader = await api.staff("finance")
    proposer = await api.staff("admin")
    schedule = await propose(
        api, report_key="interest_and_tax", frequency="monthly", recipients=[reader.id]
    )
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(proposer) as session:
            await session.execute(
                text(
                    "UPDATE report_schedules SET status = 'active', decided_by = :me, "
                    "decided_at = now(), decision_reason = 'mine' WHERE id = :id"
                ),
                {"me": proposer.id, "id": schedule["id"]},
            )
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(proposer) as session:
            await session.execute(
                text(
                    "UPDATE report_schedules SET recipients = ARRAY[:me]::push_id[] WHERE id = :id"
                ),
                {"me": proposer.id, "id": schedule["id"]},
            )


async def test_staff_contacts_answer_only_staff_and_the_worker(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    reader = await api.staff("finance")
    ask = text("SELECT email FROM identity_staff_contacts(ARRAY[:id])")
    async with app_database.actor_transaction(reader) as session:
        assert (await session.execute(ask, {"id": reader.id})).scalar_one() == await email_of(
            app_database, reader
        )
    async with worker_database.transaction() as session:
        assert (await session.execute(ask, {"id": reader.id})).scalar_one()
    # A client, or the API role claiming to be the system, is refused.
    for actor in (Actor("client", push_id()), SYSTEM):
        with pytest.raises(DBAPIError, match="staff contacts are for staff and the worker"):
            async with app_database.actor_transaction(actor) as session:
                await session.execute(ask, {"id": reader.id})
