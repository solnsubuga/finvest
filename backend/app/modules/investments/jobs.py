"""The worker's side of investing: start an investment when its payment is confirmed, cancel
the ones nobody paid for, and pay each day's interest.

No handler here calls a provider. Starting and cancelling do all their work in the single
fenced transaction that also completes the job. The daily interest run is long, so it works in
short fenced transactions of its own and completes the job at the end. A job that is run twice
changes nothing the second time.
"""

from collections.abc import Mapping
from datetime import date, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import (
    ClaimedJob,
    Handler,
    JobContext,
    JobHandler,
    PermanentJobError,
)
from app.infrastructure.jobs import queue as job_queue
from app.modules.investments import accrual, holdings, maturity
from app.modules.investments.events import START_KIND
from app.shared.actors import SYSTEM
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError

CANCEL_UNPAID_KIND = "investments.cancel_unpaid"
ACCRUE_KIND = "investments.accrue_interest"
MATURE_KIND = "investments.mature"
logger = structlog.get_logger()


class InvestmentJobs:
    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            START_KIND: self.start_from_deposit,
            CANCEL_UNPAID_KIND: self.cancel_unpaid,
            ACCRUE_KIND: self.accrue_interest,
            MATURE_KIND: self.mature,
        }

    async def start_from_deposit(self, job: ClaimedJob, context: JobContext) -> None:
        """A deposit was confirmed. If it paid for an investment, start that investment.

        A refusal fails the job for good, and staff see it among the failed jobs: the money
        is in the client's awaiting-start account and is not moved on a guess.
        """
        data = job.payload.get("data")
        data = data if isinstance(data, Mapping) else {}
        purpose_type, investment_id = data.get("purpose_type"), data.get("purpose_id")
        if purpose_type != "investment":
            # A payment for a goal. Not ours.
            await context.apply(_nothing)
            return
        if not isinstance(investment_id, str):
            raise PermanentJobError(code="investment_id_missing")
        confirmed_on = _confirmed_on(data.get("confirmed_on"), self._clock)

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            try:
                investment = await holdings.start_investment(
                    session, investment_id, confirmed_on=confirmed_on, clock=self._clock
                )
            except ApplicationError as error:
                raise PermanentJobError(code=f"investment_{error.code}") from error
            return {"investment_id": investment.id, "status": investment.status.value}

        await context.apply(effect)

    async def cancel_unpaid(self, job: ClaimedJob, context: JobContext) -> None:
        async def effect(session: AsyncSession) -> Mapping[str, object]:
            return {"cancelled": await holdings.cancel_unpaid(session, self._clock)}

        result = await context.apply(effect)
        logger.info("unpaid_investments_cancelled", count=result.get("cancelled"))

    async def accrue_interest(self, job: ClaimedJob, context: JobContext) -> None:
        """Pay every running investment its interest for the business day this run is for.

        The day comes from the time the run was scheduled for, not from when it happens to
        execute, so a run that is late or retried still pays for its own day. Investments are
        paid a batch at a time, each batch in a transaction only the current holder of the job
        can commit. A worker that dies part way leaves the paid batches paid, and the retry
        carries on with the rest.
        """
        run_date = _run_date(job.payload)
        posted = failed = 0
        after = ""
        async with context.kept_alive():
            while True:
                async with self._database.actor_transaction(SYSTEM) as session:
                    await job_queue.lock_fenced(session, job)
                    try:
                        batch = await accrual.accrue_batch(
                            session, run_date, self._clock, after=after
                        )
                    except ApplicationError as error:
                        raise PermanentJobError(code=f"interest_run_{error.code}") from error
                if batch.last_id is None:
                    break
                after = batch.last_id
                posted += batch.posted
                failed += batch.failed

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            behind = await accrual.finish_run(session, run_date, self._clock)
            return {"run_date": run_date.isoformat(), "behind": sum(behind.values())}

        result = await context.apply(effect)
        log = logger.error if failed or result.get("behind") else logger.info
        log(
            "interest_run_finished",
            run_date=run_date.isoformat(),
            posted=posted,
            failed=failed,
            behind=result.get("behind"),
        )

    async def mature(self, job: ClaimedJob, context: JobContext) -> None:
        """Close every investment whose term ended on or before the day this run is for.

        Like the interest run, it works a batch at a time in transactions only the current
        holder of the job can commit, so a retry carries on where a dead worker stopped.
        """
        run_date = _run_date(job.payload)
        matured = failed = 0
        after = ""
        async with context.kept_alive():
            while True:
                async with self._database.actor_transaction(SYSTEM) as session:
                    await job_queue.lock_fenced(session, job)
                    try:
                        batch = await maturity.mature_batch(
                            session, run_date, self._clock, after=after
                        )
                    except ApplicationError as error:
                        raise PermanentJobError(code=f"maturity_run_{error.code}") from error
                if batch.last_id is None:
                    break
                after = batch.last_id
                matured += batch.matured
                failed += batch.failed

        async def effect(_: AsyncSession) -> Mapping[str, object]:
            return {"run_date": run_date.isoformat(), "matured": matured, "failed": failed}

        await context.apply(effect)
        log = logger.error if failed else logger.info
        log("maturity_run_finished", run_date=run_date.isoformat(), matured=matured, failed=failed)


async def _nothing(_: AsyncSession) -> Mapping[str, object]:
    return {"status": "ignored"}


def _confirmed_on(value: object, clock: Clock) -> date:
    """The business day the payment was confirmed, as the payments module reported it."""
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise PermanentJobError(code="confirmed_on_invalid") from error
    if value is not None:
        raise PermanentJobError(code="confirmed_on_invalid")
    return business_date(clock.now())


def _run_date(payload: Mapping[str, object]) -> date:
    """The business day a scheduled run is for: the Kampala day of its fire time."""
    scheduled_for = payload.get("scheduled_for")
    if not isinstance(scheduled_for, str):
        raise PermanentJobError(code="scheduled_for_missing")
    try:
        return business_date(datetime.fromisoformat(scheduled_for))
    except ValueError as error:
        raise PermanentJobError(code="scheduled_for_invalid") from error
