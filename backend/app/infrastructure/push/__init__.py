"""Push notifications to the client app, through Expo's push service.

The app runs on Expo, so its devices hold Expo push tokens and Expo passes each message on to
Apple or Google. A transport sends one notification to one client's devices and sorts the
outcome, so the caller knows whether trying again is safe:

- `PushDeferred`: Expo accepted nothing. Safe to try again later.
- `PushRejected`: Expo refused the request or the credentials. Retrying cannot help.
- `PushUncertain`: the request may have reached Expo. Expo has no idempotency key, so a
  repeat could show the client the same alert twice. Never sent again blindly.

A token Expo reports as no longer registered comes back in `PushResult.dead_tokens`, so the
caller can stop using it. Error messages are safe to store: they never hold a token, a title
or a body.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, Self

import httpx
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# What Expo hands the app. Both spellings are in use.
TOKEN = re.compile(r"^Expo(?:nent)?PushToken\[[A-Za-z0-9_-]{10,200}\]$")
MAX_TITLE = 120
MAX_BODY = 2000
# Expo takes at most 100 messages in one request. A client has a handful of devices.
MAX_DEVICES = 100


class InvalidPush(ValueError):
    """The message cannot be sent as given. Retrying the same message cannot succeed."""


class PushFailure(Exception):
    code = "push_failed"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.safe_message = message
        if code is not None:
            self.code = code


class PushDeferred(PushFailure):
    code = "push_deferred"


class PushRejected(PushFailure):
    code = "push_rejected"


class PushUncertain(PushFailure):
    code = "push_uncertain"


def check_token(value: str) -> str:
    if not TOKEN.fullmatch(value):
        raise InvalidPush("A push token is not valid")
    return value


@dataclass(frozen=True)
class PushMessage:
    tokens: tuple[str, ...] = field(repr=False)
    title: str = field(repr=False)
    body: str = field(repr=False)
    # Tells the app which screen to open: a kind and an ID, never content.
    data: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tokens or len(self.tokens) > MAX_DEVICES:
            raise InvalidPush(f"A push goes to 1 to {MAX_DEVICES} devices")
        for token in self.tokens:
            check_token(token)
        if not self.title.strip() or len(self.title) > MAX_TITLE:
            raise InvalidPush(f"A push title needs 1 to {MAX_TITLE} characters")
        if not self.body.strip() or len(self.body) > MAX_BODY:
            raise InvalidPush(f"A push body needs 1 to {MAX_BODY} characters")


@dataclass(frozen=True)
class PushResult:
    provider: str
    # Devices that took the message.
    delivered: int
    # Tokens Expo says are no longer registered: the app was removed or signed out.
    dead_tokens: tuple[str, ...] = field(default=(), repr=False)


class PushTransport(Protocol):
    name: str

    async def send(self, message: PushMessage) -> PushResult: ...


class DisabledPush:
    """No push service is configured: every send is deferred until its job expires."""

    name = "none"

    async def send(self, message: PushMessage) -> PushResult:
        raise PushDeferred("Push delivery is not configured", code="push_not_configured")


class ConsolePush:
    """Local development only: print the alert. A deployment uses Expo."""

    name = "local-console"

    async def send(self, message: PushMessage) -> PushResult:
        print(  # noqa: T201
            f"[local push to {len(message.tokens)} device(s)] {message.title}: {message.body}",
            flush=True,
        )
        return PushResult(provider=self.name, delivered=len(message.tokens))


class ExpoPush:
    """Send through Expo's push API. One request carries the message to every device."""

    name = "expo"

    def __init__(
        self,
        *,
        access_token: SecretStr | None = None,
        base_url: str = "https://exp.host",
        timeout: float = 30,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.access_token = access_token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    def payload(self, message: PushMessage) -> list[dict[str, object]]:
        return [
            {
                "to": token,
                "title": message.title,
                "body": message.body,
                "data": dict(message.data),
                "sound": "default",
                "priority": "high",
            }
            for token in message.tokens
        ]

    async def send(self, message: PushMessage) -> PushResult:
        headers = {"Accept": "application/json", "User-Agent": "finvest"}
        if self.access_token is not None and self.access_token.get_secret_value():
            headers["Authorization"] = f"Bearer {self.access_token.get_secret_value()}"
        try:
            if self._client is not None:
                response = await self._post(self._client, headers, message)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await self._post(client, headers, message)
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            # The request never left: nothing can have been shown.
            raise PushDeferred(f"Expo is unreachable ({type(error).__name__})") from None
        except httpx.HTTPError as error:
            raise PushUncertain(f"Expo did not answer ({type(error).__name__})") from None
        status = response.status_code
        if status in (401, 403):
            raise PushRejected(f"Expo refused the access token ({status})", code="push_auth_failed")
        if status == 429 or status >= 500:
            # Expo asks for the request to be sent again later in both cases.
            raise PushDeferred(f"Expo deferred the message ({status})")
        if not 200 <= status < 300:
            raise PushRejected(f"Expo rejected the message ({status})")
        return self._result(message.tokens, response)

    def _result(self, tokens: Sequence[str], response: httpx.Response) -> PushResult:
        tickets = _tickets(response)
        if tickets is None or len(tickets) != len(tokens):
            # Expo took the request, so the alert may be on the client's phone already.
            raise PushUncertain("Expo answered in a way this code does not read")
        delivered = 0
        dead: list[str] = []
        refused: str | None = None
        for token, ticket in zip(tokens, tickets, strict=True):
            if ticket.get("status") == "ok":
                delivered += 1
                continue
            details = ticket.get("details")
            reason = details.get("error") if isinstance(details, dict) else None
            if reason == "DeviceNotRegistered":
                dead.append(token)
            elif isinstance(reason, str) and re.fullmatch(r"[A-Za-z]{1,64}", reason):
                refused = reason
            else:
                refused = "Unknown"
        if delivered == 0 and refused is not None:
            raise PushRejected(f"Expo refused the message ({refused})")
        return PushResult(provider=self.name, delivered=delivered, dead_tokens=tuple(dead))

    async def _post(
        self, client: httpx.AsyncClient, headers: dict[str, str], message: PushMessage
    ) -> httpx.Response:
        return await client.post(
            f"{self.base_url}/--/api/v2/push/send",
            json=self.payload(message),
            headers=headers,
            timeout=self.timeout,
        )


def _tickets(response: httpx.Response) -> list[dict[str, object]] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or not all(isinstance(ticket, dict) for ticket in data):
        return None
    return data


class PushSettings(BaseSettings):
    """Read from `FINVEST_PUSH_*` by the worker."""

    model_config = SettingsConfigDict(
        env_prefix="FINVEST_PUSH_", extra="ignore", hide_input_in_errors=True
    )

    backend: Literal["none", "local-console", "expo"] = "none"
    # Needed only when the Expo project has enhanced push security turned on.
    expo_access_token: SecretStr | None = None
    expo_url: str = "https://exp.host"
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    environment: str | None = Field(default=None, validation_alias="FINVEST_ENVIRONMENT")

    @field_validator("expo_access_token", mode="before")
    @classmethod
    def blank_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def safe(self) -> Self:
        if self.backend == "local-console" and self.environment not in (None, "", "local"):
            # It prints what a client was told, which is personal data in a log.
            raise ValueError("The local-console push backend may only run on a developer's machine")
        if self.backend == "expo" and not self.expo_url.startswith("https://"):
            raise ValueError("FINVEST_PUSH_EXPO_URL must use https")
        return self

    def transport(self) -> PushTransport:
        if self.backend == "expo":
            return ExpoPush(
                access_token=self.expo_access_token,
                base_url=self.expo_url,
                timeout=self.timeout_seconds,
            )
        return ConsolePush() if self.backend == "local-console" else DisabledPush()


__all__ = [
    "ConsolePush",
    "DisabledPush",
    "ExpoPush",
    "InvalidPush",
    "PushDeferred",
    "PushFailure",
    "PushMessage",
    "PushRejected",
    "PushResult",
    "PushSettings",
    "PushTransport",
    "PushUncertain",
    "check_token",
]
