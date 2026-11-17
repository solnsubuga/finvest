"""Product types over HTTP, against real PostgreSQL as finvest_app: the console's JSON is
taken as it is sent, with the return model as its value."""

import uuid
from collections.abc import AsyncIterator

import pytest

from app.infrastructure.database import Database
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as api:
        yield api


def new_type(code: str, model: str) -> dict[str, object]:
    return {
        "code": code,
        "model": model,
        "name": f"Savings note {code}",
        "description": "A term and a rate before tax, paid at maturity.",
        "default_tax_bps": 1500,
        "reason": "A new note the desk can offer",
    }


@pytest.mark.parametrize("model", ["fixed_income", "treasury_bill", "treasury_bond"])
async def test_a_new_type_is_proposed_with_its_model_sent_as_text(api: Api, model: str) -> None:
    await api.staff("admin")
    code = f"NOTE-{uuid.uuid4().hex[:8].upper()}"
    response = await api.http.post(
        "/staff/product-type-changes/new-type", json=new_type(code, model)
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["code"], body["model"], body["status"]) == (code, model, "proposed")


async def test_a_model_the_system_cannot_pay_is_refused(api: Api) -> None:
    await api.staff("admin")
    code = f"NOTE-{uuid.uuid4().hex[:8].upper()}"
    response = await api.http.post(
        "/staff/product-type-changes/new-type",
        json=new_type(code, "unit_price"),
    )
    assert response.status_code == 422
    fields = response.json()["error"]["fields"]
    assert [(f["field_path"], f["code"]) for f in fields] == [(["body", "model"], "enum")]
