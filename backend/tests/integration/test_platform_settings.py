"""Platform settings, set by two people, against real PostgreSQL and through the routes."""

import asyncio
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.app import create_app
from app.infrastructure.database import Database
from app.modules.audit.service import events_for_resource
from app.modules.identity.api import COOKIES
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import IdentityService
from app.modules.platform_settings import service
from app.modules.platform_settings.domain.catalogue import (
    MaturityReminders,
    StaffIdleMinutes,
)
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.database import TestDatabase
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff
from tests.support.settings import clear_settings, set_setting

pytestmark = pytest.mark.integration

ANYWHERE = service.ApprovalCheck(None)
NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def clean(migrator_database: Database) -> AsyncIterator[None]:
    await clear_settings(migrator_database)
    yield
    await clear_settings(migrator_database)


async def propose(
    database: Database,
    staff: Actor,
    clock: FakeClock,
    value: object = 15,
    key: str = "staff_idle_minutes",
    check: service.ApprovalCheck = ANYWHERE,
) -> service.Proposal:
    async with database.actor_transaction(staff) as session:
        return await service.propose(
            session,
            staff,
            key,  # type: ignore[arg-type]
            value,
            "Shorter sessions on shared desks",
            clock,
            check=check,
        )


async def decide(
    database: Database,
    staff: Actor,
    proposal_id: str,
    decision: str,
    clock: FakeClock,
    check: service.ApprovalCheck = ANYWHERE,
) -> None:
    async with database.actor_transaction(staff) as session:
        await service.decide(
            session,
            staff,
            proposal_id,
            decision,  # type: ignore[arg-type]
            "Agreed at the risk meeting",
            clock,
            check=check,
        )


async def idle(database: Database, actor: Actor, clock: FakeClock) -> StaffIdleMinutes:
    async with database.actor_transaction(actor) as session:
        return await service.staff_idle_minutes(session, clock.now())


async def test_a_value_is_used_only_after_a_second_person_approves(
    app_database: Database, fake_clock: FakeClock
) -> None:
    maker, checker = new_staff(), new_staff()
    assert await idle(app_database, maker, fake_clock) == StaffIdleMinutes(30)
    first = await propose(app_database, maker, fake_clock)
    assert await idle(app_database, maker, fake_clock) == StaffIdleMinutes(30)
    fake_clock.advance(minutes=1)
    await decide(app_database, checker, first.id, "approved", fake_clock)
    # Read with no one signed in, as a staff session's idle time is.
    async with app_database.transaction() as session:
        assert await service.staff_idle_minutes(session, fake_clock.now()) == StaffIdleMinutes(15)

    fake_clock.advance(minutes=1)
    second = await propose(app_database, checker, fake_clock, 20)
    async with app_database.actor_transaction(maker) as session:
        listed = {s.key: s for s in await service.list_settings(session, fake_clock.now())}
    setting = listed["staff_idle_minutes"]
    assert setting.value == StaffIdleMinutes(15) and setting.changed_by == checker.id
    assert setting.proposal is not None and setting.proposal.value == StaffIdleMinutes(20)
    untouched = listed["maturity_reminders"]
    assert untouched.value == MaturityReminders((7, 1)) and untouched.changed_at is None

    fake_clock.advance(minutes=1)
    await decide(app_database, maker, second.id, "approved", fake_clock)
    assert await idle(app_database, maker, fake_clock) == StaffIdleMinutes(20)

    async with app_database.actor_transaction(maker) as session:
        trail = await events_for_resource(session, service.RESOURCE, second.id)
    assert [(e.action, e.category, e.actor_id) for e in reversed(trail)] == [
        ("settings.staff_idle_minutes_proposed", "change", checker.id),
        ("settings.staff_idle_minutes_approved", "approval", maker.id),
    ]
    assert all(e.details == {"key": "staff_idle_minutes"} for e in trail)


