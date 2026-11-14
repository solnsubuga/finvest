"""What every ioTec Pay adapter shares: its settings and the OAuth token.

ioTec Pay authenticates with the OAuth 2.0 client credentials flow: the client ID and secret
are exchanged at `id.iotec.io` for a bearer token that lasts a few minutes. Collections (in
payments) and disbursements (in payouts) both use one `IotecTokens` per process, so one token
is fetched and reused until shortly before it expires.

Nothing here moves money or knows about deposits. A failure to get a token raises
`IotecUnavailable`, which carries no secret and no response body: without a token no request
reached ioTec, so the caller can treat it as "nothing was sent".
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import httpx
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.shared.clock import Clock

# Refresh this long before ioTec says the token expires, so a request never carries a token
# that expires on the way.
REFRESH_MARGIN = timedelta(seconds=30)
# ioTec's test wallets hold the test currency ITX. It is allowed only where the sandbox is.
TEST_CURRENCY = "ITX"


class IotecSettings(BaseSettings):
    """From `FINVEST_IOTEC_*`. Required only when `FINVEST_PAYMENTS_PROVIDER=iotec`."""

    model_config = SettingsConfigDict(
        env_prefix="FINVEST_IOTEC_", extra="ignore", hide_input_in_errors=True
    )

    client_id: str = Field(min_length=1)
    client_secret: SecretStr
    wallet_id: str = Field(min_length=1)
    token_url: str = "https://id.iotec.io/connect/token"  # noqa: S105 - an address, not a secret
    pay_url: str = "https://pay.iotec.io"
    timeout_seconds: float = Field(default=30, gt=0, le=60)
    # What ioTec calls our currency. `ITX` only for an ioTec test wallet.
    currency_code: Literal["UGX", "ITX"] = "UGX"
    # The value ioTec is set to send in the `Authorization` header of its callbacks, as
    # `Bearer <token>`. Set in the ioTec portal under the wallet's callback URLs.
    callback_token: SecretStr | None = None

    def http_client(self) -> httpx.AsyncClient:
        """A client with this configuration's timeout. The caller closes it."""
        return httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds))


class IotecUnavailable(Exception):
    """No token could be had. Safe to log: it never holds a secret or a response body."""


@dataclass(frozen=True, slots=True)
class _Token:
    value: str
    refresh_at: datetime


class IotecTokens:
    """The bearer token for ioTec Pay, fetched once and reused until shortly before expiry."""

    def __init__(self, settings: IotecSettings, http: httpx.AsyncClient, clock: Clock) -> None:
        self._settings = settings
        self._http = http
        self._clock = clock
        self._token: _Token | None = None
        self._lock = asyncio.Lock()

    async def bearer(self) -> str:
        """The current token. Raises `IotecUnavailable` when ioTec gives none."""
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
        """Drop the cached token, after ioTec answered 401 to it."""
        self._token = None

    async def _fetch(self) -> _Token:
        settings = self._settings
        try:
            response = await self._http.post(
                settings.token_url,
                data={
                    "client_id": settings.client_id,
                    "client_secret": settings.client_secret.get_secret_value(),
                    "grant_type": "client_credentials",
                },
            )
        except httpx.HTTPError:
            raise IotecUnavailable("token_request_failed") from None
        if response.status_code != 200:
            raise IotecUnavailable(f"token_refused_{response.status_code}")
        try:
            data = response.json()
            value, lifetime = data["access_token"], data["expires_in"]
        except (ValueError, KeyError, TypeError):
            raise IotecUnavailable("token_unreadable") from None
        if not isinstance(value, str) or not value or type(lifetime) is not int or lifetime <= 0:
            raise IotecUnavailable("token_unreadable")
        expires = self._clock.now() + timedelta(seconds=lifetime)
        return _Token(value, max(self._clock.now(), expires - REFRESH_MARGIN))
