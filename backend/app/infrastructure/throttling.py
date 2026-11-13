"""Attempt limits counted in PostgreSQL, so they hold across every API process.

Each key is a digest naming what is limited (an account, a caller's address). A key allows
`limit` attempts in a 15-minute window. The attempt is counted before it is acted on, so
concurrent guesses cannot overshoot. Past the limit the caller is refused with the seconds left.
"""

import hashlib
from datetime import timedelta

from sqlalchemy import text

from app.infrastructure.database import Database
from app.shared.clock import Clock
from app.shared.errors import ApplicationError

THROTTLE_WINDOW = timedelta(minutes=15)


def throttle_key(kind: str, value: str) -> bytes:
    return hashlib.sha256(f"{kind}\x00{value}".encode()).digest()


async def reserve_attempts(database: Database, clock: Clock, keys: list[tuple[bytes, int]]) -> None:
    """Count one attempt against every key. Raises `rate_limited` if any is past its limit."""
    now = clock.now()
    floor = now - THROTTLE_WINDOW
    retry_after: int | None = None
    # Keys are taken in a fixed order so two requests can never wait on each other's rows.
    async with database.transaction() as session:
        for key, limit in sorted(keys):
            row = (
                await session.execute(
                    text(
                        "INSERT INTO identity_throttles AS t "
                        "(key_hash, attempts, window_started_at) "
                        "VALUES (:key, 1, :now) ON CONFLICT (key_hash) DO UPDATE SET "
                        "attempts = CASE WHEN t.window_started_at <= :floor THEN 1 "
                        "ELSE t.attempts + 1 END, "
                        "window_started_at = CASE WHEN t.window_started_at <= :floor "
                        "THEN :now ELSE t.window_started_at END "
                        "RETURNING attempts, window_started_at"
                    ),
                    {"key": key, "now": now, "floor": floor},
                )
            ).one()
            if row.attempts > limit:
                remaining = row.window_started_at + THROTTLE_WINDOW - now
                retry_after = max(retry_after or 0, max(1, int(remaining.total_seconds()) + 1))
    if retry_after is not None:
        raise ApplicationError("rate_limited", retry_after_seconds=retry_after)


async def clear_attempts(database: Database, keys: list[bytes]) -> None:
    """Forget the count once the caller has proved who they are."""
    async with database.transaction() as session:
        await session.execute(
            text("DELETE FROM identity_throttles WHERE key_hash = ANY(:keys)"), {"keys": keys}
        )
