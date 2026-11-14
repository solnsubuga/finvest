"""The worker's side of goals: follow a saving into the investment it rolled over into, record
each day what every group goal member's goal is worth, and tell clients about their goals.

No handler here calls a provider. Each does its work in the fenced transaction that also
completes the job, so a job that is run twice changes nothing the second time.
"""

from collections.abc import Mapping
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import (
    ClaimedJob,
    Handler,
    JobContext,
    JobHandler,
    JobRequest,
    PermanentJobError,
    enqueue,
)
from app.modules.goals import groups, reminders, saving_rules, service
from app.modules.goals.events import FOLLOW_ROLLOVER_KIND, LINK_PLAN_RUN_KIND
from app.shared.clock import Clock

# The scheduler asks for this once a day, after interest and maturities.
REFRESH_GROUPS_KIND = "goals.refresh_groups"
# The scheduler asks for this once a day, in the morning, so a reminder is not sent at night.
SEND_REMINDERS_KIND = "goals.send_reminders"
# A later page of the morning run. Old enough to finish the run, too young to text at night.
REMINDER_PAGE_MAX_AGE = timedelta(hours=10)


class GoalJobs:
    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            FOLLOW_ROLLOVER_KIND: self.follow_rollover,
            LINK_PLAN_RUN_KIND: self.link_plan_run,
            REFRESH_GROUPS_KIND: self.refresh_groups,
            SEND_REMINDERS_KIND: self.send_reminders,
        }

    async def follow_rollover(self, job: ClaimedJob, context: JobContext) -> None:
        """An investment rolled over. If it was saved towards a goal, so is the new one."""
        data = job.payload.get("data")
        data = data if isinstance(data, Mapping) else {}
        old, new = data.get("investment_id"), data.get("new_investment_id")
        if not isinstance(old, str) or not isinstance(new, str):
            raise PermanentJobError(code="investment_id_missing")

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            followed = await service.follow_rollover(
                session, investment_id=old, new_investment_id=new, clock=self._clock
            )
            return {"followed": followed}

        await context.apply(effect)

    async def link_plan_run(self, job: ClaimedJob, context: JobContext) -> None:
        """An automatic investing run asked for its investment. If the plan is a goal's saving
        rule, the investment is a saving towards that goal."""
        data = job.payload.get("data")
        data = data if isinstance(data, Mapping) else {}
        plan_id, investment_id = data.get("plan_id"), data.get("investment_id")
        client_id = data.get("client_id")
        if not all(isinstance(value, str) for value in (plan_id, investment_id, client_id)):
            raise PermanentJobError(code="plan_run_missing")
        assert isinstance(plan_id, str) and isinstance(investment_id, str)
        assert isinstance(client_id, str)

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            linked = await saving_rules.link_plan_run(
                session,
                plan_id=plan_id,
                client_id=client_id,
                investment_id=investment_id,
                clock=self._clock,
            )
            return {"linked": linked}

        await context.apply(effect)

    async def refresh_groups(self, job: ClaimedJob, context: JobContext) -> None:
        """Record what each current member's goal is worth today, as the system, so the other
        members see the group's combined value without reading anyone's investments."""

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            return {"recorded": await groups.refresh_values(session, self._clock)}

        await context.apply(effect)

    async def send_reminders(self, job: ClaimedJob, context: JobContext) -> None:
        """Remind clients of a goal saving due in the next three days, and tell them when a
        goal reaches its target. Each is keyed, so running it again tells nobody twice.

        One page of goals per job. The scheduler's job starts at the first goal; a page that
        was full queues the next page, after its last goal, in the same transaction.
        """
        after = job.payload.get("after", "")
        if not isinstance(after, str):
            raise PermanentJobError(code="after_not_text")

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            page = await reminders.send_page(session, self._clock, after=after)
            if page.last_id is not None:
                await enqueue(
                    session,
                    JobRequest(
                        kind=SEND_REMINDERS_KIND,
                        idempotency_key=f"after:{job.id}",
                        payload={"after": page.last_id},
                        max_age=REMINDER_PAGE_MAX_AGE,
                    ),
                    now=self._clock.now(),
                )
            return {**page.told, "more": page.last_id is not None}

        await context.apply(effect)
