"""The worker's side of bonds: start a holding when its payment is confirmed, cancel the ones
nobody paid for, pay coupons on their dates, and repay holdings when their bonds mature.

No handler here calls a provider. Each does all its work in the single fenced transaction that
also completes the job, so a job that is run twice changes nothing the second time.
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
    RetryableJobError,
)
from app.infrastructure.jobs import queue as job_queue
from app.modules.bonds import coupons, holdings, maturity
from app.modules.bonds.events import START_KIND
from app.shared.actors import SYSTEM
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError

CANCEL_UNPAID_KIND = "bonds.cancel_unpaid"
PAY_COUPONS_KIND = "bonds.pay_coupons"
REPAY_MATURED_KIND = "bonds.repay_matured"
logger = structlog.get_logger()


class BondJobs:
    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            START_KIND: self.start_from_deposit,
            CANCEL_UNPAID_KIND: self.cancel_unpaid,
            PAY_COUPONS_KIND: self.pay_coupons,
            REPAY_MATURED_KIND: self.repay_matured,
        }

    async def start_from_deposit(self, job: ClaimedJob, context: JobContext) -> None:
        """A deposit was confirmed. If it paid for a bond holding, start that holding.

        A refusal fails the job for good, and staff see it among the failed jobs: the money is
        in the client's awaiting-start account and is not moved on a guess.
        """
        data = job.payload.get("data")
        data = data if isinstance(data, Mapping) else {}
        purpose_type, holding_id = data.get("purpose_type"), data.get("purpose_id")
        if purpose_type != "bond":
            # A payment for an investment. Not ours.
            await context.apply(_nothing)
            return
        if not isinstance(holding_id, str):
            raise PermanentJobError(code="holding_id_missing")
        confirmed_on = _confirmed_on(data.get("confirmed_on"), self._clock)

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            try:
                holding = await holdings.start_holding(
                    session, holding_id, confirmed_on=confirmed_on, clock=self._clock
                )
            except ApplicationError as error:
                raise PermanentJobError(code=f"bond_holding_{error.code}") from error
            return {"holding_id": holding.id, "status": holding.status.value}

        await context.apply(effect)

    async def cancel_unpaid(self, job: ClaimedJob, context: JobContext) -> None:
        async def effect(session: AsyncSession) -> Mapping[str, object]:
            return {"cancelled": await holdings.cancel_unpaid(session, self._clock)}

        result = await context.apply(effect)
        logger.info("unpaid_bond_holdings_cancelled", count=result.get("cancelled"))

    async def pay_coupons(self, job: ClaimedJob, context: JobContext) -> None:
        """Pay every holding every coupon it is owed dated on or before the business day this
        run is for, so a run also makes up any an earlier one missed.

        The day comes from the time the run was scheduled for, so a run that is late or retried
        still pays its own day's coupons. Holdings are paid a batch at a time, each batch in a
        transaction only the current holder of the job can commit, and a holding paid once is
        never paid again.
        """
        run_date = _run_date(job.payload)
        paid = held = failed = 0
        after = ""
        async with context.kept_alive():
            while True:
                async with self._database.actor_transaction(SYSTEM) as session:
                    await job_queue.lock_fenced(session, job)
                    try:
                        batch = await coupons.pay_batch(session, run_date, self._clock, after=after)
                    except ApplicationError as error:
                        raise PermanentJobError(code=f"coupon_run_{error.code}") from error
                if batch.last_id is None:
                    break
                after = batch.last_id
                paid, held, failed = paid + batch.paid, held + batch.held, failed + batch.failed

        if failed:
            # The ones that failed stay owed, and the retry pays only what is still owed.
            logger.error("bond_coupon_run_incomplete", run_date=run_date.isoformat(), failed=failed)
            raise RetryableJobError(code="coupon_run_incomplete")

        async def effect(_: AsyncSession) -> Mapping[str, object]:
            return {"run_date": run_date.isoformat(), "paid": paid, "held": held}

        await context.apply(effect)
        logger.info("bond_coupon_run_finished", run_date=run_date.isoformat(), paid=paid, held=held)

    async def repay_matured(self, job: ClaimedJob, context: JobContext) -> None:
        """Repay every active holding of an issue that matured on or before the business day
        this run is for: its face value and last coupon, and first any coupon a run missed.

        Holdings are repaid a batch at a time, each batch in a transaction only the current
        holder of the job can commit, and a holding repaid once is never repaid again.
        """
        run_date = _run_date(job.payload)
        paid = held = failed = 0
        after = ""
        async with context.kept_alive():
            while True:
                async with self._database.actor_transaction(SYSTEM) as session:
                    await job_queue.lock_fenced(session, job)
                    try:
                        batch = await maturity.repay_batch(
                            session, run_date, self._clock, after=after
                        )
                    except ApplicationError as error:
                        raise PermanentJobError(code=f"maturity_run_{error.code}") from error
                if batch.last_id is None:
                    break
                after = batch.last_id
                paid, held, failed = paid + batch.paid, held + batch.held, failed + batch.failed

        if failed:
            logger.error(
                "bond_maturity_run_incomplete", run_date=run_date.isoformat(), failed=failed
            )
            raise RetryableJobError(code="maturity_run_incomplete")

        async def effect(_: AsyncSession) -> Mapping[str, object]:
            return {"run_date": run_date.isoformat(), "paid": paid, "held": held}

        await context.apply(effect)
        logger.info(
            "bond_maturity_run_finished", run_date=run_date.isoformat(), paid=paid, held=held
        )


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
