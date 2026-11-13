"""Every member of staff can turn staff IDs into names, and nothing else about them."""

from collections.abc import AsyncIterator

import pytest

from app.infrastructure.database import Database
from app.shared.push_ids import push_id
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


async def test_any_role_reads_names_by_id_and_only_names(api: Api) -> None:
    grace = await api.staff("compliance", "Grace Auma")
    daniel = await api.staff("finance", "Daniel Kato")
    await api.staff("support", "Ruth Namata")
    unknown = push_id()
    response = await api.http.get(
        "/staff/people", params=[("ids", grace.id), ("ids", daniel.id), ("ids", unknown)]
    )
    assert response.status_code == 200, response.text
    people = response.json()["people"]
    assert {person["id"]: person["name"] for person in people} == {
        grace.id: "Grace Auma",
        daniel.id: "Daniel Kato",
    }
    assert all(set(person) == {"id", "name"} for person in people)


async def test_with_no_ids_it_lists_the_active_staff(api: Api) -> None:
    grace = await api.staff("admin", "Grace Auma")
    reader = await api.staff("support", "Ruth Namata")
    response = await api.http.get("/staff/people")
    assert response.status_code == 200, response.text
    names = {person["id"]: person["name"] for person in response.json()["people"]}
    assert names[grace.id] == "Grace Auma" and names[reader.id] == "Ruth Namata"


async def test_a_client_or_nobody_is_refused(api: Api) -> None:
    await api.client()
    assert (await api.http.get("/staff/people")).status_code in (401, 403)


async def test_too_many_or_malformed_ids_are_refused(api: Api) -> None:
    await api.staff("support")
    too_many = {"ids": [push_id() for _ in range(101)]}
    assert (await api.http.get("/staff/people", params=too_many)).status_code == 422
    assert (await api.http.get("/staff/people", params={"ids": "not an id"})).status_code == 422
