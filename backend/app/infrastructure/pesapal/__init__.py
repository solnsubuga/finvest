"""What every Pesapal adapter shares: its settings and the bearer token.

Pesapal API 3.0 signs a merchant in with `POST /api/Auth/RequestToken`, which exchanges the
consumer key and secret for a bearer token that lasts five minutes. One `PesapalTokens` per
process fetches it and reuses it until shortly before it runs out.

Nothing here moves money or knows about deposits. A failure to get a token raises
`PesapalUnavailable`, which carries no secret and no response body: without a token no request
reached Pesapal, so the caller can treat it as "nothing was sent".
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.shared.clock import Clock

# Pesapal's tokens last five minutes. One is used for four, so it never expires on the way.
TOKEN_LIFETIME = timedelta(minutes=4)
LIVE_URL = "https://pay.pesapal.com/v3"
# Pesapal's sandbox. It takes test cards and moves no money, so it is refused in production.
SANDBOX_HOSTS = frozenset({"cybqa.pesapal.com"})


class PesapalAddress(BaseSettings):
    """Only `FINVEST_PESAPAL_BASE_URL`, for start-up checks in a process that holds no Pesapal
    credentials, such as the scheduler."""

    model_config = SettingsConfigDict(env_prefix="FINVEST_PESAPAL_", extra="ignore")

    base_url: str = LIVE_URL

    @property
    def is_sandbox(self) -> bool:
        return httpx.URL(self.base_url).host in SANDBOX_HOSTS


class PesapalSettings(BaseSettings):
    """From `FINVEST_PESAPAL_*`. Read by the API and the worker when
    `FINVEST_CARDS_PROVIDER=pesapal`."""

    model_config = SettingsConfigDict(
        env_prefix="FINVEST_PESAPAL_", extra="ignore", hide_input_in_errors=True
    )

    consumer_key: SecretStr
    consumer_secret: SecretStr
    # `https://pay.pesapal.com/v3` live, `https://cybqa.pesapal.com/pesapalv3` for the sandbox.
    base_url: str = LIVE_URL
    timeout_seconds: float = Field(default=30, gt=0, le=60)
    # A long random value in the path of the address Pesapal calls. Pesapal does not sign its
    # notifications, so this keeps out anyone who does not know the address. Pesapal needs a
    # notification address on every order, so it is required.
    ipn_token: SecretStr = Field(min_length=32)
    # Where our API is reached from the internet, used to build the notification address that
    # is registered with Pesapal: `<public_api_url>/provider-callbacks/cards/pesapal/<token>`.
    public_api_url: str = "https://api.finvestug.com"
    # Where Pesapal sends the payer back to when they finish, a page of the client app.
    return_url: str = "https://app.finvestug.com/pay/card/return"

    @field_validator("base_url", "public_api_url", "return_url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        if not value.startswith("https://") or len(value) <= len("https://"):
            raise ValueError("Pesapal addresses must use https")
        return value.rstrip("/")

    @property
    def is_sandbox(self) -> bool:
        return httpx.URL(self.base_url).host in SANDBOX_HOSTS

    def notification_url(self) -> str:
        return (
            f"{self.public_api_url}/provider-callbacks/cards/pesapal/"
            f"{self.ipn_token.get_secret_value()}"
        )

    def http_client(self) -> httpx.AsyncClient:
        """A client with this configuration's timeout. The caller closes it."""
        return httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds))


class PesapalUnavailable(Exception):
    """No token could be had. Safe to log: it never holds a secret or a response body."""


def read_json(content: bytes) -> dict[str, Any]:
    """A Pesapal answer as a dict, with every decimal read as `Decimal`, never a float."""
    data = json.loads(content, parse_float=Decimal)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


@dataclass(frozen=True, slots=True)
class _Token:
    value: str
    refresh_at: datetime


class PesapalTokens:
    """The bearer token for Pesapal, fetched once and reused for four minutes."""

    def __init__(self, settings: PesapalSettings, http: httpx.AsyncClient, clock: Clock) -> None:
        self._settings = settings
        self._http = http
        self._clock = clock
        self._token: _Token | None = None
        self._lock = asyncio.Lock()

    async def bearer(self) -> str:
        token = self._token
        if token is not None and self._clock.now() < token.refresh_at:
            return token.value
        async with self._lock:
            token = self._token
            if token is not None and self._clock.now() < token.refresh_at:
                return token.value
            self._token = await self._fetch()
            return self._token.value

    def forget(self) -> None:
        """Drop the cached token, after Pesapal answered 401 to it."""
        self._token = None

    async def _fetch(self) -> _Token:
        settings = self._settings
        try:
            response = await self._http.post(
                f"{settings.base_url}/api/Auth/RequestToken",
                json={
                    "consumer_key": settings.consumer_key.get_secret_value(),
                    "consumer_secret": settings.consumer_secret.get_secret_value(),
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError:
            raise PesapalUnavailable("token_request_failed") from None
        if response.status_code != 200:
            raise PesapalUnavailable(f"token_refused_{response.status_code}")
        try:
            value = read_json(response.content).get("token")
        except ValueError:
            raise PesapalUnavailable("token_unreadable") from None
        if not isinstance(value, str) or not value:
            # Pesapal answers 200 with an `error` object for a wrong key or secret.
            raise PesapalUnavailable("token_refused")
        return _Token(value, self._clock.now() + TOKEN_LIFETIME)
