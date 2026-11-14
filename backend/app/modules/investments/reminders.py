"""Reminding a client that an investment matures soon, so they choose what happens to it.

The scheduler asks for this every morning, so no text arrives at night. It reads how many days
before maturity to remind from the platform settings (7 days and 1 day unless staff changed
it), and raises `investment_maturing` for every active investment maturing on one of those
days. Each reminder is keyed by the investment, its maturity date and how many days before it
is, so a second run on the same day tells nobody twice, and a run that missed a morning simply
reminds with fewer days to go the next time a reminder day comes round.

The words name the product and the date, never an amount.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.investments.catalogue import product_from_row, product_row
from app.modules.notifications import contract as notifications
from app.modules.platform_settings import service as platform_settings
from app.shared.clock import Clock

BATCH = 200

_DUE = text(
    "SELECT i.id, i.client_id, p.code AS product_code, i.maturity_date "
    "FROM investments i JOIN investment_products p ON p.id = i.product_id "
    "WHERE i.status = 'active' AND i.maturity_date = ANY(:days) AND i.id > :after "
    "ORDER BY i.id LIMIT :limit"
)


def reminder_key(investment_id: str, maturity_date: date, days_before: int) -> str:
    """The key that tells a client once about one reminder for one maturity."""
    return f"investment-maturing:{investment_id}:{maturity_date.isoformat()}:{days_before}"


@dataclass(frozen=True, slots=True)
class Batch:
    """How many reminders were new, and the last investment read: None when none are left."""

    told: int
    last_id: str | None


async def remind_batch(
    session: AsyncSession, run_date: date, clock: Clock, *, after: str = "", limit: int = BATCH
) -> Batch:
    """Raise the reminders due on `run_date` for the next `limit` investments after `after`."""
    now = clock.now()
    reminders = await platform_settings.maturity_reminders(session, now)
    by_day = {run_date + timedelta(days=days): days for days in reminders.days_before}
    if not by_day:
        return Batch(0, None)
    rows = (
        await session.execute(_DUE, {"days": list(by_day), "after": after, "limit": limit})
    ).all()
    names: dict[str, str] = {}
    told = 0
    for row in rows:
        if row.product_code not in names:
            names[row.product_code] = product_from_row(
                await product_row(session, row.product_code)
            ).name
        if await notifications.notify(
            session,
            event_name="investment_maturing",
            client_id=row.client_id,
            idempotency_key=reminder_key(row.id, row.maturity_date, by_day[row.maturity_date]),
            variables={"product": names[row.product_code], "date": row.maturity_date},
            link=notifications.Link("investment", row.id),
            now=now,
        ):
            told += 1
    return Batch(told, rows[-1].id if len(rows) == limit else None)
