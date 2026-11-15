"""Staff find clients by name or email, and page through every client, newest first."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.infrastructure.database import Database
from app.modules.identity.service import create_unclaimed_client
from app.shared.actors import Actor
from app.shared.push_ids import push_id
from app.shared.references import short_reference
from tests.integration.test_documents import ask as ask_for_statement
from tests.support.database import TestDatabase
from tests.support.http import PASSWORD, Api, running_api
from tests.support.investments import PRESENT, request
from tests.support.kyc import verify
from tests.support.payments import start_deposit
from tests.support.phones import unique_phone

pytestmark = pytest.mark.integration

IMPORTER = Actor("staff", "-Nabc0000000000000S1")


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        yield running


async def listed(api: Api, **params: str) -> dict[str, object]:
    response = await api.http.get("/staff/clients", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def rows(body: dict[str, object]) -> list[dict[str, Any]]:
    found = body["clients"]
    assert isinstance(found, list)
    return found


def ids(body: dict[str, object]) -> list[str]:
    return [row["id"] for row in rows(body)]


async def test_staff_find_a_client_by_name_in_any_order_and_case(
    api: Api, app_database: Database
) -> None:
    client = await api.client()
    await verify(app_database, client, PRESENT)
    await api.staff("support")
    for query in ("nakato amara", "Amara  NAK", "akat"):
        found = await listed(api, q=query)
        assert client.id in ids(found), query
    row = next(r for r in rows(found) if r["id"] == client.id)
    assert row["verified_name"] == "Amara Nakato"
    assert row["account_number"] is not None
    assert client.id not in ids(await listed(api, q="amara okello"))


async def test_staff_find_a_client_by_part_of_their_email(api: Api) -> None:
    tag = push_id().lower().replace("-", "x").replace("_", "y")[-10:]
    client_id = await api.identity.create_client(
        phone=unique_phone(),
        email=f"sarah.{tag}@example.com",
        password=PASSWORD,
    )
    await api.staff("support")
    assert ids(await listed(api, q=tag.upper())) == [client_id]
    # LIKE's wildcards are plain characters, so they find nothing rather than everyone.
    assert ids(await listed(api, q="%")) == []
    assert ids(await listed(api, q="_")) == []


async def test_an_id_or_mobile_number_finds_one_client(api: Api) -> None:
    client = await api.client()
    await api.staff("support")
    assert ids(await listed(api, q=str(client.id))) == [client.id]


async def every_page(api: Api, **params: str) -> list[dict[str, Any]]:
    """Every row of the full list, page by page. Other tests' clients are in it too."""
    seen: list[dict[str, Any]] = []
    after: str | None = None
    for _ in range(500):
        page = await listed(api, **params, **({"after": after} if after else {}))
        assert len(rows(page)) <= 25
        seen += rows(page)
        if page["next_after"] is None:
            return seen
        after = str(page["next_after"])
    raise AssertionError("the list never ended")


async def test_every_client_is_listed_newest_first_a_page_at_a_time(api: Api) -> None:
    made: list[Actor] = [await api.client() for _ in range(3)]
    await api.staff("support")
    listed_ids = [row["id"] for row in await every_page(api)]
    assert len(listed_ids) == len(set(listed_ids)), "a client was on two pages"
    mine = [client_id for client_id in listed_ids if client_id in {a.id for a in made}]
    assert mine == [actor.id for actor in reversed(made)]
    closed = await listed(api, status="closed")
    assert all(row["status"] == "closed" for row in rows(closed))


async def test_a_client_carried_over_from_the_paper_register_is_listed(api: Api) -> None:
    # Not signed up yet: no password, and an email as it was written down.
    async with api.database.actor_transaction(IMPORTER) as session:
        client_id = await create_unclaimed_client(
            session, api.clock, IMPORTER, phone="+256709460001", email="Ada@Example.com"
        )
    await api.staff("support")
    row = next(row for row in await every_page(api) if row["id"] == client_id)
    assert (row["status"], row["verified_name"], row["account_number"]) == (
        "unclaimed",
        None,
        None,
    )
    unclaimed = await every_page(api, status="unclaimed")
    assert client_id in [row["id"] for row in unclaimed]
    assert all(row["status"] == "unclaimed" for row in unclaimed)
    page = await api.http.get(f"/staff/clients/{client_id}")
    assert page.status_code == 200, page.text
    assert page.json()["account"]["status"] == "unclaimed"


async def test_a_bad_page_place_is_refused(api: Api) -> None:
    await api.staff("support")
    response = await api.http.get("/staff/clients", params={"after": "yesterday"})
    assert response.status_code == 422
    assert response.json()["error"]["fields"][0]["code"] == "bad_after"


async def test_a_short_reference_read_out_finds_the_client_it_belongs_to(
    api: Api, app_database: Database
) -> None:
    client = await api.client()
    await verify(app_database, client, PRESENT)
    other = await api.client()
    investment = await request(app_database, client, PRESENT)
    deposit = await start_deposit(app_database, PRESENT, actor=client, purpose_id=investment.id)
    statement = await ask_for_statement(app_database, client)
    await api.staff("support")

    def found(body: dict[str, object]) -> list[tuple[str, str | None]]:
        return [(row["id"], row["found_by"]) for row in rows(body)]

    paid = short_reference(deposit.id)
    assert found(await listed(api, q=paid)) == [(client.id, f"Payment {paid}")]
    # As a client reads it out: any case, spaces, no dashes, O for 0.
    spoken = paid.lower().replace("-", " ").replace("0", "o")
    assert found(await listed(api, q=spoken)) == [(client.id, f"Payment {paid}")]
    invested = short_reference(investment.id)
    assert found(await listed(api, q=invested)) == [(client.id, f"Investment {invested}")]
    sent = short_reference(statement.id, "ST")
    assert found(await listed(api, q=sent)) == [(client.id, f"Statement {sent}")]
    # The prefix says which kind of record: a statement's code with FV- names no payment.
    assert ids(await listed(api, q=sent.replace("ST-", "FV-"))) == []
    assert ids(await listed(api, q=paid.replace("FV-", "RC-"))) == []
    assert other.id not in ids(await listed(api, q=paid))
    # A name search shows no reference.
    assert all(row["found_by"] is None for row in rows(await listed(api, q="nakato")))