async def test_nobody_approves_their_own_value_even_by_writing_the_row(
    app_database: Database, fake_clock: FakeClock
) -> None:
    maker = new_staff()
    proposal = await propose(app_database, maker, fake_clock)
    for decision in ("approved", "rejected"):
        with pytest.raises(ApplicationError) as raised:
            await decide(app_database, maker, proposal.id, decision, fake_clock)
        assert raised.value.code == "forbidden"
    with pytest.raises(DBAPIError, match="ck_platform_settings_two_people"):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE platform_settings SET status = 'approved', decided_by = :staff, "
                    "decided_at = now(), decision_reason = 'mine' WHERE id = :id"
                ),
                {"staff": maker.id, "id": proposal.id},
            )
    with pytest.raises(DBAPIError, match="decided once, by the acting member of staff"):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE platform_settings SET status = 'approved', decided_by = :staff, "
                    "decided_at = now(), decision_reason = 'theirs' WHERE id = :id"
                ),
                {"staff": new_staff().id, "id": proposal.id},
            )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text("UPDATE platform_settings SET value = '5' WHERE id = :id"),
                {"id": proposal.id},
            )
    with pytest.raises(ApplicationError) as raised:
        await decide(app_database, new_staff(), proposal.id, "withdrawn", fake_clock)
    assert raised.value.code == "forbidden"
    await decide(app_database, maker, proposal.id, "withdrawn", fake_clock)
    assert await idle(app_database, maker, fake_clock) == StaffIdleMinutes(30)


async def test_one_change_waits_per_setting_and_a_decision_is_final(
    app_database: Database, fake_clock: FakeClock
) -> None:
    maker, checker = new_staff(), new_staff()
    proposal = await propose(app_database, maker, fake_clock)
    with pytest.raises(ApplicationError) as raised:
        await propose(app_database, checker, fake_clock, 45)
    assert raised.value.code == "conflict"
    other = await propose(app_database, checker, fake_clock, [3], key="maturity_reminders")
    await decide(app_database, maker, other.id, "rejected", fake_clock)
    await decide(app_database, checker, proposal.id, "rejected", fake_clock)
    with pytest.raises(ApplicationError) as raised:
        await decide(app_database, checker, proposal.id, "approved", fake_clock)
    assert raised.value.code == "not_found"
    with pytest.raises(DBAPIError, match="a decided setting never changes"):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(
                text("UPDATE platform_settings SET status = 'approved' WHERE id = :id"),
                {"id": proposal.id},
            )
    # A rejected change frees the setting for the next proposal.
    await propose(app_database, maker, fake_clock)


async def test_two_approvals_racing_decide_once(
    app_database: Database, fake_clock: FakeClock, concurrency_barrier: AsyncBarrier
) -> None:
    proposal = await propose(app_database, new_staff(), fake_clock)

    async def approve() -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await concurrency_barrier.wait()
                await service.decide(
                    session, staff, proposal.id, "approved", "Agreed", fake_clock, check=ANYWHERE
                )
        except ApplicationError as error:
            return error.code
        return "approved"

    results = await asyncio.gather(approve(), approve())
    assert sorted(results) == ["approved", "not_found"]


async def test_two_proposals_racing_for_one_setting_leave_one_waiting(
    app_database: Database, fake_clock: FakeClock, concurrency_barrier: AsyncBarrier
) -> None:
    async def put_forward(minutes: int) -> str:
        staff = new_staff()
        try:
            async with app_database.actor_transaction(staff) as session:
                await concurrency_barrier.wait()
                await service.propose(
                    session,
                    staff,
                    "staff_idle_minutes",
                    minutes,
                    "Why",
                    fake_clock,
                    check=ANYWHERE,
                )
        except ApplicationError as error:
            return error.code
        return "proposed"

    results = await asyncio.gather(put_forward(10), put_forward(20))
    assert sorted(results) == ["conflict", "proposed"]
    async with app_database.actor_transaction(new_staff()) as session:
        assert len(await service.waiting(session)) == 1


async def test_bad_values_and_missing_reasons_are_refused(
    app_database: Database, fake_clock: FakeClock
) -> None:
    staff = new_staff()
    with pytest.raises(ApplicationError) as raised:
        await propose(app_database, staff, fake_clock, 2)
    assert raised.value.code == "validation_failed"
    assert [(f.field_path, f.code) for f in raised.value.fields] == [(["value"], "invalid")]
    with pytest.raises(ApplicationError) as raised:
        async with app_database.actor_transaction(staff) as session:
            await service.propose(
                session, staff, "staff_idle_minutes", 15, "  ", fake_clock, check=ANYWHERE
            )
    assert [(f.field_path, f.code) for f in raised.value.fields] == [(["reason"], "required")]


