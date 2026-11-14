"""Check that a webhook request was signed with our hook secret, the standard Svix way.

Finvest's own Resend account sends its events straight to `https://api.finvestug.com/webhooks/
resend`, signed with that webhook's signing secret (`whsec_...`), which Resend's dashboard shows
and `RESEND_HOOK_SECRET` holds. A request carries three headers:

- `svix-id`: the event's id. A retry of the same event carries the same id.
- `svix-timestamp`: when it was signed, in Unix seconds.
- `svix-signature`: one or more `v1,<base64 HMAC-SHA256>` separated by spaces, so a secret
  can be rotated with the old and new signatures both present.

The signed content is `{svix-id}.{svix-timestamp}.{body}`. A timestamp more than five minutes
from now is refused, so a captured request cannot be replayed later.
"""

import base64
import binascii
import hashlib
import hmac
import re
from collections.abc import Mapping
from datetime import datetime, timedelta

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TOLERANCE = timedelta(minutes=5)
SECRET_PREFIX = "whsec_"  # noqa: S105 - the prefix, not a secret
# Svix ids look like `msg_2KWPBgLlAfxdpx2AI54pPJ85f4W`. Kept to a safe shape before storing.
EVENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
MAX_SIGNATURES = 10


class InvalidSignature(Exception):
    """The request was not signed with our secret, or not recently. Answer 401."""


def _key(secret: str) -> bytes:
    if not secret.startswith(SECRET_PREFIX):
        raise ValueError("The hook secret must start with whsec_")
    try:
        key = base64.b64decode(secret.removeprefix(SECRET_PREFIX), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("The hook secret is not valid base64") from None
    if len(key) < 16:
        raise ValueError("The hook secret is too short")
    return key


class WebhookVerifier:
    """Verifies `svix-*` headers against one secret and returns the event's id."""

    def __init__(self, secret: SecretStr) -> None:
        self._key = _key(secret.get_secret_value())

    def sign(self, event_id: str, timestamp: int, body: bytes) -> str:
        """The `v1,...` signature for this content. Used by tests and by nothing else."""
        content = f"{event_id}.{timestamp}.".encode() + body
        digest = hmac.new(self._key, content, hashlib.sha256).digest()
        return "v1," + base64.b64encode(digest).decode()

    def verify(self, headers: Mapping[str, str], body: bytes, now: datetime) -> str:
        event_id = headers.get("svix-id", "")
        timestamp = headers.get("svix-timestamp", "")
        signatures = headers.get("svix-signature", "")
        if not EVENT_ID.fullmatch(event_id) or not re.fullmatch(r"[0-9]{1,12}", timestamp):
            raise InvalidSignature
        signed_at = int(timestamp)
        if abs(now.timestamp() - signed_at) > TOLERANCE.total_seconds():
            raise InvalidSignature
        expected = self.sign(event_id, signed_at, body).encode()
        offered = signatures.split()
        if len(offered) > MAX_SIGNATURES:
            raise InvalidSignature
        # Compare every v1 signature in constant time; any one matching is enough.
        matched = False
        for signature in offered:
            if signature.startswith("v1,"):
                matched |= hmac.compare_digest(signature.encode(), expected)
        if not matched:
            raise InvalidSignature
        return event_id


class ResendHookSettings(BaseSettings):
    """`RESEND_HOOK_SECRET`, read by the API. The name is the one the router's set-up uses."""

    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    resend_hook_secret: SecretStr | None = None

    @field_validator("resend_hook_secret", mode="before")
    @classmethod
    def blank_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("resend_hook_secret")
    @classmethod
    def usable(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            _key(value.get_secret_value())
        return value

    def verifier(self) -> WebhookVerifier | None:
        """None when no secret is set: the route then answers 404 to everyone."""
        return WebhookVerifier(self.resend_hook_secret) if self.resend_hook_secret else None
