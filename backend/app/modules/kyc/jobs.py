"""The worker's side of identity checks: send a submission to the provider, read its result,
and sweep up what the provider never answered.

The provider is called with no transaction open. A send that may or may not have arrived is
never sent again on a guess: the provider is asked what it holds first.
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
    JobRequest,
    PermanentJobError,
    RetryableJobError,
    UncertainOutcomeError,
    enqueue,
    external_effect,
)
from app.infrastructure.sealing import Sealer
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.provider import (
    Accepted,
    IdentityProvider,
    Pending,
    ProviderDeferred,
    ProviderUncertain,
    Refused,
)
from app.shared.actors import SYSTEM
from app.shared.clock import Clock

READ_ATTEMPTS = 12


@dataclass(frozen=True)
class ProviderJobs:
    database: Database
    sealer: Sealer
    provider: IdentityProvider
    clock: Clock
    # The owner's decision (docs/decisions.md). Off: a person approves every client.
    automatic_approval: bool = False

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            kyc.SEND_CHECK: external_effect(self.send_check),
            # Reading a result changes nothing at the provider, so it is simply tried again.
            kyc.READ_RESULT: self.read_result,
        }

    async def send_check(self, job: ClaimedJob, context: JobContext) -> None:
        check_id, submission = _target(job)
        async with self.database.actor_transaction(SYSTEM) as session:
            request = await kyc.provider_request(session, self.sealer, check_id, submission)
        if request is None:
            return  # the check moved on before the job ran
        answer: Accepted | Refused | None
        try:
            answer = await self.provider.submit(request)
        except ProviderDeferred:
            # Never chain the cause: a provider error can quote the client's details.
            raise RetryableJobError(code="provider_deferred") from None
        except ProviderUncertain:
            answer = await self._held(request.idempotency_key)

        async def accepted(session: AsyncSession) -> Mapping[str, object]:
            assert isinstance(answer, Accepted)
            noted = await kyc.note_provider_reference(
                session, check_id, submission, answer.reference, self.clock
            )
            if noted:
                await enqueue(
                    session,
                    JobRequest(
                        kind=kyc.READ_RESULT,
                        idempotency_key=job.idempotency_key,
                        payload={"check_id": check_id, "submission": submission},
                        max_attempts=READ_ATTEMPTS,
                        max_age=kyc.READ_WITHIN,
                    ),
                    now=self.clock.now(),
                )
            return {"outcome": "accepted" if noted else "moved_on"}

        async def to_person(session: AsyncSession) -> Mapping[str, object]:
            flag = "provider_refused" if isinstance(answer, Refused) else "provider_unreachable"
            await kyc.give_to_person(session, check_id, submission, flag, self.clock)
            return {"outcome": flag}

        await context.apply(accepted if isinstance(answer, Accepted) else to_person)

    async def _held(self, idempotency_key: str) -> Accepted | None:
        """After an uncertain send: what the provider says it holds. None goes to a person."""
        try:
            reference = await self.provider.find(idempotency_key)
        except (ProviderDeferred, ProviderUncertain):
            # Still unknown. The job stops here and the sweep gives the check to a person.
            raise UncertainOutcomeError from None
        return None if reference is None else Accepted(reference)

    async def read_result(self, job: ClaimedJob, context: JobContext) -> None:
        check_id, submission = _target(job)
        async with self.database.actor_transaction(SYSTEM) as session:
            reference = await kyc.awaited_reference(session, check_id, submission)
        if reference is None:
            return
        try:
            answer = await self.provider.result(reference)
        except (ProviderDeferred, ProviderUncertain):
            raise RetryableJobError(code="provider_unavailable") from None
        if isinstance(answer, Pending):
            raise RetryableJobError(code="provider_pending")
        outcomes = answer.outcomes

        async def record(session: AsyncSession) -> Mapping[str, object]:
            view = await kyc.record_provider_result(
                session,
                self.sealer,
                check_id,
                submission,
                reference,
                outcomes,
                self.clock,
                automatic_approval=self.automatic_approval,
            )
            return {"status": view.status.value}

        await context.apply(record)


def sweep_unanswered(clock: Clock) -> JobHandler:
    """Runs whatever the provider is, so a check is never left waiting on a job that was lost."""

    async def handler(job: ClaimedJob, context: JobContext) -> None:
        async def sweep(session: AsyncSession) -> Mapping[str, object]:
            return {"moved": await kyc.give_unanswered_to_people(session, clock)}

        await context.apply(sweep)

    return handler


def _target(job: ClaimedJob) -> tuple[str, int]:
    check_id, submission = job.payload.get("check_id"), job.payload.get("submission")
    if not isinstance(check_id, str) or type(submission) is not int:
        raise PermanentJobError(code="invalid_payload")
    return check_id, submission
