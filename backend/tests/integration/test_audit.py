"""The audit trail is append-only and cannot be written in someone else's name."""

from contextlib import AbstractAsyncContextManager

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.audit import service as audit
from app.modules.audit.domain.events import REDACTED, AuditActor, AuditEvent
from app.shared.actors import SYSTEM, Actor
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock

pytestmark = pytest.mark.integration


def staff() -> Actor:
    return Actor("staff", push_id())


def client() -> Actor:
    return Actor("client", push_id())


def as_audit(actor: Actor) -> AuditActor:
    return AuditActor(actor.kind, actor.id)


def rate_change(actor: AuditActor, resource_id: str, **changes: object) -> AuditEvent:
    values: dict[str, object] = {
        "category": "change",
        "action": "rates.rate_changed",
        "resource_type": "product",
        "resource_id": resource_id,
        "actor": actor,
        "reason": "Board memo 12",
        "correlation_id": "req-1",
        "details": {"old_rate_bps": 1250, "new_rate_bps": 1300},
    }
    values.update(changes)
    return AuditEvent(**values)  # type: ignore[arg-type]


async def test_staff_record_and_read_what_they_did(
    app_database: Database, fake_clock: FakeClock
) -> None:
    maker, product = staff(), push_id()
    async with app_database.actor_transaction(maker) as session:
        saved = await audit.record(session, rate_change(as_audit(maker), product), fake_clock)
    fake_clock.advance(minutes=1)
    checker = staff()
    async with app_database.actor_transaction(checker) as session:
        await audit.record(
            session,
            rate_change(
                as_audit(checker),
                product,
                category="approval",
                action="rates.change_approved",
                reason="Checked against the memo",
            ),
            fake_clock,
        )
        events = await audit.events_for_resource(session, "product", product)
        recent = await audit.recent_events(session, limit=2)
    assert [event.action for event in events] == ["rates.change_approved", "rates.rate_changed"]
    assert [event.id for event in recent] == [event.id for event in events]
    first = events[1]
    assert first.id == saved.id
    assert (first.actor_kind, first.actor_id) == ("staff", maker.id)
    assert first.reason == "Board memo 12"
    assert first.correlation_id == "req-1"
    assert first.outcome == "succeeded"
    assert first.details == {"old_rate_bps": 1250, "new_rate_bps": 1300}
    assert first.occurred_at < events[0].occurred_at == fake_clock.now()


async def test_an_event_rolls_back_with_the_change_it_describes(
    app_database: Database, fake_clock: FakeClock
) -> None:
    actor, product = staff(), push_id()
    with pytest.raises(RuntimeError, match="change failed"):
        async with app_database.actor_transaction(actor) as session:
            await audit.record(session, rate_change(as_audit(actor), product), fake_clock)
            raise RuntimeError("change failed")
    async with app_database.actor_transaction(actor) as session:
        assert await audit.events_for_resource(session, "product", product) == []


async def test_personal_data_never_reaches_the_table(
    app_database: Database, migrator_database: Database, fake_clock: FakeClock
) -> None:
    actor, resource = staff(), push_id()
    event = rate_change(
        as_audit(actor),
        resource,
        action="clients.profile_updated",
        resource_type="client",
        reason="Client phoned from 0700 123 456",
        details={
            "phone": "+256700123456",
            "note": "wrote to jane@example.com about 1001004827",
            "changed_fields": ["phone"],
        },
    )
    async with app_database.actor_transaction(actor) as session:
        await audit.record(session, event, fake_clock)
    async with migrator_database.transaction() as session:
        stored = (
            await session.execute(
                text(
                    "SELECT reason || ' ' || details::text FROM audit_events "
                    "WHERE resource_id = :id"
                ),
                {"id": resource},
            )
        ).scalar_one()
    for secret in ("0700", "256700123456", "jane@example.com", "1001004827"):
        assert secret not in stored
    assert REDACTED in stored and "changed_fields" in stored


@pytest.mark.parametrize("actor", ["client", "staff", "system", None])
async def test_the_api_records_only_its_own_actor(
    app_database: Database, fake_clock: FakeClock, actor: str | None
) -> None:
    acting = {"client": client(), "staff": staff(), "system": SYSTEM, None: None}[actor]
    # A signed-in person is recorded as themselves and a visitor as anonymous. The API is never
    # trusted to speak as the system, so claiming to be it records nothing at all.
    allowed = {
        "client": as_audit(acting) if acting else None,
        "staff": as_audit(acting) if acting else None,
        "system": None,
        None: AuditActor.anonymous(),
    }[actor]
    resource = push_id()
    candidates = [
        AuditActor.client(push_id()),
        AuditActor.staff(push_id()),
        AuditActor.system(),
        AuditActor.provider(),
        AuditActor.anonymous(),
    ]
    if allowed is not None and allowed not in candidates:
        candidates.append(allowed)
    for candidate in candidates:
        event = rate_change(candidate, resource, category="security", action="auth.sign_in")
        if candidate == allowed:
            async with _transaction(app_database, acting) as session:
                await audit.record(session, event, fake_clock)
        else:
            with pytest.raises(DBAPIError, match="row-level security"):
                async with _transaction(app_database, acting) as session:
                    await audit.record(session, event, fake_clock)
    async with app_database.actor_transaction(staff()) as session:
        events = await audit.events_for_resource(session, "product", resource)
    expected = [(allowed.kind, allowed.id)] if allowed else []
    assert [(event.actor_kind, event.actor_id) for event in events] == expected


