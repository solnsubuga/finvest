"""The worker's side of the dealing desk: record the bills the issuer has paid at maturity,
fund early withdrawals from the buffer, and allocate bills to investments.

No handler here calls a provider, so each does all its work in the fenced transaction that also
completes the job. A run repeated, or two at once, records each maturity and each funding once.
"""

from collections.abc import Mapping

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import ClaimedJob, Handler, JobContext, JobHandler, PermanentJobError
from app.modules.dealing import allocations, maturities, withdrawals
from app.modules.dealing.events import FUND_WITHDRAWAL_KIND
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money, MoneyError

MATURITIES_KIND = "dealing.record_maturities"
RELEASE_KIND = "dealing.release_withdrawal_queue"
ALLOCATE_KIND = "dealing.allocate"
logger = structlog.get_logger()


class DealingJobs:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            MATURITIES_KIND: self.record_maturities,
            FUND_WITHDRAWAL_KIND: self.fund_early_withdrawal,
            RELEASE_KIND: self.release_queue,
            ALLOCATE_KIND: self.allocate,
        }

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

    async def fund_early_withdrawal(self, job: ClaimedJob, context: JobContext) -> None:
        """Record how a withdrawal investing handed over is funded. With no buffer rules in
        force the job fails for good, so staff see it, rather than guessing."""
        investment_id, amount = _withdrawal(job.payload)

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            try:
                funding = await withdrawals.fund_early_withdrawal(
                    session, investment_id, amount, self._clock
                )
            except ApplicationError as error:
                raise PermanentJobError(code=f"dealing_funding_{error.code}") from error
            return {"funding_id": funding.id, "status": funding.status.value}

        result = await context.apply(effect)
        logger.info("dealing_withdrawal_funding_recorded", status=result.get("status"))

    async def release_queue(self, job: ClaimedJob, context: JobContext) -> None:
        """Fund the queued withdrawals the buffer can now pay."""

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            try:
                return {"released": await withdrawals.release_queue(session, self._clock)}
            except ApplicationError as error:
                raise PermanentJobError(code=f"dealing_release_{error.code}") from error

        result = await context.apply(effect)
        logger.info("dealing_withdrawal_queue_released", count=result.get("released"))

    async def allocate(self, job: ClaimedJob, context: JobContext) -> None:
        """Bring the allocations of bills to investments up to date."""

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            run = await allocations.allocate_all(session, self._clock)
            return {"allocated": run.allocated, "released": run.released}

        result = await context.apply(effect)
        logger.info(
            "dealing_allocations_updated",
            allocated=result.get("allocated"),
            released=result.get("released"),
        )


def _withdrawal(payload: Mapping[str, object]) -> tuple[str, Money]:
    data = payload.get("data")
    data = data if isinstance(data, Mapping) else {}
    investment_id, currency, amount = (
        data.get("investment_id"),
        data.get("currency"),
        data.get("amount_minor"),
    )
    if not isinstance(investment_id, str) or not investment_id:
        raise PermanentJobError(code="investment_id_missing")
    try:
        if not isinstance(currency, str) or type(amount) is not int:
            raise ValueError
        return investment_id, Money(amount, Currency(currency))
    except (ValueError, MoneyError) as error:
        raise PermanentJobError(code="amount_invalid") from error
