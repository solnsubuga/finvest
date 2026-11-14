"""Finance closes a month in order, once its days are closed, with a second person's approval,
and the books it signed are kept as they were."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.audit import service as audit
from app.modules.ledger import closing
from app.shared.clock import SystemClock
from app.shared.push_ids import push_id
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.ledger import staff

pytestmark = pytest.mark.integration

# Integration tests only close days in 2001.
MONTH_END = date(2001, 1, 31)


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        running.clock.current = datetime.now(UTC)
        yield running


async def propose(api: Api, month: str) -> tuple[int, dict[str, object]]:
    response = await api.http.post(
        "/staff/finance/month-closes",
        json={"month": month, "reason": "Every day closed and the books agree"},
        headers={"Idempotency-Key": push_id()},
    )
    return response.status_code, response.json()


async def decide(api: Api, close_id: object, action: str) -> tuple[int, dict[str, object]]:
    headers = {"Idempotency-Key": push_id()} if action == "approve" else {}
    response = await api.http.post(
        f"/staff/finance/month-closes/{close_id}/{action}",
        json={"reason": "Checked the books"},
        headers=headers,
    )
    return response.status_code, response.json()


def refusal(body: dict[str, object]) -> object:
    error = body["error"]
    assert isinstance(error, dict)
    return error["fields"][0]["code"]


async def test_a_month_is_closed_once_in_order_by_a_second_person(
    api: Api, app_database: Database
) -> None:
    closer = staff()
    async with app_database.actor_transaction(closer) as session:
        closed = await closing.closed_through(session)
        if closed is None or closed < MONTH_END:
            await closing.close_through(session, closer, MONTH_END, SystemClock())
    proposer = await api.staff("finance")
    listed = await api.http.get("/staff/finance/month-closes")
    assert listed.status_code == 200, listed.text
    if listed.json()["last_closed"] is not None:
        pytest.skip("a month is already closed in this database")

    status, body = await propose(api, "2001-02")
    assert (status, refusal(body)) == (409, "days_not_closed")
    status, first = await propose(api, "2001-01")
    assert status == 201, first
    assert first["status"] == "proposed"
    assert first["month"] == "2001-01"
    assert len(str(first["books_sha256"])) == 64
    status, body = await propose(api, "2001-01")
    assert (status, refusal(body)) == (409, "close_already_proposed")
    status, body = await decide(api, first["id"], "approve")
    assert (status, refusal(body)) == (403, "proposed_by_you")

    approver = await api.staff("finance", name="Joan Akello")
    inbox = await api.http.get("/staff/approvals")
    assert any(
        item["kind"] == "month_close" and item["id"] == first["id"] and item["can_decide"]
        for item in inbox.json()["items"]
    )
    status, body = await decide(api, first["id"], "approve")
    assert status == 200, body
    assert body["status"] == "approved"
    assert body["decided_by"] == approver.id
    assert body["books_sha256"] == first["books_sha256"]

    listed = await api.http.get("/staff/finance/month-closes")
    assert listed.json()["last_closed"] == "2001-01"
    assert listed.json()["next"] == {
        "month": "2001-02",
        "closed_through": listed.json()["next"]["closed_through"],
        "refusal": "days_not_closed",
    }
    status, body = await propose(api, "2001-01")
    assert (status, refusal(body)) == (409, "month_already_closed")
    status, body = await propose(api, "2001-03")
    assert (status, refusal(body)) == (409, "close_months_in_order")

    async with app_database.actor_transaction(approver) as session:
        events = await audit.events_for_resource(session, "finance_month_close", str(first["id"]))
    assert {e.action for e in events} >= {
        "finance.month_close_proposed",
        "finance.month_closed",
    }

    await api.as_staff(proposer)
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(proposer) as session:
            await session.execute(
                text("UPDATE finance_month_closes SET books = '[]'::jsonb WHERE id = :id"),
                {"id": first["id"]},
            )
    with pytest.raises(DBAPIError):
        async with app_database.actor_transaction(proposer) as session:
            await session.execute(
                text("DELETE FROM finance_month_closes WHERE id = :id"), {"id": first["id"]}
            )


async def test_a_month_that_has_not_ended_cannot_be_proposed(api: Api) -> None:
    await api.staff("finance")
    this_month = datetime.now(UTC).strftime("%Y-%m")
    status, body = await propose(api, this_month)
    assert status == 409, body
    assert refusal(body) in ("month_not_ended", "close_months_in_order")


async def test_a_role_without_finance_cannot_close_a_month(api: Api) -> None:
    await api.staff("support")
    status, _ = await propose(api, "2001-01")
    assert status == 403
