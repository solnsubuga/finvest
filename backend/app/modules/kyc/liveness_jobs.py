"""The worker's side of liveness: open a session at the provider, then read it until it answers.

The provider is called with no transaction open. A start that may or may not have arrived is
not sent again: the attempt ends as not started, and the client starts a new one, which is a
new session with a new key. Reading changes nothing at the provider, so a read is simply tried
again, every `POLL_EVERY` while the attempt is open.
"""

from collections.abc import Mapping
from dataclasses import dataclass

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
    external_effect,
)
from app.infrastructure.sealing import Sealer
from app.infrastructure.storage import FileStorage, NewFile, StorageError
from app.modules.kyc import liveness, service
from app.modules.kyc.domain.liveness import (
    Live,
    LivenessProvider,
    LivenessStatus,
    NotLive,
    NotStarted,
    Started,
    StillOpen,
)
from app.modules.kyc.domain.provider import ProviderDeferred, ProviderUncertain
from app.shared.actors import SYSTEM
from app.shared.clock import Clock
from app.shared.push_ids import push_id

logger = structlog.get_logger()


@dataclass(frozen=True)
class LivenessJobs:
    database: Database
    sealer: Sealer
    provider: LivenessProvider
    storage: FileStorage
    clock: Clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            liveness.START_LIVENESS: external_effect(self.start),
            liveness.READ_LIVENESS: self.read,
        }

    async def start(self, job: ClaimedJob, context: JobContext) -> None:
        attempt_id = _attempt(job)
        async with self.database.actor_transaction(SYSTEM) as session:
            waiting = await liveness.to_start(session, attempt_id, self.clock)
        if not waiting:
            return
        answer: Started | NotStarted
        try:
            answer = await self.provider.start(attempt_id)
        except ProviderDeferred:
            raise RetryableJobError(code="provider_deferred") from None
        except ProviderUncertain:
            # The provider cannot be asked what it holds, and the client is waiting: end this
            # attempt, and the client starts another.
            answer = NotStarted("provider_uncertain")

        async def record(session: AsyncSession) -> Mapping[str, object]:
            if isinstance(answer, Started):
                opened = await liveness.note_open(
                    session, self.sealer, attempt_id, answer.request_id, answer.url, self.clock
                )
                return {"outcome": "open" if opened else "moved_on"}
            await liveness.finish(
                session, attempt_id, LivenessStatus.NOT_STARTED, answer.code, self.clock
            )
            return {"outcome": answer.code}

        await context.apply(record)

    async def read(self, job: ClaimedJob, context: JobContext) -> None:
        attempt_id = _attempt(job)
        read = job.payload.get("read")
        if type(read) is not int:
            raise PermanentJobError(code="invalid_payload")
        async with self.database.actor_transaction(SYSTEM) as session:
            request_id = await liveness.to_read(session, attempt_id, self.clock)
        if request_id is None:
            return
        try:
            answer = await self.provider.result(request_id)
        except (ProviderDeferred, ProviderUncertain):
            raise RetryableJobError(code="provider_unavailable") from None

        if isinstance(answer, StillOpen):

            async def again(session: AsyncSession) -> Mapping[str, object]:
                await liveness.enqueue_read(
                    session, attempt_id, read + 1, self.clock.now(), delay=liveness.POLL_EVERY
                )
                return {"outcome": "still_open"}

            await context.apply(again)
            return
        if isinstance(answer, NotLive):
            code = answer.code

            async def not_live(session: AsyncSession) -> Mapping[str, object]:
                await liveness.finish(session, attempt_id, LivenessStatus.FAILED, code, self.clock)
                return {"outcome": code}

            await context.apply(not_live)
            return
        await self._live(attempt_id, answer, context)

    async def _live(self, attempt_id: str, answer: Live, context: JobContext) -> None:
        async with self.database.actor_transaction(SYSTEM) as session:
            check_id = await liveness.check_of(session, attempt_id)
        photo_id = push_id()
        key = service.photo_key(check_id, photo_id)
        try:
            stored = await self.storage.put(NewFile(key, answer.photo, answer.content_type))
        except StorageError:
            # Nothing was saved. The provider still holds the answer, so read it again.
            raise RetryableJobError(code="storage_unavailable") from None
        used = False

        async def record(session: AsyncSession) -> Mapping[str, object]:
            nonlocal used
            used = await liveness.record_live(session, attempt_id, photo_id, stored, self.clock)
            return {"outcome": "passed" if used else "passed_unused"}

        try:
            await context.apply(record)
        except Exception:
            await self._discard(key, photo_id)
            raise
        if not used:
            await self._discard(key, photo_id)

    async def _discard(self, key: str, photo_id: str) -> None:
        try:
            await self.storage.delete(key)
        except StorageError:
            logger.warning("identity_photo_orphaned", photo_id=photo_id)


def _attempt(job: ClaimedJob) -> str:
    attempt_id = job.payload.get("attempt_id")
    if not isinstance(attempt_id, str):
        raise PermanentJobError(code="invalid_payload")
    return attempt_id
