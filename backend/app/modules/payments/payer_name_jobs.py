"""The worker's side of looking up the name the network holds for a number that paid.

The same shape as the payout number lookup (`payouts/name_lookup_jobs.py`): each handler calls
ioTec with no transaction open, then records what it said in one transaction that also
completes the job.

* The send handler is registered as having an external effect, so it is never run again after
  a crash or a lost lease. The lookup stays `queued`: the payment has counted already, so
  nothing waits on it.
* A send that certainly never arrived is tried again by the job. One that may have arrived is
  never sent again: the lookup is `unanswered`.
* Reading an accepted lookup changes nothing at ioTec, so it is simply read again later.

Logs carry deposit IDs and codes only: never the number or the name.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import (
    ClaimedJob,
    Handler,
    JobContext,
    JobHandler,
    PermanentJobError,
    RetryableJobError,
    external_effect,
)
from app.modules.payments import payer_names
from app.modules.payouts.domain.name_lookups import (
    Accepted,
    Found,
    LookupDeferred,
    LookupStatus,
    NoAnswer,
    NoName,
    NotReceived,
    Pending,
    RegisteredNamesProvider,
)
from app.shared.actors import SYSTEM
from app.shared.clock import Clock


@dataclass(frozen=True)
class PayerNameJobs:
    database: Database
    provider: RegisteredNamesProvider
    clock: Clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            payer_names.SEND_KIND: external_effect(self.send),
            payer_names.READ_KIND: self.read,
        }

    async def send(self, job: ClaimedJob, context: JobContext) -> None:
        deposit_id = _deposit_id(job)
        async with self.database.actor_transaction(SYSTEM) as session:
            number = await payer_names.number_to_look_up(session, deposit_id)
        if number is None:
            return  # it moved on
        answer = await self.provider.send(number, deposit_id)
        if isinstance(answer, NotReceived):
            raise RetryableJobError(code="lookup_not_received")

        async def record(session: AsyncSession) -> Mapping[str, object]:
            if isinstance(answer, Accepted):
                noted = await payer_names.note_sent(
                    session, deposit_id, answer.request_id, self.clock
                )
                return {"outcome": "sent" if noted else "moved_on"}
            await payer_names.note_unanswered(session, deposit_id, answer.code, self.clock)
            return {"outcome": answer.code}

        await context.apply(record)

    async def read(self, job: ClaimedJob, context: JobContext) -> None:
        deposit_id = _deposit_id(job)
        async with self.database.actor_transaction(SYSTEM) as session:
            lookup = await payer_names.payer_name(session, deposit_id)
        if lookup is None or lookup.request_id is None or lookup.status is not LookupStatus.SENT:
            return
        try:
            answer = await self.provider.read(lookup.request_id)
        except LookupDeferred:
            raise RetryableJobError(code="lookup_unavailable") from None
        if isinstance(answer, Pending):
            raise RetryableJobError(code="lookup_pending")

        async def record(session: AsyncSession) -> Mapping[str, object]:
            if isinstance(answer, NoAnswer):
                await payer_names.note_unanswered(session, deposit_id, answer.code, self.clock)
                return {"outcome": answer.code}
            assert isinstance(answer, Found | NoName)
            outcome = await payer_names.record_answer(session, deposit_id, answer, self.clock)
            return {"outcome": outcome}

        await context.apply(record)


def _deposit_id(job: ClaimedJob) -> str:
    deposit_id = job.payload.get("deposit_id")
    if not isinstance(deposit_id, str):
        raise PermanentJobError(code="invalid_payload")
    return deposit_id
