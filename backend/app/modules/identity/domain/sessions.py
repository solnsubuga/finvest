"""Session tokens and lifetimes. The token is a bearer secret; only its digest is stored."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

SecondStep = Literal["passkey", "authenticator", "code", "signup"]
Transport = Literal["cookie", "token"]

LOGIN_CHALLENGE_LIFETIME = timedelta(minutes=5)
# docs/rules.md: signing in with a code in place of a passkey, or resetting a password, limits
# withdrawals to known accounts for this long.
RESTRICTION = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    """`idle` ends a session nobody is using. `absolute` ends it however busy it is."""

    idle: timedelta
    absolute: timedelta

    def __post_init__(self) -> None:
        if not timedelta(0) < self.idle <= self.absolute:
            raise ValueError("A session needs 0 < idle <= absolute")

    def expiry(self, created_at: datetime, now: datetime) -> tuple[datetime, datetime]:
        """The idle and absolute expiry of a session created at `created_at` and used `now`."""
        absolute = created_at + self.absolute
        return min(now + self.idle, absolute), absolute


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def restricted_until(second_step: SecondStep, now: datetime) -> datetime | None:
    """A code proves less than a passkey, so it restricts the account for a day."""
    return now + RESTRICTION if second_step == "code" else None
