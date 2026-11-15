"""The worker's side of bonds: start a holding when its payment is confirmed, and cancel the
ones nobody paid for.

No handler here calls a provider. Each does all its work in the single fenced transaction that
also completes the job, so a job that is run twice changes nothing the second time.
"""

from collections.abc import Mapping
from datetime import date

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import ClaimedJob, Handler, JobContext, JobHandler, PermanentJobError
from app.modules.bonds import holdings
from app.modules.bonds.events import START_KIND
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError

CANCEL_UNPAID_KIND = "bonds.cancel_unpaid"
logger = structlog.get_logger()


class BondJobs:
    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {START_KIND: self.start_from_deposit, CANCEL_UNPAID_KIND: self.cancel_unpaid}

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
