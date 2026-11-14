"""Withdrawal locks: until a day, an investment is not withdrawn early or sent to another client.

A client sets them through a goal (see `goals/service.py`): locking a goal locks each of its
savings until the goal's date. The database (0303) holds the rule that matters: a lock is only
lengthened, or shortened to no sooner than seven days from today, and nobody removes one. On the
day itself the lock lapses by itself.

A rolled over investment keeps the lock of the one it came from (`maturity.py`).
"""

from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The field code of a refusal while an investment is locked.
LOCKED = "locked"
# Waiting a week before money comes out is the point of undoing a lock (Solo's rule).
UNLOCK_WAIT_DAYS = 7

_LOCK = text(
    "INSERT INTO investment_withdrawal_locks (investment_id, client_id, locked_until, "
    "created_at, updated_at) "
    "SELECT i.id, i.client_id, CAST(:until AS date), :now, :now FROM investments i "
    "WHERE i.id = ANY(CAST(:ids AS text[])) AND i.client_id = :client_id "
    "AND i.status IN ('awaiting_payment', 'active') "
    "ON CONFLICT (investment_id) DO UPDATE SET locked_until = EXCLUDED.locked_until, "
    "updated_at = EXCLUDED.updated_at "
    "WHERE investment_withdrawal_locks.locked_until < EXCLUDED.locked_until"
)
# Seven days from the database's own today at the soonest, as its guard counts them, so a clock
# a moment behind it at midnight is not refused.
_SOONEST = (
    "greatest(CAST(:on AS date), (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date + 7)"
)
_SHORTEN = text(
    f"UPDATE investment_withdrawal_locks SET locked_until = {_SOONEST}, updated_at = :now "  # noqa: S608
    f"WHERE investment_id = ANY(CAST(:ids AS text[])) AND locked_until > {_SOONEST}"
)
_INHERIT = text(
    "INSERT INTO investment_withdrawal_locks (investment_id, client_id, locked_until, "
    "created_at, updated_at) "
    "SELECT :new_id, client_id, locked_until, :now, :now FROM investment_withdrawal_locks "
    "WHERE investment_id = :old_id AND locked_until > :today "
    "ON CONFLICT (investment_id) DO NOTHING"
)


async def lock_withdrawals(
    session: AsyncSession, *, client_id: str, investment_ids: list[str], until: date, now: datetime
) -> None:
    """Lock the client's waiting and running investments among `investment_ids` until `until`.

    A lock that already runs later is kept. Others of the list, closed ones or another
    client's, are passed over.
    """
    if not investment_ids:
        return
    await session.execute(
        _LOCK, {"ids": investment_ids, "client_id": client_id, "until": until, "now": now}
    )


async def unlock_withdrawals(
    session: AsyncSession, *, investment_ids: list[str], on: date, now: datetime
) -> None:
    """Free the investments on `on`, which is seven days from today at the soonest. A lock that
    ends sooner is left as it is."""
    if not investment_ids:
        return
    await session.execute(_SHORTEN, {"ids": investment_ids, "on": on, "now": now})


async def inherit_lock(
    session: AsyncSession, *, old_id: str, new_id: str, today: date, now: datetime
) -> None:
    """Give a rolled over investment the lock of the one it came from, if it still runs."""
    await session.execute(
        _INHERIT, {"old_id": old_id, "new_id": new_id, "today": today, "now": now}
    )


def is_locked(locked_until: date | None, today: date) -> bool:
    return locked_until is not None and today < locked_until
