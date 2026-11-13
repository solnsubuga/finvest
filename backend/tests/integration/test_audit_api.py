"""The audit trail on the console: filters, pages, names, and an export that is itself recorded."""

import csv
import io
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from app.infrastructure.database import Database
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.shared.actors import Actor
from app.shared.push_ids import push_id
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


async def write(api: Api, actor: Actor, resource_id: str, action: str, **extra: object) -> None:
    async with api.database.actor_transaction(actor) as session:
        await audit.record(
            session,
            AuditEvent(
                category=extra.pop("category", "change"),  # type: ignore[arg-type]
                action=action,
                resource_type="product",
                resource_id=resource_id,
                actor=AuditActor(actor.kind, actor.id),
                reason=extra.pop("reason", "Board memo"),  # type: ignore[arg-type]
                details=extra,
            ),
            api.clock,
        )
    api.clock.advance(seconds=1)


async def test_staff_with_audit_view_page_through_filtered_events(api: Api) -> None:
    maker = await api.staff("operations", "Maker Person")
    product = push_id()
    for index in range(5):
        await write(api, maker, product, "rates.rate_changed", step=index)
    await write(api, maker, product, "products.product_closed")
    viewer = await api.staff("compliance", "Clare Viewer")
    await api.as_staff(viewer)

    first = await api.http.get(
        "/staff/audit/events",
        params={"resource_id": product, "action": "rates.rate_changed", "limit": 3},
    )
    assert first.status_code == 200, first.text
    page = first.json()
    assert [event["details"]["step"] for event in page["events"]] == [4, 3, 2]
    assert page["events"][0]["actor_name"] == "Maker Person"
    second = await api.http.get(
        "/staff/audit/events",
        params={
            "resource_id": product,
            "action": "rates.rate_changed",
            "limit": 3,
            "cursor": page["next_cursor"],
        },
    )
    assert [event["details"]["step"] for event in second.json()["events"]] == [1, 0]
    assert second.json()["next_cursor"] is None

    prefixed = await api.http.get(
        "/staff/audit/events", params={"resource_id": product, "action": "products."}
    )
    assert [event["action"] for event in prefixed.json()["events"]] == ["products.product_closed"]


async def test_time_and_actor_filters(api: Api) -> None:
    one, two = await api.staff("operations"), await api.staff("operations")
    product = push_id()
    start = api.clock.now()
    await write(api, one, product, "rates.rate_changed")
    api.clock.advance(hours=1)
    middle = api.clock.now()
    await write(api, two, product, "rates.rate_changed")
    await api.staff("admin")

    mine = await api.http.get(
        "/staff/audit/events", params={"resource_id": product, "actor_id": one.id}
    )
    assert [event["actor_id"] for event in mine.json()["events"]] == [one.id]
    early = await api.http.get(
        "/staff/audit/events",
        params={
            "resource_id": product,
            "since": start.isoformat(),
            "until": middle.isoformat(),
        },
    )
    assert [event["actor_id"] for event in early.json()["events"]] == [one.id]
    backwards = await api.http.get(
        "/staff/audit/events",
        params={"since": middle.isoformat(), "until": (middle - timedelta(1)).isoformat()},
    )
    assert backwards.status_code == 422


async def test_bad_filters_and_cursors_are_refused(api: Api) -> None:
    await api.staff("admin")
    for params in (
        {"action": "Not An Action"},
        {"action": "rates.%"},
        {"actor_id": "short"},
        {"cursor": "bm90IGEgY3Vyc29y"},
        {"since": "2026-11-13T10:00:00"},
    ):
        response = await api.http.get("/staff/audit/events", params=params)
        assert response.status_code == 422, params


async def test_roles_without_audit_view_are_refused(api: Api) -> None:
    for role in ("operations", "finance", "support"):
        await api.staff(role)
        assert (await api.http.get("/staff/audit/events")).status_code == 403
        assert (await api.http.get("/staff/audit/events.csv")).status_code == 403


async def test_an_export_is_a_csv_that_never_runs_a_formula_and_is_recorded(api: Api) -> None:
    maker = await api.staff("operations", "Maker Person")
    product = push_id()
    await write(api, maker, product, "rates.rate_changed", reason="=HYPERLINK(1)")
    viewer = await api.staff("admin")
    response = await api.http.get("/staff/audit/events.csv", params={"resource_id": product})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][:3] == ["occurred_at", "category", "action"]
    assert len(rows) == 2
    assert rows[1][2] == "rates.rate_changed" and rows[1][6] == "Maker Person"
    assert rows[1][9] == "'=HYPERLINK(1)"

    exported = await api.http.get(
        "/staff/audit/events",
        params={"action": "audit.trail_exported", "actor_id": viewer.id},
    )
    events = exported.json()["events"]
    assert len(events) == 1
    assert events[0]["details"] == {"events": 1, "filters": {"resource_id": product}}


async def test_facets_list_what_is_on_record(api: Api) -> None:
    maker = await api.staff("operations")
    await write(api, maker, push_id(), "rates.rate_changed")
    await api.staff("admin")
    facets = (await api.http.get("/staff/audit/facets")).json()
    assert "rates.rate_changed" in facets["actions"]
    assert "product" in facets["resource_types"]
