"""Telling a client about their goals: a saving that is due soon, and a goal that is reached.

The worker runs this once a day, as the system, one page of goals per job: each page is its
own transaction and queues the next page in that transaction, so a run over many goals never
holds one long transaction and a crash repeats one page at most. It raises `goal_top_up_due`
or `goal_reached` through the notifications contract. Each
is keyed so that a second run on the same day, or a run after a missed day, tells nobody twice:
a top-up reminder by the goal and its saving date, a goal reached by the goal and its target.
A client who reaches a goal and then raises its target is told again when they reach the new
one, and a value that dips below the same target and comes back tells nobody twice.

The words name the goal and the date, never an amount. The app shows the figures.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.goals import saving_rules, service
from app.modules.goals.domain.goals import GoalStatus
from app.modules.goals.domain.reminders import top_up_to_remind
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import InvestmentStatus
from app.modules.notifications import contract as notifications
from app.shared.business_day import business_date
from app.shared.clock import Clock

BATCH = 200

_ACTIVE = text(
    "SELECT id FROM goals WHERE status = 'active' AND id > :after ORDER BY id LIMIT :limit"
)
# The savings the client made themselves. A rollover is the same money carried on, not a top-up.
_OWN_SAVINGS = text(
    "SELECT investment_id, created_at FROM goal_savings "
    "WHERE goal_id = :goal_id AND rolled_from_investment_id IS NULL"
)
# A saving that was cancelled or undone did not put money towards the goal.
_NOT_SAVED = frozenset({InvestmentStatus.CANCELLED, InvestmentStatus.UNWOUND})


def reached_key(goal_id: str, target_minor: int) -> str:
    """The key that tells a client once that a goal reached this target."""
    return f"goal-reached:{goal_id}:{target_minor}"


async def send_reminders(session: AsyncSession, clock: Clock) -> dict[str, int]:
    """Raise today's goal notifications for every goal, in one transaction. Returns how many
    of each were new. The worker pages through them with `send_page` instead."""
    told = {"top_up_due": 0, "reached": 0}
    after = ""
    while True:
        page = await send_page(session, clock, after=after)
        for name, count in page.told.items():
            told[name] += count
        if page.last_id is None:
            return told
        after = page.last_id


@dataclass(frozen=True, slots=True)
class Page:
    """What one page of the run told, and the last goal it read: None when it read every goal
    left, so there is no next page."""

    told: dict[str, int]
    last_id: str | None


async def send_page(
    session: AsyncSession, clock: Clock, *, after: str = "", limit: int | None = None
) -> Page:
    """Raise today's goal notifications for the next `limit` (default `BATCH`) active goals
    after `after`."""
    limit = limit or BATCH
    now = clock.now()
    today = business_date(now)
    told = {"top_up_due": 0, "reached": 0}
    goal_ids = list(
        (await session.execute(_ACTIVE, {"after": after, "limit": limit})).scalars().all()
    )
    # A goal saved by its own monthly rule needs no reminder to pay by hand.
    ruled = await saving_rules.goals_with_rules(session, goal_ids)
    for goal_id in goal_ids:
        goal = await service.get_goal(session, goal_id)
        if goal.status is not GoalStatus.ACTIVE:
            continue
        reached = await service.goal_value(session, goal) >= goal.target
        link = notifications.Link("goal", goal.id)
        if reached and await notifications.notify(
            session,
            event_name="goal_reached",
            client_id=goal.client_id,
            idempotency_key=reached_key(goal.id, goal.target.minor),
            variables={"goal": goal.name},
            link=link,
            now=now,
        ):
            told["reached"] += 1
        due = (
            None
            if goal.id in ruled
            else top_up_to_remind(
                set_up_on=business_date(goal.created_at),
                target_date=goal.target_date,
                today=today,
                reached=reached,
                saved_on=await _saved_on(session, goal.id),
            )
        )
        if due is not None and await notifications.notify(
            session,
            event_name="goal_top_up_due",
            client_id=goal.client_id,
            idempotency_key=f"goal-top-up:{goal.id}:{due.isoformat()}",
            variables={"goal": goal.name, "date": due},
            link=link,
            now=now,
        ):
            told["top_up_due"] += 1
    return Page(told, goal_ids[-1] if len(goal_ids) == limit else None)


async def _saved_on(session: AsyncSession, goal_id: str) -> list[date]:
    rows = (await session.execute(_OWN_SAVINGS, {"goal_id": goal_id})).all()
    if not rows:
        return []
    found = await investments.get_investments(session, [row.investment_id for row in rows])
    saved = {item.id for item in found if item.status not in _NOT_SAVED}
    return [business_date(row.created_at) for row in rows if row.investment_id in saved]
