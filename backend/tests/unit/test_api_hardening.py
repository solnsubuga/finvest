from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel

from app.api import hardening
from app.api.app import create_app
from app.infrastructure.settings import Settings
from app.shared.errors import ErrorEnvelope


class StubDatabase:
    async def ready(self) -> bool:
        return True

    async def close(self) -> None: ...


class Note(BaseModel):
    text: str


def build() -> FastAPI:
    settings = Settings(database_url="postgresql://finvest_app:unused@localhost/finvest")
    app = create_app(settings, StubDatabase())  # type: ignore[arg-type]

    @app.post("/notes")
    async def note(body: Note) -> Note:
        return body

    @app.post("/raw")
    async def raw(request: Request) -> Response:
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
        return Response(str(size))

    @app.get("/cached")
    async def cached() -> Response:
        return Response("x", headers={"Cache-Control": "private, max-age=60"})

    @app.get("/broken")
    async def broken() -> None:
        raise RuntimeError("boom")

    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        yield client


def code(response: httpx.Response) -> str:
    return ErrorEnvelope.model_validate(response.json()).error.code


@pytest.mark.parametrize("path", ["/health/live", "/nowhere", "/broken"])
async def test_every_response_carries_the_headers(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    for name, value in hardening.HEADERS.items():
        assert response.headers[name] == value
    assert "X-Request-ID" in response.headers


async def test_a_route_may_set_its_own_cache_header(client: httpx.AsyncClient) -> None:
    response = await client.get("/cached")
    assert response.headers["Cache-Control"] == "private, max-age=60"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


async def test_a_body_within_the_limit_is_served(client: httpx.AsyncClient) -> None:
    text = "a" * (hardening.DEFAULT_BODY_LIMIT - 100)
    response = await client.post("/notes", json={"text": text})
    assert response.status_code == 200


async def test_a_declared_length_over_the_limit_is_refused_unread(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/notes", content=b"{" * (hardening.DEFAULT_BODY_LIMIT + 1))
    assert response.status_code == 413
    assert code(response) == "payload_too_large"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", ["/notes", "/raw"])
async def test_a_streamed_body_is_cut_off_at_the_limit(
    client: httpx.AsyncClient, path: str
) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(hardening.DEFAULT_BODY_LIMIT // 1024 + 2):
            yield b"a" * 1024

    response = await client.post(path, content=chunks())
    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert code(response) == "payload_too_large"


async def test_an_unreadable_length_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.post("/notes", content=b"{}", headers={"Content-Length": "-1"})
    assert response.status_code == 413


def test_a_listed_prefix_has_its_own_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardening, "BODY_LIMITS", {"/staff/": 10, "/staff/files/": 20})
    assert hardening.body_limit("/staff/files/x") == 20
    assert hardening.body_limit("/staff/other") == 10
    assert hardening.body_limit("/client/x") == hardening.DEFAULT_BODY_LIMIT
