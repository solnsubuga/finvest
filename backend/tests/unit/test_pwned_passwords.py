"""The Pwned Passwords range lookup, against a fake service."""

import hashlib

import httpx
import pytest

from app.infrastructure.pwned_passwords import PwnedPasswords
from app.infrastructure.settings import Settings

PASSWORD = "marble-copper-giraffe-41"
DIGEST = hashlib.sha1(PASSWORD.encode(), usedforsecurity=False).hexdigest().upper()
PREFIX, SUFFIX = DIGEST[:5], DIGEST[5:]
OTHER = "0" * 35


def service(handler: httpx.MockTransport) -> PwnedPasswords:
    return PwnedPasswords(client=httpx.AsyncClient(transport=handler))


def answering(
    body: str, status: int = 200, seen: list[httpx.Request] | None = None
) -> PwnedPasswords:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, text=body)

    return service(httpx.MockTransport(handle))


async def test_a_listed_password_is_seen() -> None:
    assert await answering(f"{OTHER}:3\r\n{SUFFIX}:52\r\n").seen(PASSWORD) is True


async def test_an_unlisted_password_is_not_seen() -> None:
    assert await answering(f"{OTHER}:3\r\n").seen(PASSWORD) is False


async def test_a_padding_line_is_never_a_match() -> None:
    assert await answering(f"{SUFFIX}:0\r\n").seen(PASSWORD) is False


async def test_only_five_characters_of_the_digest_are_sent() -> None:
    requests: list[httpx.Request] = []
    await answering("", seen=requests).seen(PASSWORD)
    [request] = requests
    assert str(request.url) == f"https://api.pwnedpasswords.com/range/{PREFIX}"
    assert request.headers["Add-Padding"] == "true"
    assert request.headers["User-Agent"] == "finvest-backend"
    sent = str(request.url).encode() + b"".join(
        f"{k}{v}".encode() for k, v in request.headers.items()
    )
    assert PASSWORD.encode() not in sent
    assert SUFFIX.encode() not in sent


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_refusal_from_the_service_means_we_cannot_tell(status: int) -> None:
    assert await answering("", status=status).seen(PASSWORD) is None


@pytest.mark.parametrize(
    "error", [httpx.ConnectError("down"), httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow")]
)
async def test_an_unreachable_service_means_we_cannot_tell(error: httpx.HTTPError) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise error

    assert await service(httpx.MockTransport(fail)).seen(PASSWORD) is None


@pytest.mark.parametrize(
    ("environment", "setting", "on"),
    [
        (None, None, False),
        ("local", None, False),
        ("staging", None, True),
        ("production", None, True),
        ("production", False, False),
        (None, True, True),
    ],
)
def test_the_breach_list_is_on_where_deployed(
    environment: str | None, setting: bool | None, on: bool
) -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://u@localhost/db",
        environment=environment,
        breach_list_check=setting,
    )
    assert settings.breach_list_on() is on
