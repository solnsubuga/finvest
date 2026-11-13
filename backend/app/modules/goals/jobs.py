"""The worker's side of goals: follow a saving into the investment it rolled over into, and
record each day what every group goal member's goal is worth.

No handler here calls a provider. Each does its work in the fenced transaction that also
completes the job, so a job that is run twice changes nothing the second time.
"""

from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import ClaimedJob, Handler, JobContext, JobHandler, PermanentJobError
from app.modules.goals import groups, service
from app.modules.goals.events import FOLLOW_ROLLOVER_KIND
from app.shared.clock import Clock

# The scheduler asks for this once a day, after interest and maturities.
REFRESH_GROUPS_KIND = "goals.refresh_groups"


class GoalJobs:
    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            FOLLOW_ROLLOVER_KIND: self.follow_rollover,
            REFRESH_GROUPS_KIND: self.refresh_groups,
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

    async def refresh_groups(self, job: ClaimedJob, context: JobContext) -> None:
        """Record what each current member's goal is worth today, as the system, so the other
        members see the group's combined value without reading anyone's investments."""

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            return {"recorded": await groups.refresh_values(session, self._clock)}

        await context.apply(effect)
