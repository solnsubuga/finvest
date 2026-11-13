from collections.abc import AsyncIterator, MutableMapping
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from structlog.testing import capture_logs

from app.api.app import create_app
from app.infrastructure.settings import Settings


class StubDatabase:
    async def ready(self) -> bool:
        return True

    async def close(self) -> None: ...


def build() -> FastAPI:
    settings = Settings(database_url="postgresql://finvest_app:unused@localhost/finvest")
    app = create_app(settings, StubDatabase())  # type: ignore[arg-type]

    @app.get("/things/{thing_id}")
    async def thing(thing_id: str) -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/broken")
    async def broken() -> None:
        raise RuntimeError("0772123456")

    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        yield client


def served(logs: list[MutableMapping[str, Any]]) -> list[MutableMapping[str, Any]]:
    return [line for line in logs if line["event"] == "request_served"]


async def test_a_request_logs_its_route_template_not_its_path(client: httpx.AsyncClient) -> None:
    with capture_logs() as logs:
        response = await client.get("/things/0772123456?phone=0772123456")
    assert response.status_code == 200
    [line] = served(logs)
    assert line["method"] == "GET"
    assert line["route"] == "/things/{thing_id}"
    assert line["status"] == 200
    assert isinstance(line["duration_ms"], float)
    assert "0772123456" not in repr(logs)


async def test_an_unknown_path_logs_as_unmatched(client: httpx.AsyncClient) -> None:
    with capture_logs() as logs:
        await client.get("/secret-0772123456")
    [line] = served(logs)
    assert line["route"] == "unmatched"
    assert line["status"] == 404
    assert "0772123456" not in repr(logs)


async def test_a_failure_logs_its_status(client: httpx.AsyncClient) -> None:
    with capture_logs() as logs:
        await client.get("/broken")
    [line] = served(logs)
    assert line["status"] == 500
    assert "0772123456" not in repr(logs)


async def test_health_probes_are_quiet(client: httpx.AsyncClient) -> None:
    with capture_logs() as logs:
        await client.get("/health/live")
        await client.get("/health/ready")
    assert served(logs) == []
