"""Announcements: two people agree the words, then the worker tells every active client once."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.jobs import Worker, WorkerConfig
from app.modules.audit.service import events_for_resource
from app.modules.identity.api import COOKIES
from app.modules.identity.domain.permissions import ROLE_PERMISSIONS
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import IdentityService
from app.modules.notifications import announcements, preferences
from app.modules.notifications.announcements import AnnouncementJobs
from app.modules.notifications.domain.events import Category, Channel
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.database import TestDatabase
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

TITLE = "Office closed on Monday"
MESSAGE = "Our Kampala office is closed on Monday for the holiday. The app works as usual."
PASSWORD = "copper-harbour-violin"
HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
STAFF_ORIGIN = "https://admin.finvestug.com"


async def proposed(database: Database, clock: FakeClock) -> tuple[Actor, str]:
    proposer = new_staff()
    async with database.actor_transaction(proposer) as session:
        item = await announcements.propose(session, proposer, TITLE, MESSAGE, "Holiday", clock)
    return proposer, item.id


async def decide(
    database: Database,
    staff: Actor,
    announcement_id: str,
    decision: announcements.Decision,
    clock: FakeClock,
    reason: str | None = None,
) -> announcements.Announcement:
    async with database.actor_transaction(staff) as session:
        return await announcements.decide(session, staff, announcement_id, decision, reason, clock)


async def told(database: Database, client_id: str, announcement_id: str) -> list[tuple[str, str]]:
    async with database.actor_transaction(Actor("client", client_id)) as session:
        rows = await session.execute(
            text(
                "SELECT title, body FROM notifications WHERE client_id = :client "
                "AND event = 'announcement' AND idempotency_key = :key"
            ),
            {"client": client_id, "key": announcement_id},
        )
        return [(row.title, row.body) for row in rows]


async def run_jobs(worker_database: Database, clock: FakeClock) -> None:
    runner = Worker(
        worker_database,
        AnnouncementJobs(worker_database, clock).handlers(),
        WorkerConfig(),
        clock,
    )
    while await runner.run_once():
        pass


@pytest.fixture
def identity(
    test_database: TestDatabase, app_database: Database, fake_clock: FakeClock
) -> IdentityService:
    return IdentityService(app_database, fake_clock, test_database.settings("finvest_app"), HASHER)


async def active_client(identity: IdentityService) -> str:
    return await identity.create_client(
        phone=f"+2567{uuid.uuid4().int % 10**8:08d}", email=None, password=PASSWORD
    )


# Sending -----------------------------------------------------------------------------------------


async def test_an_approved_announcement_tells_every_active_client_once(
    app_database: Database,
    worker_database: Database,
    identity: IdentityService,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Small pages, so the worker has to carry on from one page to the next.
    monkeypatch.setattr(announcements, "PAGE", 2)
    clients = [await active_client(identity) for _ in range(3)]
    proposer, announcement_id = await proposed(app_database, fake_clock)
    # Nothing is sent while it waits for a second person.
    await run_jobs(worker_database, fake_clock)
    assert await told(app_database, clients[0], announcement_id) == []

    approved = await decide(app_database, new_staff(), announcement_id, "approved", fake_clock)
    assert approved.status == "approved"
    await run_jobs(worker_database, fake_clock)
    for client_id in clients:
        assert await told(app_database, client_id, announcement_id) == [(TITLE, MESSAGE)]
    async with worker_database.actor_transaction(SYSTEM) as session:
        statuses = {
            row.status
            for row in await session.execute(
                text(
                    "SELECT status FROM jobs WHERE kind = :kind "
                    "AND payload ->> 'announcement_id' = :id"
                ),
                {"kind": announcements.SEND, "id": announcement_id},
            )
        }
    assert statuses == {"succeeded"}
    # A page run again tells nobody twice.
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await announcements.send_page(session, announcement_id, None, fake_clock) == 0
    async with app_database.actor_transaction(proposer) as session:
        (listed,) = [
            item
            for item in await announcements.list_announcements(session)
            if item.id == announcement_id
        ]
    assert listed.told >= len(clients)


async def test_a_locked_client_is_not_told(
    app_database: Database,
    worker_database: Database,
    migrator_database: Database,
    identity: IdentityService,
    fake_clock: FakeClock,
) -> None:
    locked = await active_client(identity)
    # Locking takes two members of staff (migration 0152), which test_client_locks covers. This
    # test only needs a locked client, so the table owner sets the status with the guard off
    # for this one statement.
    guard = "TRIGGER tr_identity_clients_status_guard"
    async with migrator_database.transaction() as session:
        await session.execute(text(f"ALTER TABLE identity_clients DISABLE {guard}"))
        await session.execute(
            text("UPDATE identity_clients SET status = 'locked' WHERE id = :id"), {"id": locked}
        )
        await session.execute(text(f"ALTER TABLE identity_clients ENABLE {guard}"))
    _, announcement_id = await proposed(app_database, fake_clock)
    await decide(app_database, new_staff(), announcement_id, "approved", fake_clock)
    await run_jobs(worker_database, fake_clock)
    assert await told(app_database, locked, announcement_id) == []


# Two people --------------------------------------------------------------------------------------


async def test_the_proposer_never_approves_and_only_the_proposer_withdraws(
    app_database: Database, fake_clock: FakeClock
) -> None:
    proposer, announcement_id = await proposed(app_database, fake_clock)
    for staff, decision in (
        (proposer, "approved"),
        (proposer, "rejected"),
        (new_staff(), "withdrawn"),
    ):
        with pytest.raises(ApplicationError) as refused:
            await decide(app_database, staff, announcement_id, decision, fake_clock, "Because")  # type: ignore[arg-type]
        assert refused.value.code == "forbidden"
    # A rejection says why.
    with pytest.raises(ApplicationError) as refused:
        await decide(app_database, new_staff(), announcement_id, "rejected", fake_clock)
    assert refused.value.code == "validation_failed"
    withdrawn = await decide(app_database, proposer, announcement_id, "withdrawn", fake_clock)
    assert withdrawn.status == "withdrawn" and withdrawn.decided_by == proposer.id
    # Decided once.
    with pytest.raises(ApplicationError) as refused:
        await decide(app_database, new_staff(), announcement_id, "approved", fake_clock)
    assert refused.value.code == "not_found"
    async with app_database.actor_transaction(proposer) as session:
        actions = [
            event.action
            for event in await events_for_resource(session, announcements.RESOURCE, announcement_id)
        ]
    # Newest first.
    assert actions == [
        "notifications.announcement_withdrawn",
        "notifications.announcement_proposed",
    ]


async def test_a_rejection_keeps_its_reason_and_sends_nothing(
    app_database: Database,
    worker_database: Database,
    identity: IdentityService,
    fake_clock: FakeClock,
) -> None:
    client_id = await active_client(identity)
    _, announcement_id = await proposed(app_database, fake_clock)
    rejected = await decide(
        app_database, new_staff(), announcement_id, "rejected", fake_clock, "Say which Monday."
    )
    assert (rejected.status, rejected.decision_reason) == ("rejected", "Say which Monday.")
    await run_jobs(worker_database, fake_clock)
    assert await told(app_database, client_id, announcement_id) == []


async def test_two_staff_approving_at_once_send_it_once(
    app_database: Database,
    worker_database: Database,
    fake_clock: FakeClock,
    concurrency_barrier: AsyncBarrier,
) -> None:
    _, announcement_id = await proposed(app_database, fake_clock)

    async def approve() -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await concurrency_barrier.wait()
                await announcements.decide(
                    session, staff, announcement_id, "approved", None, fake_clock
                )
        except ApplicationError as error:
            return error.code
        return "approved"

    assert sorted(await asyncio.gather(approve(), approve())) == ["approved", "not_found"]
    async with worker_database.actor_transaction(SYSTEM) as session:
        jobs = await session.scalar(
            text(
                "SELECT count(*) FROM jobs WHERE kind = :kind "
                "AND payload ->> 'announcement_id' = :id"
            ),
            {"kind": announcements.SEND, "id": announcement_id},
        )
    assert jobs == 1


async def test_postgresql_holds_the_rules(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    proposer, announcement_id = await proposed(app_database, fake_clock)
    insert = text(
        "INSERT INTO notification_announcements (id, title, message, reason, proposed_by, "
        "proposed_at) VALUES (:id, 'T', 'M', 'R', :staff, now())"
    )
    decide_sql = text(
        "UPDATE notification_announcements SET status = :status, decided_by = :staff, "
        "decided_at = now(), decision_reason = :reason WHERE id = :id"
    )
    with pytest.raises(DBAPIError, match="only staff write an announcement"):
        async with app_database.actor_transaction(Actor("client", push_id())) as session:
            await session.execute(insert, {"id": push_id(), "staff": push_id()})
    with pytest.raises(DBAPIError, match="proposal by the acting member of staff"):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(insert, {"id": push_id(), "staff": push_id()})
    with pytest.raises(DBAPIError, match="ck_notification_announcements_two_people"):
        async with app_database.actor_transaction(proposer) as session:
            await session.execute(
                decide_sql,
                {"status": "approved", "staff": proposer.id, "reason": None, "id": announcement_id},
            )
    with pytest.raises(DBAPIError, match="ck_notification_announcements_reason"):
        async with app_database.actor_transaction(new_staff()) as session:
            staff_id = await session.scalar(text("SELECT finvest_actor_id()"))
            await session.execute(
                decide_sql,
                {"status": "rejected", "staff": staff_id, "reason": " ", "id": announcement_id},
            )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(proposer) as session:
            await session.execute(
                text("UPDATE notification_announcements SET message = 'Changed' WHERE id = :id"),
                {"id": announcement_id},
            )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(proposer) as session:
            await session.execute(
                text("DELETE FROM notification_announcements WHERE id = :id"),
                {"id": announcement_id},
            )
    # The worker sees an announcement only once it is approved.
    async with worker_database.actor_transaction(SYSTEM) as session:
        with pytest.raises(ApplicationError):
            await announcements.send_page(session, announcement_id, None, fake_clock)
    approver = new_staff()
    await decide(app_database, approver, announcement_id, "approved", fake_clock)
    with pytest.raises(DBAPIError, match="a decided announcement never changes"):
        async with app_database.actor_transaction(new_staff()) as session:
            staff_id = await session.scalar(text("SELECT finvest_actor_id()"))
            await session.execute(
                decide_sql,
                {"status": "rejected", "staff": staff_id, "reason": "No", "id": announcement_id},
            )


async def test_the_words_follow_the_wording_rules(
    app_database: Database, fake_clock: FakeClock
) -> None:
    staff = new_staff()
    for title, message, field, code in (
        (TITLE, "Read more at https://example.com/finvest", "message", "outside_link"),
        (TITLE, "Rates are up to 12% this week.", "message", "no_rates"),
        ("Hello {there", MESSAGE, "title", "stray_brace"),
        (TITLE, "Karibu ✓", "message", "invalid_characters"),
        (TITLE, "x" * 292, "message", "too_long"),
    ):
        with pytest.raises(ApplicationError) as refused:
            async with app_database.actor_transaction(staff) as session:
                await announcements.propose(session, staff, title, message, "Why", fake_clock)
        assert refused.value.code == "validation_failed"
        assert [(problem.field_path, problem.code) for problem in refused.value.fields] == [
            ([field], code)
        ]
    async with app_database.actor_transaction(staff) as session:
        ok = await announcements.propose(
            session, staff, TITLE, "Details at https://finvestug.com/help", "Why", fake_clock
        )
    assert ok.status == "proposed"


# Routes ------------------------------------------------------------------------------------------


@dataclass
class Api:
    http: httpx.AsyncClient
    identity: IdentityService
    database: Database

    async def staff(self, role: str = "admin") -> Actor:
        staff_id = await self.identity.invite_staff(
            email=f"staff-{uuid.uuid4().hex}@finvestug.com",
            name="Grace Auma",
            role=role,  # type: ignore[arg-type]
            created_by=None,
        )
        # Test set-up only: active, with a password. In the application only enrolment does this.
        async with self.database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE identity_staff SET password_hash = :hash, status = 'active', "
                    "password_changed_at = created_at WHERE id = :id"
                ),
                {"hash": await HASHER.hash(PASSWORD), "id": staff_id},
            )
        actor = Actor("staff", staff_id)
        issued = await self.identity.issue_session(actor, second_step="passkey", transport="cookie")
        self.http.cookies.set(COOKIES["staff"], issued.token)
        self.http.headers["Origin"] = STAFF_ORIGIN
        return actor


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    settings = test_database.settings("finvest_app").model_copy(
        update={"staff_allowed_origins": [STAFF_ORIGIN]}
    )
    app = create_app(settings, app_database, FakeClock(), HASHER)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as http:
        yield Api(http, app.state.identity, app_database)


async def test_staff_propose_and_approve_over_http(api: Api) -> None:
    body = {"title": TITLE, "message": MESSAGE, "reason": "Holiday"}
    await api.staff("support")
    assert (await api.http.post("/staff/announcements", json=body)).status_code == 403
    proposer = await api.staff()
    response = await api.http.post("/staff/announcements", json=body)
    assert response.status_code == 201
    created = response.json()
    assert (created["status"], created["yours"], created["proposed_by"]) == (
        "proposed",
        True,
        proposer.id,
    )
    decision = f"/staff/announcements/{created['id']}/decision"
    assert (await api.http.post(decision, json={"decision": "approved"})).status_code == 403
    await api.staff()
    listed = (await api.http.get("/staff/announcements")).json()["announcements"]
    (mine,) = [item for item in listed if item["id"] == created["id"]]
    assert mine["yours"] is False
    response = await api.http.post(decision, json={"decision": "approved"})
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    invalid = {"title": TITLE, "message": "See www.example.org", "reason": "Why"}
    response = await api.http.post("/staff/announcements", json=invalid)
    assert response.status_code == 422


async def test_changing_wordings_is_not_enough_to_send_an_announcement(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Give operations the wordings, as the owner might, and it still cannot tell every client.
    monkeypatch.setitem(
        ROLE_PERMISSIONS,
        "operations",
        ROLE_PERMISSIONS["operations"] | {"settings.view", "wordings.change"},
    )
    body = {"title": TITLE, "message": MESSAGE, "reason": "Holiday"}
    await api.staff()
    created = (await api.http.post("/staff/announcements", json=body)).json()
    await api.staff("operations")
    assert (await api.http.get("/staff/announcements")).status_code == 200
    assert (await api.http.post("/staff/announcements", json=body)).status_code == 403
    decision = f"/staff/announcements/{created['id']}/decision"
    assert (await api.http.post(decision, json={"decision": "approved"})).status_code == 403


# Reach -------------------------------------------------------------------------------------------


async def choose_news(app_database: Database, client_id: str, *channels: Channel) -> None:
    client = Actor("client", client_id)
    async with app_database.actor_transaction(client) as session:
        await preferences.save(
            session, client, {Category.NEWS: frozenset(channels)}, FakeClock().now()
        )


async def reach_now(app_database: Database) -> announcements.Reach:
    async with app_database.actor_transaction(new_staff()) as session:
        return await announcements.reach(session)


async def test_the_reach_counts_active_clients_and_their_choices_for_news(
    app_database: Database, migrator_database: Database, identity: IdentityService
) -> None:
    before = await reach_now(app_database)
    # News goes by push only unless a client chooses more.
    await active_client(identity)
    texted = await active_client(identity)
    await choose_news(app_database, texted, Channel.PUSH, Channel.SMS, Channel.EMAIL)
    emailed = await active_client(identity)
    await choose_news(app_database, emailed, Channel.EMAIL)
    locked = await active_client(identity)
    await choose_news(app_database, locked, Channel.SMS)
    guard = "TRIGGER tr_identity_clients_status_guard"
    async with migrator_database.transaction() as session:
        await session.execute(text(f"ALTER TABLE identity_clients DISABLE {guard}"))
        await session.execute(
            text("UPDATE identity_clients SET status = 'locked' WHERE id = :id"), {"id": locked}
        )
        await session.execute(text(f"ALTER TABLE identity_clients ENABLE {guard}"))

    after = await reach_now(app_database)

    # A locked client is not told, so their choice of SMS counts for nothing.
    assert (after.clients - before.clients, after.sms - before.sms) == (3, 1)
    assert after.email - before.email == 2


async def test_staff_who_see_settings_see_the_reach(api: Api) -> None:
    await api.staff("support")
    assert (await api.http.get("/staff/announcements/reach")).status_code == 403
    await api.staff()
    response = await api.http.get("/staff/announcements/reach")
    assert response.status_code == 200
    assert set(response.json()) == {"clients", "sms", "email"}
