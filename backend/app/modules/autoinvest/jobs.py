"""The worker's side of automatic investing. No handler here calls a provider.

The scheduler fires `autoinvest.open_due_runs` every 15 minutes, which opens today's runs and
queues one `autoinvest.request_run` job for each. That job saves the investment and the
mobile money deposit; the payments worker sends the prompt. `autoinvest.settle_runs` records
what became of each payment every few minutes.
"""

from collections.abc import Mapping
from datetime import timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import ClaimedJob, Handler, JobContext, JobHandler, JobRequest, enqueue
from app.modules.autoinvest import requests, runs
from app.shared.business_day import business_date
from app.shared.clock import Clock

OPEN_DUE_KIND = "autoinvest.open_due_runs"
REQUEST_KIND = "autoinvest.request_run"
SETTLE_KIND = "autoinvest.settle_runs"
# A request waits no longer than its day. One that could not run by then is missed.
REQUEST_MAX_AGE = timedelta(hours=12)
logger = structlog.get_logger()


class AutoinvestJobs:
    """`provider` is the collections provider's name, or None where mobile money is turned off.

    Without a provider no run is asked for: the scheduler then fires none of these jobs.
    """

    def __init__(self, database: Database, clock: Clock, provider: str | None = None) -> None:
        self._database = database
        self._clock = clock
        self._provider = provider

    def handlers(self) -> dict[str, JobHandler | Handler]:
        kinds: dict[str, JobHandler | Handler] = {
            OPEN_DUE_KIND: self.open_due_runs,
            SETTLE_KIND: self.settle_runs,
        }
        if self._provider is not None:
            kinds[REQUEST_KIND] = self.request_run
        return kinds

    async def open_due_runs(self, job: ClaimedJob, context: JobContext) -> None:
        async def effect(session: AsyncSession) -> Mapping[str, object]:
            opened = await runs.open_due_runs(session, self._clock)
            now = self._clock.now()
            day = business_date(now).isoformat()
            for run_id in opened.due:
                # Keyed by the day too: a retry is the same run on a later day.
                await enqueue(
                    session,
                    JobRequest(
                        kind=REQUEST_KIND,
                        idempotency_key=f"{run_id}:{day}",
                        payload={"run_id": run_id},
                        max_age=REQUEST_MAX_AGE,
                    ),
                    now=now,
                )
            return {"due": len(opened.due), "missed": opened.missed}

        await context.apply(effect)

    async def request_run(self, job: ClaimedJob, context: JobContext) -> None:
        provider = self._provider
        assert provider is not None
        run_id = str(job.payload["run_id"])

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            outcome = await requests.request_run(
                session, run_id, provider=provider, clock=self._clock
            )
            return {"outcome": outcome}

        await context.apply(effect)

    async def settle_runs(self, job: ClaimedJob, context: JobContext) -> None:
        async def effect(session: AsyncSession) -> Mapping[str, object]:
            settled = await requests.settle_runs(session, self._clock)
            return {"paid": settled.paid, "failed": settled.failed, "missed": settled.missed}

        await context.apply(effect)
