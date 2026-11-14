"""The worker's side of company and group accounts: telling a client they were asked to sign.

A client's own transaction can write only that client's alerts, so the invitation is told here.
Nothing here calls a provider. A job run twice tells the person once.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import ClaimedJob, Handler, JobContext, JobHandler, PermanentJobError
from app.modules.organisations import service
from app.shared.clock import Clock


class OrganisationJobs:
    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {service.TELL_INVITED_KIND: self.tell_invited}

    async def tell_invited(self, job: ClaimedJob, context: JobContext) -> None:
        payload = dict(job.payload)

        async def effect(session: AsyncSession) -> dict[str, object]:
            try:
                told = await service.tell_invited(session, payload, self._clock)
            except ValueError:
                raise PermanentJobError(code="invitation_missing") from None
            return {"told": told}

        await context.apply(effect)
