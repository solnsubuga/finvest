import httpx
import pytest

from app.api.app import create_app
from app.infrastructure.settings import Settings
from app.shared.errors import ErrorEnvelope

URL = "postgresql://finvest_app:unused@localhost/finvest"


class UntouchedDatabase:
    """Fails the test if a closed sign-up reaches the database."""

    async def ready(self) -> bool:
        return True

    def transaction(self) -> None:
        raise AssertionError("a closed sign-up must do no work")

    def actor_transaction(self, *_: object) -> None:
        raise AssertionError("a closed sign-up must do no work")

    async def close(self) -> None: ...


@pytest.mark.parametrize(
    ("environment", "explicit", "expected"),
    [
        (None, None, True),
        ("local", None, True),
        ("production", None, False),
        ("staging", None, False),
        ("somewhere-new", None, False),
        ("production", True, True),
        (None, False, False),
    ],
)
def test_sign_up_is_closed_anywhere_deployed_unless_opened(
    environment: str | None, explicit: bool | None, expected: bool
) -> None:
    settings = Settings(database_url=URL, environment=environment, client_signup_open=explicit)
    assert settings.signup_open() is expected


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/client/signup/start", {"phone": "0772123456"}),
        ("/client/signup/confirm", {"challenge_id": "-Nabc0000000000000A1", "code": "123456"}),
        ("/client/signup/complete", {"ticket": "x" * 43, "password": "copper-harbour-violin"}),
    ],
)
async def test_a_closed_sign_up_is_refused_before_any_work(path: str, body: dict[str, str]) -> None:
    settings = Settings(database_url=URL, environment="production")
    app = create_app(settings, UntouchedDatabase())  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as client:
        response = await client.post(path, json=body)
    assert response.status_code == 503
    assert ErrorEnvelope.model_validate(response.json()).error.code == "unavailable"
