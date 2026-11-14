"""What other modules may read of a goal. Read only, and it imports nothing of goals' own, so a
module that goals itself reads from (investments) can import it without a cycle.

Investments reads it when a client chooses to save an investment's interest to a goal, and
again at maturity, when the interest becomes a new saving. Each read runs in the caller's
transaction, under the caller's row policies: a client sees only their own goals, and the
worker sees every one.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.money import Currency

_GOAL = text(
    "SELECT id, client_id, name, currency, product_code, status, locked_until "
    "FROM goals WHERE id = :id"
)
_SAVED_TO = text("SELECT goal_id FROM goal_savings WHERE investment_id = :investment_id")
_NAMES = text("SELECT id, name FROM goals WHERE id = ANY(CAST(:ids AS text[]))")


@dataclass(frozen=True, slots=True)
class GoalForInterest:
    """What decides whether, and where, interest can be saved to a goal."""

    id: str
    client_id: str
    name: str
    currency: Currency
    # The product every saving towards the goal goes into.
    product_code: str
    active: bool
    # The goal's lock, as the goal keeps it. Lapsed once the day has come.
    locked_until: date | None

    def locked_on(self, today: date) -> date | None:
        """The day its savings are free again, if it is locked today."""
        if self.locked_until is None or self.locked_until <= today:
            return None
        return self.locked_until


async def goal_for_interest(session: AsyncSession, goal_id: str) -> GoalForInterest | None:
    """The goal, if this transaction may see it."""
    row = (await session.execute(_GOAL, {"id": goal_id})).one_or_none()
    if row is None:
        return None
    return GoalForInterest(
        id=row.id,
        client_id=row.client_id,
        name=row.name,
        currency=Currency(row.currency.strip()),
        product_code=row.product_code.strip(),
        active=row.status == "active",
        locked_until=row.locked_until,
    )


async def goal_saved_to(session: AsyncSession, investment_id: str) -> str | None:
    """The goal the investment is a saving towards, if any."""
    found = await session.scalar(_SAVED_TO, {"investment_id": investment_id})
    return None if found is None else str(found)


async def goal_names(session: AsyncSession, goal_ids: list[str]) -> dict[str, str]:
    """The names of the goals this transaction may see, by ID. The client's own words."""
    if not goal_ids:
        return {}
    rows = await session.execute(_NAMES, {"ids": sorted(set(goal_ids))})
    return {row.id: row.name for row in rows.all()}
