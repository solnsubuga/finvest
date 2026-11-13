"""The worker's side of the dealing desk: record the bills the issuer has paid at maturity.

No handler here calls a provider, so each does all its work in the fenced transaction that also
completes the job. A run repeated, or two at once, records each maturity once.
"""

from collections.abc import Mapping

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import ClaimedJob, Handler, JobContext, JobHandler
from app.modules.dealing import maturities
from app.shared.clock import Clock

MATURITIES_KIND = "dealing.record_maturities"
logger = structlog.get_logger()


class DealingJobs:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {MATURITIES_KIND: self.record_maturities}

    async def record_maturities(self, job: ClaimedJob, context: JobContext) -> None:
        """Post every bill due by today. A lot whose maturity date is in a closed day is left
        out and logged as an error for staff, and the others are still posted. The next run
        tries it again."""

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            run = await maturities.record_maturities(session, self._clock)
            return {"recorded": run.recorded, "stuck": list(run.stuck)}

        result = await context.apply(effect)
        stuck = result.get("stuck")
        for lot_id in stuck if isinstance(stuck, list) else []:
            logger.error(
                "dealing_maturity_stuck", lot_id=lot_id, error_code="dealing_maturity_day_closed"
            )
        logger.info("dealing_maturities_recorded", count=result.get("recorded"))
