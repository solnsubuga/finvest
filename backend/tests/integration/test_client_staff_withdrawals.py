"""An early withdrawal two members of staff start for a client who asked for it in help chat."""

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from app.modules.console import actions
from app.modules.investments import service as investments
from app.modules.support import conversations
from app.shared.actors import SYSTEM, Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.investments import PRESENT, approved_destination, at, pay, request, today
from tests.support.kyc import verify
from tests.support.ledger import key

pytestmark = pytest.mark.integration

LATER = at(today() + timedelta(days=2))
DECIDERS = {"client_withdrawals.start"}


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database, early_withdrawal_open=True) as api:
        yield api


async def asked_in_chat(app_database: Database, client_id: str, clock: Clock = LATER) -> str:
    client = Actor("client", client_id)
    async with app_database.actor_transaction(client) as session:
        message = await conversations.send_as_client(
            session, client, "Please withdraw my bill, I need the money", key(), clock.now()
        )
    return message.id


async def started(
    api: Api, app_database: Database, worker_database: Database
) -> investments.Investment:
    """A running investment of a client with a sign-in account, as staff act only for one."""
    client = await api.client()
    await verify(app_database, client, PRESENT)
    investment = await request(app_database, client, PRESENT)
    await pay(worker_database, investment, investment.amount, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        return await investments.start_investment(
            session, investment.id, confirmed_on=today(), clock=PRESENT
        )


async def setting_up(
    api: Api, app_database: Database, worker_database: Database
) -> tuple[investments.Investment, str, str]:
    investment = await started(api, app_database, worker_database)
    destination_id = await approved_destination(
        app_database, Actor("client", investment.client_id), PRESENT
    )
    message_id = await asked_in_chat(app_database, investment.client_id)
    return investment, destination_id, message_id


async def propose(
    app_database: Database, staff: Actor, investment: investments.Investment, **details: str
) -> actions.ClientAction:
    async with app_database.actor_transaction(staff) as session:
        return await actions.propose(
            session,
            staff,
            investment.client_id,
            "withdrawal",
            "Client asked in help chat",
            LATER,
            details,
        )


async def decide(
    app_database: Database, staff: Actor, action_id: str, decision: str = "approved"
) -> actions.ClientAction:
    async with app_database.actor_transaction(staff) as session:
        return await actions.decide(
            session,
            staff,
            action_id,
            decision,  # type: ignore[arg-type]
            "Read the client's message",
            LATER,
            permissions=DECIDERS,
        )


async def test_two_staff_start_the_withdrawal_the_client_asked_for(
    api: Api, app_database: Database, worker_database: Database, migrator_database: Database
) -> None:
    investment, destination_id, message_id = await setting_up(api, app_database, worker_database)
    maker, checker = await api.staff("operations"), await api.staff("admin")
    action = await propose(
        app_database,
        maker,
        investment,
        investment_id=investment.id,
        destination_id=destination_id,
        message_id=message_id,
    )
    with pytest.raises(ApplicationError) as refused:
        await decide(app_database, maker, action.id)
    assert refused.value.code == "forbidden"

    decided = await decide(app_database, checker, action.id)
    assert decided.status == "approved"
    async with worker_database.actor_transaction(SYSTEM) as session:
        after = await investments.get_investment(session, investment.id)
    assert after.withdrawal_requested_at is not None
    async with migrator_database.transaction() as session:
        jobs = await session.scalar(
            text("SELECT count(*) FROM jobs WHERE idempotency_key = :id"), {"id": investment.id}
        )
        sent = (
            await session.scalars(
                text(
                    "SELECT channel FROM notification_messages WHERE idempotency_key LIKE :key "
                    "ORDER BY channel"
                ),
                {"key": f"staff-withdrawal:{action.id}:%"},
            )
        ).all()
        actor = await session.scalar(
            text(
                "SELECT actor_kind FROM audit_events WHERE resource_id = :id "
                "AND action = 'investments.withdrawal_requested'"
            ),
            {"id": investment.id},
        )
    assert jobs == 1
    assert "sms" in sent
    assert actor == "staff"


async def test_a_message_backs_one_withdrawal_and_must_be_the_clients_own(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    investment, destination_id, message_id = await setting_up(api, app_database, worker_database)
    other = await started(api, app_database, worker_database)
    someone_elses = await asked_in_chat(app_database, other.client_id)
    maker = await api.staff("operations")
    details = {"investment_id": investment.id, "destination_id": destination_id}
    with pytest.raises(ApplicationError) as refused:
        await propose(app_database, maker, investment, **details, message_id=someone_elses)
    assert refused.value.fields[0].code == "not_found"
    with pytest.raises(ApplicationError):
        await propose(app_database, maker, investment, **details)
    with pytest.raises(ApplicationError) as refused:
        await propose(
            app_database,
            maker,
            investment,
            investment_id=other.id,
            destination_id=destination_id,
            message_id=message_id,
        )
    assert refused.value.fields[0].code == "not_found"
    action = await propose(app_database, maker, investment, **details, message_id=message_id)
    await decide(app_database, maker, action.id, "withdrawn")
    again = await propose(app_database, maker, investment, **details, message_id=message_id)
    assert again.status == "proposed"


async def test_an_old_message_is_not_a_request(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    investment = await started(api, app_database, worker_database)
    destination_id = await approved_destination(
        app_database, Actor("client", investment.client_id), PRESENT
    )
    old = await asked_in_chat(app_database, investment.client_id, at(today() - timedelta(days=8)))
    maker = await api.staff("operations")
    with pytest.raises(ApplicationError) as refused:
        await propose(
            app_database,
            maker,
            investment,
            investment_id=investment.id,
            destination_id=destination_id,
            message_id=old,
        )
    assert refused.value.fields[0].code == "too_old"


async def test_support_cannot_start_a_withdrawal_and_it_is_off_while_withdrawals_are(
    test_database: TestDatabase, app_database: Database
) -> None:
    async with running_api(test_database, app_database) as closed:
        client = await closed.client()
        await closed.staff("admin")
        response = await closed.http.post(
            f"/staff/clients/{client.id}/actions",
            headers={"Idempotency-Key": uuid.uuid4().hex},
            json={
                "kind": "withdrawal",
                "reason": "Asked in chat",
                "withdrawal": {
                    "investment_id": "-Nabc0000000000000Z1",
                    "destination_id": "-Nabc0000000000000Z2",
                    "message_id": "-Nabc0000000000000Z3",
                },
            },
        )
        assert response.status_code == 409
        await closed.staff("support")
        response = await closed.http.post(
            f"/staff/clients/{client.id}/actions",
            headers={"Idempotency-Key": uuid.uuid4().hex},
            json={"kind": "withdrawal", "reason": "Asked in chat"},
        )
        assert response.status_code == 403


async def test_staff_never_ask_to_withdraw_without_an_approval(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    investment, destination_id, _ = await setting_up(api, app_database, worker_database)
    staff = await api.staff("admin")
    async with app_database.actor_transaction(staff) as session:
        changed = await session.execute(
            text(
                "UPDATE investments SET withdrawal_requested_at = clock_timestamp(), "
                "withdrawal_destination_id = :destination WHERE id = :id"
            ),
            {"id": investment.id, "destination": destination_id},
        )
    # The row policy hides the investment from a staff update with no approval.
    assert changed.rowcount == 0  # type: ignore[attr-defined]


async def test_staff_see_what_a_withdrawal_can_name(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    investment, destination_id, message_id = await setting_up(api, app_database, worker_database)
    api.clock.current = LATER.now()
    await api.staff("operations")
    options = await api.http.get(f"/staff/clients/{investment.client_id}/withdrawal-options")
    assert options.status_code == 200, options.text
    body = options.json()
    assert [item["id"] for item in body["investments"]] == [investment.id]
    assert body["investments"][0]["value_minor"] >= investment.amount.minor
    assert [item["id"] for item in body["accounts"]] == [destination_id]
    assert [item["id"] for item in body["requests"]] == [message_id]
    assert body["open"] is True
    await api.staff("support")
    forbidden = await api.http.get(f"/staff/clients/{investment.client_id}/withdrawal-options")
    assert forbidden.status_code == 403
