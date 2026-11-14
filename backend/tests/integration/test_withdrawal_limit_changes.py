"""Changing the withdrawal limits by two people, against real PostgreSQL and through the routes.

A version never changes or goes away, so every test here starts its versions on days far in the
future that no other test uses.
"""

import asyncio
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

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
from app.modules.payouts import limit_changes
from app.modules.payouts.payouts import current_limits
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock
from tests.support.database import TestDatabase
from tests.support.ledger import client as new_client
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def far_day() -> date:
    """A start day no other test takes."""
    return date(2100, 1, 1) + timedelta(days=secrets.randbelow(300_000))


@pytest.fixture(autouse=True)
async def clean(migrator_database: Database) -> AsyncIterator[None]:
    # One change waits at a time, for the whole platform.
    async def clear() -> None:
        async with migrator_database.transaction() as session:
            await session.execute(text("TRUNCATE withdrawal_limit_changes"))

    await clear()
    yield
    await clear()


async def propose(
    database: Database,
    staff: Actor,
    clock: FakeClock,
    day: date,
    *,
    per_payout: int = 2_000_000,
    per_day: int | None = 4_000_000,
    review_above: int | None = 5_000_000,
) -> limit_changes.LimitsChange:
    async with database.actor_transaction(staff) as session:
        return await limit_changes.propose(
            session,
            staff,
            effective_from=day,
            mobile_money_per_payout=ugx(per_payout),
            mobile_money_per_day=None if per_day is None else ugx(per_day),
            bank_per_payout=None,
            bank_per_day=None,
            review_above=None if review_above is None else ugx(review_above),
            reason="Lower limits after the fraud review",
            clock=clock,
        )


async def decide(
    database: Database, staff: Actor, change_id: str, decision: str, clock: FakeClock
) -> None:
    async with database.actor_transaction(staff) as session:
        await limit_changes.decide(
            session,
            staff,
            change_id,
            decision,  # type: ignore[arg-type]
            "Agreed at the risk meeting",
            clock,
        )


async def test_a_version_starts_on_its_day_only_after_a_second_person_approves(
    app_database: Database, fake_clock: FakeClock
) -> None:
    maker, checker = new_staff(), new_staff()
    day = far_day()
    change = await propose(app_database, maker, fake_clock, day)
    async with app_database.actor_transaction(checker) as session:
        assert (await limit_changes.waiting(session)) == change
        before = await current_limits(session, day)
    assert before.effective_from < day

    fake_clock.advance(minutes=1)
    await decide(app_database, checker, change.id, "approved", fake_clock)
    async with app_database.actor_transaction(checker) as session:
        assert await limit_changes.waiting(session) is None
        on_the_day = await current_limits(session, day)
        the_day_before = await current_limits(session, day - timedelta(days=1))
        row = (
            await session.execute(
                text(
                    "SELECT l.proposed_by, l.approved_by, c.status FROM withdrawal_limits l "
                    "JOIN withdrawal_limit_changes c ON c.limits_id = l.id WHERE c.id = :id"
                ),
                {"id": change.id},
            )
        ).one()
        trail = await events_for_resource(session, limit_changes.RESOURCE, change.id)
    assert on_the_day == change.limits
    assert the_day_before.effective_from < day
    assert tuple(row) == (maker.id, checker.id, "approved")
    assert [(e.action, e.category, e.actor_id) for e in reversed(trail)] == [
        ("payouts.limits_proposed", "change", maker.id),
        ("payouts.limits_approved", "approval", checker.id),
    ]
    assert all(e.details == {"effective_from": day.isoformat()} for e in trail)


