from collections.abc import AsyncIterator
from typing import Annotated

import httpx
import pytest
from fastapi import FastAPI
from pydantic import Field

from app.api.app import create_app
from app.infrastructure.database import Database
from app.infrastructure.settings import Settings
from app.shared.contracts import Contract
from app.shared.errors import ApplicationError, ErrorEnvelope
from app.shared.push_ids import validate_push_id

SUBMITTED = "0772-secret-value"


class StubDatabase:
    def __init__(self) -> None:
        self.is_ready = True

    async def ready(self) -> bool:
        return self.is_ready

    async def close(self) -> None: ...


class Order(Contract):
    amount: Annotated[int, Field(gt=0)]
    phone: Annotated[str, Field(pattern=r"^\d+$")]


def build(*, api_docs: bool = False) -> tuple[FastAPI, StubDatabase]:
    settings = Settings(
        database_url="postgresql://finvest_app:unused@localhost/finvest",
        api_docs=api_docs,
    )
    database = StubDatabase()
    app = create_app(settings, database)  # type: ignore[arg-type]

    @app.post("/orders")
    async def order(body: Order) -> Order:
        return body

    @app.get("/refused")
    async def refused() -> None:
        raise ApplicationError("rate_limited", retry_after_seconds=30)

    @app.get("/broken")
    async def broken() -> None:
        raise RuntimeError(SUBMITTED)

    return app, database


@pytest.fixture
async def api() -> AsyncIterator[tuple[httpx.AsyncClient, StubDatabase]]:
    app, database = build()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        yield client, database


def envelope(response: httpx.Response) -> ErrorEnvelope:
    parsed = ErrorEnvelope.model_validate(response.json())
    assert parsed.request_id == response.headers["X-Request-ID"]
    return parsed


async def test_liveness(api: tuple[httpx.AsyncClient, StubDatabase]) -> None:
    client, _ = api
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_follows_the_database(api: tuple[httpx.AsyncClient, StubDatabase]) -> None:
    client, database = api
    assert (await client.get("/health/ready")).status_code == 200
    database.is_ready = False
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert envelope(response).error.code == "unavailable"


async def test_each_response_has_its_own_request_id(
    api: tuple[httpx.AsyncClient, StubDatabase],
) -> None:
    client, _ = api
    first = await client.get("/health/live", headers={"X-Request-ID": "chosen-by-caller"})
    second = await client.get("/health/live")
    assert validate_push_id(first.headers["X-Request-ID"])
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


async def test_validation_errors_name_the_field_but_not_the_value(
    api: tuple[httpx.AsyncClient, StubDatabase],
) -> None:
    client, _ = api
    response = await client.post("/orders", json={"amount": 100, "phone": SUBMITTED})
    assert response.status_code == 422
    error = envelope(response).error
    assert error.code == "validation_failed"
    assert [problem.field_path for problem in error.fields] == [["body", "phone"]]
    assert SUBMITTED not in response.text


@pytest.mark.parametrize("amount", ["1000", 1000.0, 1000.5, True, 0, -5])
async def test_amounts_are_strict_positive_integers(
    api: tuple[httpx.AsyncClient, StubDatabase], amount: object
) -> None:
    client, _ = api
    response = await client.post("/orders", json={"amount": amount, "phone": "0772"})
    assert response.status_code == 422
    assert envelope(response).error.fields[0].field_path == ["body", "amount"]


async def test_unknown_fields_are_refused(api: tuple[httpx.AsyncClient, StubDatabase]) -> None:
    client, _ = api
    response = await client.post("/orders", json={"amount": 1, "phone": "1", "extra": 1})
    assert response.status_code == 422


async def test_unknown_routes_and_methods_use_the_envelope(
    api: tuple[httpx.AsyncClient, StubDatabase],
) -> None:
    client, _ = api
    missing = await client.get("/nowhere")
    assert missing.status_code == 404
    assert envelope(missing).error.code == "not_found"
    wrong = await client.delete("/health/live")
    assert wrong.status_code == 405
    assert envelope(wrong).error.code == "method_not_allowed"


async def test_application_errors_carry_their_code_and_retry_hint(
    api: tuple[httpx.AsyncClient, StubDatabase],
) -> None:
    client, _ = api
    response = await client.get("/refused")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"
    assert envelope(response).error.code == "rate_limited"


async def test_unexpected_errors_reveal_nothing(
    api: tuple[httpx.AsyncClient, StubDatabase], capsys: pytest.CaptureFixture[str]
) -> None:
    client, _ = api
    response = await client.get("/broken")
    assert response.status_code == 500
    assert envelope(response).error.code == "internal_error"
    assert SUBMITTED not in response.text
    assert SUBMITTED not in capsys.readouterr().out


async def test_api_documentation_is_off_unless_enabled() -> None:
    for enabled, status in ((False, 404), (True, 200)):
        app, _ = build(api_docs=enabled)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            assert (await client.get("/openapi.json")).status_code == status


def test_the_stub_matches_the_database_interface() -> None:
    assert {"ready", "close"} <= set(dir(Database))
