"""Email transports: the Resend HTTP API, and a disabled transport that fails closed.

A transport sends one message and sorts every failure, so the caller knows whether trying
again is safe:

- `EmailDeferred`: nothing was accepted, or the provider drops a repeat. Retry later.
- `EmailRejected`: the provider refused the message or the credentials. Retrying cannot help.

Resend remembers an idempotency key for 24 hours, so a send whose answer was lost is safe to
repeat with the same key. That is why there is no uncertain outcome here.

Error messages are safe to store: they never contain addresses, content or credentials.
"""

import base64
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import SecretStr

from app.infrastructure.email.message import MAX_TAGS, TAG, EmailMessage, InvalidEmail, Sender


class EmailFailure(Exception):
    code = "email_failed"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.safe_message = message
        if code is not None:
            self.code = code


class EmailDeferred(EmailFailure):
    code = "email_deferred"


class EmailRejected(EmailFailure):
    code = "email_rejected"


@dataclass(frozen=True)
class SendResult:
    provider: str
    provider_message_id: str | None


class EmailTransport(Protocol):
    name: str

    async def send(self, message: EmailMessage) -> SendResult: ...


class DisabledTransport:
    """No email provider is configured: every send fails, and queued messages wait to retry."""

    name = "none"

    async def send(self, message: EmailMessage) -> SendResult:
        raise EmailDeferred("Email delivery is not configured", code="email_not_configured")


class ResendTransport:
    """Send through Resend's HTTP API with its idempotency key, so a retry never duplicates.

    `tags` go on every message this transport sends, after the message's own. The shared
    webhook router at hooks.fidiba.com reads `fidiba_environment` to send Resend's events for
    a message back to the environment that sent it.
    """

    name = "resend"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        sender: Sender,
        base_url: str = "https://api.resend.com",
        timeout: float = 30,
        client: httpx.AsyncClient | None = None,
        tags: Mapping[str, str] | None = None,
    ) -> None:
        if not api_key.get_secret_value():
            raise ValueError("A Resend API key is required")
        self.tags = dict(tags or {})
        if len(self.tags) > MAX_TAGS or not all(
            TAG.fullmatch(name) and TAG.fullmatch(value) for name, value in self.tags.items()
        ):
            raise ValueError("A transport tag is not valid")
        self.api_key = api_key
        self.sender = sender
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    def payload(self, message: EmailMessage) -> dict[str, object]:
        body: dict[str, object] = {
            "from": self.sender.header(),
            "to": [message.to],
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
        }
        tags = {**message.tags, **self.tags}
        if len(tags) > MAX_TAGS:
            raise InvalidEmail("A message has too many tags")
        if tags:
            body["tags"] = [{"name": k, "value": v} for k, v in tags.items()]
        if message.attachments:
            body["attachments"] = [
                {
                    "filename": file.file_name,
                    "content": base64.b64encode(file.content).decode("ascii"),
                    "content_type": file.content_type,
                }
                for file in message.attachments
            ]
        return body

    async def send(self, message: EmailMessage) -> SendResult:
        headers = {
            "Authorization": f"Bearer {self.api_key.get_secret_value()}",
            "Idempotency-Key": message.idempotency_key,
            "User-Agent": "finvest",
        }
        try:
            if self._client is not None:
                response = await self._post(self._client, headers, message)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await self._post(client, headers, message)
        except httpx.HTTPError as error:
            # The idempotency key makes a repeat safe even if the first request arrived.
            raise EmailDeferred(f"Resend is unreachable ({type(error).__name__})") from None
        status = response.status_code
        if 200 <= status < 300:
            found = response.json().get("id") if _is_json(response) else None
            return SendResult(
                provider=self.name,
                provider_message_id=found if isinstance(found, str) else None,
            )
        # Resend names the problem (`validation_error`, `invalid_from_address`). Its message
        # can quote addresses, so only the name is kept.
        reason = f"{status} {name}" if (name := _error_name(response)) else str(status)
        if status in (401, 403):
            raise EmailRejected(f"Resend refused the API key ({reason})", code="email_auth_failed")
        if status in (408, 409, 429) or status >= 500:
            raise EmailDeferred(f"Resend deferred the message ({reason})")
        raise EmailRejected(f"Resend rejected the message ({reason})")

    async def _post(
        self, client: httpx.AsyncClient, headers: dict[str, str], message: EmailMessage
    ) -> httpx.Response:
        return await client.post(
            f"{self.base_url}/emails",
            json=self.payload(message),
            headers=headers,
            timeout=self.timeout,
        )


def _error_name(response: httpx.Response) -> str | None:
    name = response.json().get("name") if _is_json(response) else None
    return name if isinstance(name, str) and re.fullmatch(r"[a-z_]{1,64}", name) else None


def _is_json(response: httpx.Response) -> bool:
    if not response.headers.get("content-type", "").startswith("application/json"):
        return False
    try:
        return isinstance(response.json(), dict)
    except ValueError:
        return False