async def test_office_networks_that_leave_out_the_proposer_or_approver_are_refused(
    app_database: Database, fake_clock: FakeClock
) -> None:
    office = [{"network": "196.43.130.0/24", "label": "Head office"}]
    at_home = service.ApprovalCheck("41.210.140.5")
    at_office = service.ApprovalCheck("196.43.130.9")
    with pytest.raises(ApplicationError) as raised:
        await propose(app_database, new_staff(), fake_clock, office, "office_networks", at_home)
    assert raised.value.code == "conflict"
    assert [f.code for f in raised.value.fields] == ["leaves_you_out"]
    proposal = await propose(
        app_database, new_staff(), fake_clock, office, "office_networks", at_office
    )
    checker = new_staff()
    with pytest.raises(ApplicationError) as raised:
        await decide(app_database, checker, proposal.id, "approved", fake_clock, at_home)
    assert [f.code for f in raised.value.fields] == ["leaves_you_out"]
    # Rejecting from anywhere is fine: it changes nothing.
    await decide(app_database, checker, proposal.id, "rejected", fake_clock, at_home)


async def test_only_staff_set_a_value_and_others_see_only_what_was_approved(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    await set_setting(app_database, "maturity_reminders", [3], fake_clock)
    waiting = await propose(app_database, new_staff(), fake_clock, [5], key="maturity_reminders")
    for actor in (new_client(), SYSTEM):
        with pytest.raises(DBAPIError, match="only staff change a setting"):
            async with app_database.actor_transaction(actor) as session:
                await session.execute(
                    text(
                        "INSERT INTO platform_settings (id, key, value, reason, proposed_by, "
                        "proposed_at) VALUES ('-Nabc0000000000000Z1', 'staff_idle_minutes', "
                        "'15', 'x', '-Nabc0000000000000Z2', now())"
                    )
                )
    async with app_database.actor_transaction(new_client()) as session:
        rows = (await session.execute(text("SELECT id, status FROM platform_settings"))).all()
        assert [row.status for row in rows] == ["approved"]
        assert waiting.id not in {row.id for row in rows}
        assert await service.maturity_reminders(session, fake_clock.now()) == MaturityReminders(
            (3,)
        )
    async with worker_database.actor_transaction(SYSTEM) as session:
        assert await service.maturity_reminders(session, fake_clock.now()) == MaturityReminders(
            (3,)
        )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_database.actor_transaction(new_staff()) as session:
            await session.execute(text("DELETE FROM platform_settings"))


# Routes ---------------------------------------------------------------------------------------

HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
STAFF_ORIGIN = "https://admin.finvestug.com"


@dataclass
class Api:
    app: object
    identity: IdentityService
    database: Database
    clock: FakeClock

    def http(self, address: str = "127.0.0.1") -> httpx.AsyncClient:
        transport = httpx.ASGITransport(
            app=self.app,  # type: ignore[arg-type]
            raise_app_exceptions=False,
            client=(address, 1),
        )
        return httpx.AsyncClient(transport=transport, base_url="https://api")

    async def staff(self, http: httpx.AsyncClient, role: str = "admin") -> Actor:
        staff_id = await self.identity.invite_staff(
            email=f"staff-{secrets.token_hex(6)}@finvestug.com",
            name="Grace Auma",
            role=role,  # type: ignore[arg-type]
            created_by=None,
        )
        async with self.database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE identity_staff SET password_hash = 'x', status = 'active', "
                    "password_changed_at = created_at WHERE id = :id"
                ),
                {"id": staff_id},
            )
        actor = Actor("staff", staff_id)
        issued = await self.identity.issue_session(actor, second_step="passkey", transport="cookie")
        http.cookies.set(COOKIES["staff"], issued.token)
        http.headers["Origin"] = STAFF_ORIGIN
        return actor


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    settings = test_database.settings("finvest_app").model_copy(
        update={"staff_allowed_origins": [STAFF_ORIGIN]}
    )
    clock = FakeClock(datetime.now(UTC))
    app = create_app(settings, app_database, clock, HASHER)
    yield Api(app, app.state.identity, app_database, clock)


