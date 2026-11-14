"""The worker's side of transaction monitoring: one job per confirmed deposit and per payout.

A deposit arrives as `payments.deposit_confirmed`, and a payout as a job payouts enqueued when
it was created. Each carries only an ID: the amount and the time are read from the module that
owns them, so a job never carries amounts tied to a person.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import ClaimedJob, Handler, JobContext, JobHandler, PermanentJobError
from app.modules.payments import service as payments
from app.modules.payments.domain.deposits import DepositStatus
from app.modules.payouts import payouts
from app.modules.screening import monitoring
from app.modules.screening.domain.monitoring import Kind, Transaction
from app.shared.actors import SYSTEM
from app.shared.clock import Clock
from app.shared.errors import ApplicationError


@dataclass(frozen=True)
class MonitoringJobs:
    database: Database
    clock: Clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            monitoring.MONITOR_DEPOSIT_KIND: self.monitor_deposit,
            monitoring.MONITOR_PAYOUT_KIND: self.monitor_payout,
        }

    async def monitor_deposit(self, job: ClaimedJob, context: JobContext) -> None:
        data = job.payload.get("data")
        deposit_id = data.get("deposit_id") if isinstance(data, Mapping) else None
        if not isinstance(deposit_id, str):
            raise PermanentJobError(code="invalid_payload")

        async with self.database.actor_transaction(SYSTEM) as session:
            deposit = await payments.find_deposit(session, deposit_id)
        if deposit is None or deposit.status is not DepositStatus.CONFIRMED:
            raise PermanentJobError(code="deposit_not_confirmed")
        transaction = Transaction(
            deposit.id,
            Kind.DEPOSIT,
            deposit.amount.minor,
            deposit.amount.currency,
            deposit.settled_at or deposit.updated_at,
        )
        await context.apply(self._record(deposit.client_id, transaction))

    async def monitor_payout(self, job: ClaimedJob, context: JobContext) -> None:
        payout_id = job.payload.get("payout_id")
        if not isinstance(payout_id, str):
            raise PermanentJobError(code="invalid_payload")

        try:
            async with self.database.actor_transaction(SYSTEM) as session:
                payout = await payouts.get_payout(session, payout_id)
        except ApplicationError:
            raise PermanentJobError(code="payout_not_found") from None
        transaction = Transaction(
            payout.id, Kind.PAYOUT, payout.amount.minor, payout.amount.currency, payout.created_at
        )
        await context.apply(self._record(payout.client_id, transaction))

    def _record(
        self, client_id: str, transaction: Transaction
    ) -> Callable[[AsyncSession], Awaitable[Mapping[str, object]]]:
        async def record(session: AsyncSession) -> Mapping[str, object]:
            opened = await monitoring.record(
                session, client_id=client_id, transaction=transaction, clock=self.clock
            )
            return {"recorded": opened is not None, "cases_opened": len(opened or [])}

        return record