async def test_nobody_approves_their_own_limits_even_by_writing_the_rows(
    app_database: Database, fake_clock: FakeClock
) -> None:
    maker, other = new_staff(), new_staff()
    change = await propose(app_database, maker, fake_clock, far_day())
    for decision in ("approved", "rejected"):
        with pytest.raises(ApplicationError) as raised:
            await decide(app_database, maker, change.id, decision, fake_clock)
        assert raised.value.code == "forbidden"
    with pytest.raises(ApplicationError) as raised:
        await decide(app_database, other, change.id, "withdrawn", fake_clock)
    assert raised.value.code == "forbidden"

    with pytest.raises(DBAPIError, match="ck_withdrawal_limit_changes_two_people"):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text(
                    "UPDATE withdrawal_limit_changes SET status = 'rejected', decided_by = :staff, "
                    "decided_at = now(), decision_reason = 'Mine' WHERE id = :id"
                ),
                {"staff": maker.id, "id": change.id},
            )
    # An approval must name a version with exactly these limits, made by the approver.
    with pytest.raises(DBAPIError, match="names the version it made"):
        async with app_database.actor_transaction(other) as session:
            await session.execute(
                text(
                    "UPDATE withdrawal_limit_changes SET status = 'approved', decided_by = :staff, "
                    "decided_at = now(), decision_reason = 'Fine', "
                    "limits_id = (SELECT id FROM withdrawal_limits ORDER BY effective_from "
                    "LIMIT 1) WHERE id = :id"
                ),
                {"staff": other.id, "id": change.id},
            )
    # Deciding in someone else's name is refused.
    with pytest.raises(DBAPIError, match="decided once, by the acting member of staff"):
        async with app_database.actor_transaction(other) as session:
            await session.execute(
                text(
                    "UPDATE withdrawal_limit_changes SET status = 'rejected', decided_by = :staff, "
                    "decided_at = now(), decision_reason = 'Fine' WHERE id = :id"
                ),
                {"staff": new_staff().id, "id": change.id},
            )

    await decide(app_database, maker, change.id, "withdrawn", fake_clock)
    with pytest.raises(ApplicationError) as raised:
        await decide(app_database, other, change.id, "approved", fake_clock)
    assert raised.value.code == "not_found"
    with pytest.raises(DBAPIError, match="never changes"):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text("UPDATE withdrawal_limit_changes SET status = 'proposed' WHERE id = :id"),
                {"id": change.id},
            )
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(maker) as session:
            await session.execute(
                text("DELETE FROM withdrawal_limit_changes WHERE id = :id"), {"id": change.id}
            )


_DIRECT = text(
    "INSERT INTO withdrawal_limits (id, effective_from, currency, mobile_money_per_payout, "
    "mobile_money_per_day, review_above, proposed_by, approved_by, reason, created_at) VALUES "
    "(:id, :day, 'UGX', :per_payout, :per_day, :review_above, :proposed_by, :approved_by, "
    "'Direct', now())"
)


async def test_a_version_is_only_ever_the_approval_of_a_waiting_change(
    app_database: Database, fake_clock: FakeClock
) -> None:
    maker, checker = new_staff(), new_staff()
    day = far_day()

    async def insert(by: Actor, proposed_by: str | None, per_payout: int = 2_000_000) -> None:
        async with app_database.actor_transaction(by) as session:
            await session.execute(
                _DIRECT,
                {
                    "id": push_id(),
                    "day": day,
                    "per_payout": per_payout,
                    "per_day": 4_000_000,
                    "review_above": 5_000_000,
                    "proposed_by": proposed_by,
                    "approved_by": by.id,
                },
            )

    # One person, naming a colleague as proposer, with no change waiting.
    with pytest.raises(DBAPIError, match="row-level security"):
        await insert(checker, maker.id)
    change = await propose(app_database, maker, fake_clock, day)
    # Other limits than the ones waiting, or the proposer approving their own.
    with pytest.raises(DBAPIError, match="row-level security"):
        await insert(checker, maker.id, per_payout=3_000_000)
    with pytest.raises(DBAPIError, match="row-level security"):
        await insert(maker, maker.id)
    await decide(app_database, checker, change.id, "approved", fake_clock)


async def test_a_version_starts_on_a_later_day_and_one_change_waits_at_a_time(
    app_database: Database, fake_clock: FakeClock
) -> None:
    maker, checker = new_staff(), new_staff()
    today = business_date(fake_clock.now())
    for day in (today, today - timedelta(days=1)):
        with pytest.raises(ApplicationError) as raised:
            await propose(app_database, maker, fake_clock, day)
        assert raised.value.code == "validation_failed"
        assert raised.value.fields[0].code == "not_a_later_day"
    with pytest.raises(ApplicationError) as raised:
        await propose(app_database, maker, fake_clock, far_day(), per_day=1_000_000)
    assert raised.value.fields[0].code == "limits_invalid"

    tomorrow = today + timedelta(days=1)
    change = await propose(app_database, maker, fake_clock, tomorrow)
    with pytest.raises(ApplicationError) as raised:
        await propose(app_database, checker, fake_clock, far_day())
    assert raised.value.code == "conflict"

    # Tomorrow has come before anyone approved: the version would start part way through a day.
    fake_clock.advance(days=1)
    with pytest.raises(ApplicationError) as raised:
        await decide(app_database, checker, change.id, "approved", fake_clock)
    assert (raised.value.code, raised.value.fields[0].code) == ("conflict", "not_a_later_day")
    await decide(app_database, checker, change.id, "rejected", fake_clock)


async def test_a_day_that_already_has_a_version_is_refused(
    app_database: Database, fake_clock: FakeClock
) -> None:
    maker, checker = new_staff(), new_staff()
    day = far_day()
    first = await propose(app_database, maker, fake_clock, day)
    await decide(app_database, checker, first.id, "approved", fake_clock)
    with pytest.raises(ApplicationError) as raised:
        await propose(app_database, maker, fake_clock, day)
    assert (raised.value.code, raised.value.fields[0].code) == ("conflict", "day_taken")