async def test_staff_list_propose_and_approve_a_setting_through_the_routes(api: Api) -> None:
    async with api.http() as maker, api.http() as checker:
        await api.staff(maker, "operations")
        await api.staff(checker, "finance")
        listed = (await maker.get("/staff/settings/platform")).json()
        by_key = {s["key"]: s for s in listed["settings"]}
        assert by_key["payment_channels"]["value"]["airtel_payout"] is True
        assert by_key["payment_channels"]["can_change"] is True
        assert by_key["staff_idle_minutes"]["can_change"] is False
        assert by_key["staff_idle_minutes"]["section"] == "staff_security"
        assert listed["your_address"] == "127.0.0.1"
        assert "DD/MM/YYYY" in listed["date_formats"]

        channels = by_key["payment_channels"]["value"] | {"airtel_payout": False}
        proposed = await maker.post(
            "/staff/settings/platform/proposals",
            json={"key": "payment_channels", "value": channels, "reason": "Airtel is down"},
        )
        assert proposed.status_code == 201, proposed.text
        proposal = proposed.json()
        assert proposal["key"] == "payment_channels" and proposal["proposal"]["yours"] is True
        inbox = (await checker.get("/staff/approvals")).json()["items"]
        [item] = [i for i in inbox if i["id"] == proposal["proposal"]["id"]]
        assert (item["kind"], item["subject"], item["can_decide"]) == (
            "payment_channels_change",
            "payment_channels",
            True,
        )

        url = f"/staff/settings/platform/proposals/{proposal['proposal']['id']}/decision"
        own = await maker.post(url, json={"decision": "approved", "reason": "Mine"})
        assert own.status_code == 403
        approved = await checker.post(url, json={"decision": "approved", "reason": "Checked"})
        assert approved.status_code == 200, approved.text
        listed = (await maker.get("/staff/settings/platform")).json()
        [setting] = [s for s in listed["settings"] if s["key"] == "payment_channels"]
        assert setting["value"]["airtel_payout"] is False and setting["proposal"] is None


async def test_a_section_is_changed_only_with_its_own_permission(api: Api) -> None:
    async with api.http() as finance, api.http() as support, api.http() as admin:
        await api.staff(finance, "finance")
        await api.staff(support, "support")
        await api.staff(admin, "admin")
        body = {"key": "staff_idle_minutes", "value": 15, "reason": "Shorter"}
        assert (await support.get("/staff/settings/platform")).status_code == 403
        refused = await finance.post("/staff/settings/platform/proposals", json=body)
        assert refused.status_code == 403
        proposed = await admin.post("/staff/settings/platform/proposals", json=body)
        assert proposed.status_code == 201
        url = f"/staff/settings/platform/proposals/{proposed.json()['proposal']['id']}/decision"
        decided = await finance.post(url, json={"decision": "approved", "reason": "Fine"})
        assert decided.status_code == 403
        bad = await admin.post(
            "/staff/settings/platform/proposals",
            json={"key": "bank_payout_cutoff", "value": "19:00", "reason": "Late"},
        )
        assert bad.status_code == 422
        assert bad.json()["error"]["fields"] == [
            {"field_path": ["value"], "code": "outside_banking_hours"}
        ]
        unknown = await admin.post(
            "/staff/settings/platform/proposals",
            json={"key": "wallet", "value": 1, "reason": "No"},
        )
        assert unknown.status_code == 422


async def test_deciding_needs_a_recent_second_step(api: Api) -> None:
    async with api.http() as maker, api.http() as checker:
        await api.staff(maker)
        await api.staff(checker)
        proposed = await maker.post(
            "/staff/settings/platform/proposals",
            json={"key": "maturity_reminders", "value": [3], "reason": "Fewer texts"},
        )
        api.clock.advance(minutes=11)
        url = f"/staff/settings/platform/proposals/{proposed.json()['proposal']['id']}/decision"
        late = await checker.post(url, json={"decision": "approved", "reason": "Fine"})
        assert late.status_code == 403
        assert late.json()["error"]["code"] == "confirm_it_is_you"
