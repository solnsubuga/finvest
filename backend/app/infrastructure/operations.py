"""Counts of stopped work, for the operations health check and its alert.

The counts come from `finvest_operations_signals`, which returns numbers only. See migration
0130 for what each one counts.
"""

from datetime import datetime

from sqlalchemy import text

from app.infrastructure.database import Database

SIGNALS = (
    "jobs_overdue",
    "jobs_stalled",
    "jobs_failed",
    "schedules_late",
    "payouts_waiting_on_wallet",
    "interest_behind",
)


async def operations_signals(database: Database, now: datetime) -> dict[str, int]:
    async with database.transaction() as session:
        rows = (
            await session.execute(
                text("SELECT signal, total FROM finvest_operations_signals(:now)"), {"now": now}
            )
        ).all()
    return {row.signal: int(row.total) for row in rows}
