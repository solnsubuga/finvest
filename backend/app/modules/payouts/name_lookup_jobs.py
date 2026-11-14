"""The worker's side of looking up the name a network holds for a payout number.

Each handler calls ioTec with no transaction open, then records what it said in one transaction
that also completes the job. A lookup is sent at most once:

* The send handler is registered as having an external effect, so the queue never runs it again
  after a crash or a lost lease. The lookup then stays `queued`, and after an hour the client may
  ask for a new one.
* A send that certainly never arrived is tried again by the job (`RetryableJobError`). One that
  may have arrived is never sent again: ioTec cannot look it up by our reference, so the lookup
  is `unanswered` and the client may ask again, up to the limit.
* Reading an accepted lookup changes nothing at ioTec, so it is simply read again later.

Logs carry lookup IDs and codes only: never the number or the name.
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
from app.modules.payouts import name_lookups
from app.modules.payouts.domain.name_lookups import (
    Accepted,
    Found,
    LookupDeferred,
    NoAnswer,
    NoName,
    NotReceived,
    Pending,
    RegisteredNamesProvider,
)
from app.shared.actors import SYSTEM
from app.shared.clock import Clock


@dataclass(frozen=True)
class NameLookupJobs:
    database: Database
    provider: RegisteredNamesProvider
    clock: Clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            name_lookups.SEND_KIND: external_effect(self.send),
            name_lookups.READ_KIND: self.read,
            name_lookups.DECIDE_KIND: self.decide,
        }

    async def send(self, job: ClaimedJob, context: JobContext) -> None:
        lookup_id = _lookup_id(job)
        async with self.database.actor_transaction(SYSTEM) as session:
            msisdn = await name_lookups.number_to_look_up(session, lookup_id)
        if msisdn is None:
            return  # the number moved on before the job ran
        answer = await self.provider.send(msisdn, lookup_id)
        if isinstance(answer, NotReceived):
            # Certainly never arrived: the job tries again later, and the lookup stays queued.
            raise RetryableJobError(code="lookup_not_received")

        async def record(session: AsyncSession) -> Mapping[str, object]:
            if isinstance(answer, Accepted):
                noted = await name_lookups.note_sent(
                    session, lookup_id, answer.request_id, self.clock
                )
                return {"outcome": "sent" if noted else "moved_on"}
            await name_lookups.note_unanswered(session, lookup_id, answer.code, self.clock)
            return {"outcome": answer.code}

        await context.apply(record)

    async def read(self, job: ClaimedJob, context: JobContext) -> None:
        lookup_id = _lookup_id(job)
        async with self.database.actor_transaction(SYSTEM) as session:
            lookup = await name_lookups.get_lookup(session, lookup_id)
        if lookup.request_id is None or lookup.status.value != "sent":
            return
        try:
            answer = await self.provider.read(lookup.request_id)
        except LookupDeferred:
            raise RetryableJobError(code="lookup_unavailable") from None
        if isinstance(answer, Pending):
            raise RetryableJobError(code="lookup_pending")

        async def record(session: AsyncSession) -> Mapping[str, object]:
            if isinstance(answer, NoAnswer):
                await name_lookups.note_unanswered(session, lookup_id, answer.code, self.clock)
                return {"outcome": answer.code}
            assert isinstance(answer, Found | NoName)
            outcome = await name_lookups.record_answer(
                session, SYSTEM, lookup_id, answer, self.clock
            )
            return {"outcome": outcome}

        await context.apply(record)

    async def decide(self, job: ClaimedJob, context: JobContext) -> None:
        lookup_id = _lookup_id(job)

        async def record(session: AsyncSession) -> Mapping[str, object]:
            outcome = await name_lookups.decide_lookup(session, SYSTEM, lookup_id, self.clock)
            return {"outcome": outcome}

        await context.apply(record)


def _lookup_id(job: ClaimedJob) -> str:
    lookup_id = job.payload.get("lookup_id")
    if not isinstance(lookup_id, str):
        raise PermanentJobError(code="invalid_payload")
    return lookup_id
