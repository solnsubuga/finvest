"""The worker's side of automatic investing. No handler here calls a provider."""

from collections.abc import Mapping

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import ClaimedJob, Handler, JobContext, JobHandler
from app.modules.autoinvest import runs
from app.shared.clock import Clock

OPEN_DUE_KIND = "autoinvest.open_due_runs"
logger = structlog.get_logger()


class AutoinvestJobs:
    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {OPEN_DUE_KIND: self.open_due_runs}

    async def open_due_runs(self, job: ClaimedJob, context: JobContext) -> None:
        async def effect(session: AsyncSession) -> Mapping[str, object]:
            opened = await runs.open_due_runs(session, self._clock)
            return {"due": len(opened.due), "missed": opened.missed}

        await context.apply(effect)
