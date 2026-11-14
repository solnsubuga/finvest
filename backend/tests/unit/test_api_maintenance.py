"""The client maintenance switch: client and public routes answer `maintenance`, the rest work."""

import httpx
from fastapi import FastAPI

from app.api.app import create_app
from app.infrastructure.settings import Settings
from app.shared.errors import ErrorEnvelope


class StubDatabase:
    async def ready(self) -> bool:
        return True

    async def close(self) -> None: ...


def build(*, on: bool) -> FastAPI:
    settings = Settings(
        database_url="postgresql://finvest_app:unused@localhost/finvest",
        client_maintenance=on,
        client_allowed_origins=["https://app.finvestug.com"],
    )
    app = create_app(settings, StubDatabase())  # type: ignore[arg-type]

    @app.get("/client/ping")
    async def client_ping() -> dict[str, str]:
        return {"ok": "client"}

    @app.get("/public/ping")
    async def public_ping() -> dict[str, str]:
        return {"ok": "public"}

    @app.get("/staff/ping")
    async def staff_ping() -> dict[str, str]:
        return {"ok": "staff"}

    @app.post("/webhooks/ping")
    async def webhook_ping() -> dict[str, str]:
        return {"ok": "webhook"}

    return app


async def call(app: FastAPI, method: str, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as http:
        return await http.request(method, path, headers={"Origin": "https://app.finvestug.com"})


async def test_client_and_public_routes_answer_maintenance_while_it_is_on() -> None:
    app = build(on=True)
    for path in ("/client/ping", "/public/ping"):
        response = await call(app, "GET", path)
        assert response.status_code == 503
        assert ErrorEnvelope.model_validate(response.json()).error.code == "maintenance"
        assert response.headers["Retry-After"] == "120"
        # The app's browser can read the answer, so it shows the maintenance screen.
        assert response.headers["access-control-allow-origin"] == "https://app.finvestug.com"
        assert response.headers["x-request-id"]


async def test_staff_webhooks_and_health_keep_working() -> None:
    app = build(on=True)
    assert (await call(app, "GET", "/staff/ping")).json() == {"ok": "staff"}
    assert (await call(app, "POST", "/webhooks/ping")).json() == {"ok": "webhook"}
    assert (await call(app, "GET", "/health/live")).status_code == 200


async def test_everything_works_while_it_is_off() -> None:
    app = build(on=False)
    assert (await call(app, "GET", "/client/ping")).json() == {"ok": "client"}
    assert (await call(app, "GET", "/public/ping")).json() == {"ok": "public"}