async def test_two_approvals_racing_make_one_version(
    app_database: Database, fake_clock: FakeClock
) -> None:
    maker = new_staff()
    day = far_day()
    change = await propose(app_database, maker, fake_clock, day)

    async def approve() -> str:
        try:
            await decide(app_database, new_staff(), change.id, "approved", fake_clock)
        except ApplicationError as error:
            return error.code
        return "approved"

    outcomes = sorted(await asyncio.gather(approve(), approve()))
    assert outcomes == ["approved", "not_found"]
    async with app_database.actor_transaction(maker) as session:
        count = await session.scalar(
            text("SELECT count(*) FROM withdrawal_limits WHERE effective_from = :day"),
            {"day": day},
        )
    assert count == 1


async def test_two_proposals_racing_leave_one_waiting(
    app_database: Database, fake_clock: FakeClock
) -> None:
    async def put_forward() -> str:
        try:
            await propose(app_database, new_staff(), fake_clock, far_day())
        except ApplicationError as error:
            return error.code
        return "proposed"

    assert sorted(await asyncio.gather(put_forward(), put_forward())) == ["conflict", "proposed"]


async def test_only_staff_see_or_propose_changes(
    app_database: Database, fake_clock: FakeClock
) -> None:
    await propose(app_database, new_staff(), fake_clock, far_day())
    client = new_client()
    async with app_database.actor_transaction(client) as session:
        assert await limit_changes.waiting(session) is None
    with pytest.raises(ApplicationError) as raised:
        await propose(app_database, client, fake_clock, far_day())
    assert raised.value.code == "forbidden"


# --- Through the routes -----------------------------------------------------------------------

HASHER = PasswordHasher(memory_kib=19_456, time_cost=2)
STAFF_ORIGIN = "https://admin.finvestug.com"


@dataclass
class Api:
    app: object
    identity: IdentityService
    database: Database
    clock: FakeClock

    def http(self) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(
            app=self.app,  # type: ignore[arg-type]
            raise_app_exceptions=False,
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


async def test_staff_propose_and_approve_limits_through_the_routes(api: Api) -> None:
    day = far_day()
    async with api.http() as maker, api.http() as checker, api.http() as operations:
        await api.staff(maker, "finance")
        await api.staff(checker, "admin")
        await api.staff(operations, "operations")
        screen = (await operations.get("/staff/withdrawal-limits")).json()
        assert screen["can_change"] is False and screen["change"] is None
        assert screen["in_force"]["mobile_money_per_payout_minor"] >= 1
        body = {
            "effective_from": day.isoformat(),
            "mobile_money_per_payout_minor": 2_000_000,
            "mobile_money_per_day_minor": 4_000_000,
            "review_above_minor": 5_000_000,
            "reason": "Lower limits after the fraud review",
        }
        refused = await operations.post("/staff/withdrawal-limits/changes", json=body)
        assert refused.status_code == 403
        too_big = await maker.post(
            "/staff/withdrawal-limits/changes",
            json=body | {"mobile_money_per_payout_minor": 5_000_001},
        )
        assert too_big.status_code == 422

        proposed = await maker.post("/staff/withdrawal-limits/changes", json=body)
        assert proposed.status_code == 201, proposed.text
        change = proposed.json()
        assert change["yours"] is True and change["limits"]["effective_from"] == day.isoformat()
        inbox = (await checker.get("/staff/approvals")).json()["items"]
        [item] = [i for i in inbox if i["id"] == change["id"]]
        assert (item["kind"], item["subject"], item["can_decide"]) == (
            "withdrawal_limits_change",
            day.isoformat(),
            True,
        )
        seen = (await operations.get("/staff/approvals")).json()["items"]
        [item] = [i for i in seen if i["id"] == change["id"]]
        assert item["can_decide"] is False

        url = f"/staff/withdrawal-limits/changes/{change['id']}/decision"
        own = await maker.post(url, json={"decision": "approved", "reason": "Mine"})
        assert own.status_code == 403
        approved = await checker.post(url, json={"decision": "approved", "reason": "Checked"})
        assert approved.status_code == 200, approved.text
        assert approved.json()["yours"] is False

        screen = (await checker.get("/staff/withdrawal-limits")).json()
        assert screen["change"] is None and screen["can_change"] is True
        [upcoming] = [u for u in screen["upcoming"] if u["effective_from"] == day.isoformat()]
        assert upcoming["review_above_minor"] == 5_000_000
        again = await checker.post(url, json={"decision": "approved", "reason": "Twice"})
        assert again.status_code == 404
