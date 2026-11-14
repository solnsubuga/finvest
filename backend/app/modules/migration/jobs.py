"""The worker's side of the switch: carry every client of an approved switch over.

No handler here calls a provider. The carry-over works in one transaction per client, each
checking the job's lease first, and completes the job at the end. The sealed sheets are removed
then only if every client was carried over: a client noted as not carried over is still only in
them.
A run that stops part way is run again and carries on from the first client not yet done.
"""

from collections.abc import Mapping

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import ClaimedJob, Handler, JobContext, JobHandler, PermanentJobError
from app.infrastructure.jobs import queue as job_queue
from app.infrastructure.sealing import Sealer
from app.modules.migration import carry_over, switch
from app.shared.clock import Clock

logger = structlog.get_logger()


class MigrationJobs:
    def __init__(
        self, database: Database, sealer: Sealer, clock: Clock, client_app: str | None
    ) -> None:
        self._database = database
        self._sealer = sealer
        self._clock = clock
        # Where the claim links point: the client app's origin.
        self._client_app = client_app

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {switch.CARRY_OVER: self.carry_over}

    async def carry_over(self, job: ClaimedJob, context: JobContext) -> None:
        switch_id = job.payload.get("switch_id")
        if not isinstance(switch_id, str):
            raise PermanentJobError("The job names no switch", code="invalid_payload")
        if not self._client_app:
            # Without the client app's address a link would point nowhere.
            raise PermanentJobError("No client app origin is set", code="no_client_app")

        async def fence(session: AsyncSession) -> None:
            await job_queue.lock_fenced(session, job)

        async with context.kept_alive():
            result = await carry_over.carry_over(
                self._database,
                self._sealer,
                self._clock,
                switch_id,
                client_app=self._client_app,
                fence=fence,
            )

        async def done(session: AsyncSession) -> Mapping[str, object]:
            # A noted client is still only in the sheets: they stay until every client is done.
            if result.problems == 0:
                await carry_over.remove_sheets(session, switch_id)
            return {"carried": result.carried, "problems": result.problems}

        await context.apply(done)
        logger.info("migration_carried_over", carried=result.carried, problems=result.problems)
