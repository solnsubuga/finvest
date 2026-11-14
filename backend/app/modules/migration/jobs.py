"""The worker's side of the switch: carry every client of an approved switch over.

No handler here calls a provider. The carry-over works in one transaction per client, each
checking the job's lease first, and completes the job at the end. The sealed sheets are removed
then only if every client was carried over or closed: a client still noted is only in them.
Approving a retry or a close queues the job again.
A run that stops part way is run again and carries on from the first client not yet done.

It also hears the identity checks module's decisions. When a migrated client's claim check or
full ID check is decided, their claim moves on: a match confirms who they are, anything else
sends them back to the identity step. Any other client's decision changes nothing here.
"""

from collections.abc import Mapping

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import ClaimedJob, Handler, JobContext, JobHandler, PermanentJobError
from app.infrastructure.jobs import queue as job_queue
from app.infrastructure.sealing import Sealer
from app.modules.migration import carry_over, claims, switch
from app.modules.migration.domain.claim import ClaimStatus
from app.shared.actors import SYSTEM
from app.shared.clock import Clock

logger = structlog.get_logger()

# The consumers the identity checks module names for its two decisions (app/modules/kyc).
CLAIM_CHECK_DECIDED = "migration.claim_check_decided"
IDENTITY_CHECK_DECIDED = "migration.identity_check_decided"


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
        return {
            switch.CARRY_OVER: self.carry_over,
            CLAIM_CHECK_DECIDED: self.claim_check_decided,
            IDENTITY_CHECK_DECIDED: self.identity_check_decided,
        }

    async def claim_check_decided(self, job: ClaimedJob, context: JobContext) -> None:
        """The selfie and ID photo were compared with the identity carried over."""
        await self._identity_decided(job, context, "matched")

    async def identity_check_decided(self, job: ClaimedJob, context: JobContext) -> None:
        """A full ID check was decided. Most belong to clients who never used Finvest 1.0."""
        await self._identity_decided(job, context, "approved")

    async def _identity_decided(self, job: ClaimedJob, context: JobContext, field: str) -> None:
        data = job.payload.get("data")
        data = data if isinstance(data, Mapping) else {}
        client_id, matched = data.get("client_id"), data.get(field)
        if not isinstance(client_id, str) or not isinstance(matched, bool):
            raise PermanentJobError("The event names no client or outcome", code="invalid_payload")

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            claim = await claims.claim_for(session, client_id)
            # Only a claim waiting for this answer moves. A check decided before the client
            # handed in their identity step, or for anyone else, changes nothing.
            if claim is None or claim.status is not ClaimStatus.IDENTITY_SUBMITTED:
                return {"moved": False}
            view = await claims.record_identity_result(
                session, SYSTEM, self._clock, client_id, matched=matched
            )
            return {"moved": True, "status": view.status.value}

        await context.apply(effect)

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
            return {"carried": result.carried, "problems": result.problems, "closed": result.closed}

        await context.apply(done)
        logger.info(
            "migration_carried_over",
            carried=result.carried,
            problems=result.problems,
            closed=result.closed,
        )