def _transaction(
    database: Database, actor: Actor | None
) -> AbstractAsyncContextManager[AsyncSession]:
    return database.actor_transaction(actor) if actor else database.transaction()


async def test_a_worker_records_the_system_or_a_provider_never_a_person(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    resource = push_id()
    for actor in (AuditActor.system(), AuditActor.provider()):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await audit.record(
                session,
                rate_change(actor, resource, action="payments.status_confirmed"),
                fake_clock,
            )
    for actor in (
        AuditActor.staff(push_id()),
        AuditActor.client(push_id()),
        AuditActor.anonymous(),
    ):
        with pytest.raises(DBAPIError, match="row-level security"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await audit.record(session, rate_change(actor, resource), fake_clock)
    async with app_database.actor_transaction(staff()) as session:
        events = await audit.events_for_resource(session, "product", resource)
    assert sorted(event.actor_kind for event in events) == ["provider", "system"]


async def test_only_staff_read_the_trail(
    app_database: Database, worker_database: Database, fake_clock: FakeClock
) -> None:
    actor, resource = client(), push_id()
    async with app_database.actor_transaction(actor) as session:
        await audit.record(
            session,
            rate_change(as_audit(actor), resource, category="security", action="auth.sign_in"),
            fake_clock,
        )
        # Not even their own events: the trail is a staff record.
        assert await audit.events_for_resource(session, "product", resource) == []
    async with app_database.actor_transaction(SYSTEM) as session:
        assert await audit.events_for_resource(session, "product", resource) == []
    async with app_database.transaction() as session:
        assert await audit.recent_events(session) == []
    with pytest.raises(DBAPIError, match="permission denied"):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await audit.recent_events(session)
    async with app_database.actor_transaction(staff()) as session:
        assert len(await audit.events_for_resource(session, "product", resource)) == 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET reason = 'rewritten'",
        "UPDATE audit_events SET actor_id = NULL, actor_kind = 'system'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ],
)
async def test_nobody_can_edit_or_erase_the_trail(
    app_database: Database,
    worker_database: Database,
    migrator_database: Database,
    fake_clock: FakeClock,
    statement: str,
) -> None:
    actor, resource = staff(), push_id()
    async with app_database.actor_transaction(actor) as session:
        await audit.record(session, rate_change(as_audit(actor), resource), fake_clock)
    for database, acting in ((app_database, actor), (worker_database, SYSTEM)):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with database.actor_transaction(acting) as session:
                await session.execute(text(statement))
    with pytest.raises(DBAPIError, match="audit events are append-only"):
        async with migrator_database.transaction() as session:
            await session.execute(text(statement))
    async with app_database.actor_transaction(actor) as session:
        (event,) = await audit.events_for_resource(session, "product", resource)
    assert event.reason == "Board memo 12"
    assert (event.actor_kind, event.actor_id) == ("staff", actor.id)


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("category", "gossip", "ck_audit_events_category"),
        ("action", "nodots", "ck_audit_events_action"),
        ("outcome", "maybe", "ck_audit_events_outcome"),
        # An unknown kind of actor is stopped by the row policy before the check is reached.
        ("actor_kind", "robot", "row-level security"),
        ("actor_kind", "staff", "row-level security"),
        ("resource_type", "Has Space", "ck_audit_events_resource_type"),
        ("resource_id", "has space", "ck_audit_events_resource_id"),
        ("reason", "   ", "ck_audit_events_reason"),
        ("correlation_id", "has space", "ck_audit_events_correlation_id"),
        ("details", "[]", "ck_audit_events_details"),
    ],
)
async def test_the_database_checks_the_shape_of_an_event_itself(
    worker_database: Database, column: str, value: str, constraint: str
) -> None:
    row = {
        "id": push_id(),
        "category": "change",
        "action": "rates.rate_changed",
        "outcome": "succeeded",
        "actor_kind": "system",
        "resource_type": "product",
        "resource_id": push_id(),
        "reason": "Scheduled",
        "correlation_id": "job-1",
        "details": "{}",
    }
    row[column] = value
    with pytest.raises(DBAPIError, match=constraint):
        async with worker_database.actor_transaction(SYSTEM) as session:
            await session.execute(
                text(
                    "INSERT INTO audit_events (id, occurred_at, category, action, outcome, "
                    "actor_kind, resource_type, resource_id, reason, correlation_id, details) "
                    "VALUES (:id, now(), :category, :action, :outcome, :actor_kind, "
                    ":resource_type, :resource_id, :reason, :correlation_id, "
                    "CAST(:details AS jsonb))"
                ),
                row,
            )


async def test_the_database_requires_a_reason_for_a_decision(worker_database: Database) -> None:
    for category in ("approval", "operator_control"):
        with pytest.raises(DBAPIError, match="ck_audit_events_reason_required"):
            async with worker_database.actor_transaction(SYSTEM) as session:
                await session.execute(
                    text(
                        "INSERT INTO audit_events (id, occurred_at, category, action, outcome, "
                        "actor_kind, resource_type) VALUES (:id, now(), :category, "
                        "'jobs.job_requeued', 'succeeded', 'system', 'job')"
                    ),
                    {"id": push_id(), "category": category},
                )
