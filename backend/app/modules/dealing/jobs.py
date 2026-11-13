"""The worker's side of the dealing desk: record the bills the issuer has paid at maturity.

No handler here calls a provider, so each does all its work in the fenced transaction that also
completes the job. A run repeated, or two at once, records each maturity once.
"""

from collections.abc import Mapping

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import ClaimedJob, Handler, JobContext, JobHandler, PermanentJobError
from app.modules.dealing import maturities
from app.modules.ledger.domain.errors import DayClosed
from app.shared.clock import Clock

MATURITIES_KIND = "dealing.record_maturities"
logger = structlog.get_logger()


class DealingJobs:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {MATURITIES_KIND: self.record_maturities}

    async def record_maturities(self, job: ClaimedJob, context: JobContext) -> None:
        """Post every bill due by today. A maturity in a closed day fails the run for good, so
        staff see it among the failed jobs, and the next run tries again."""

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            try:
                return {"recorded": await maturities.record_maturities(session, self._clock)}
            except DayClosed as error:
                raise PermanentJobError(code="dealing_maturity_day_closed") from error

        result = await context.apply(effect)
        logger.info("dealing_maturities_recorded", count=result.get("recorded"))
