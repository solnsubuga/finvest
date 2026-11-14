"""The worker's side of face confirmations: liveness page, live capture, then the face match.

Every provider is called with no transaction open. A start or a face match request that may or
may not have arrived is not sent again, because ioTec cannot be asked about it by our
reference: the attempt ends, and the client starts a new one, which is a new request with a new
key. Reading changes nothing at a provider, so a read is simply tried again, every
`POLL_EVERY` while the attempt is waiting. See docs/identity-checks.md#face-confirmations.
"""

from collections.abc import Awaitable, Callable, Mapping
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
from app.modules.kyc import face_confirmations as confirmations
from app.modules.kyc.domain.face_confirmations import (
    ConfirmationStatus,
    FaceMatcher,
    MatchAnswer,
    MatchNotRun,
    MatchPending,
    MatchRefused,
    MatchSent,
)
from app.modules.kyc.domain.liveness import (
    Live,
    LivenessProvider,
    NotLive,
    NotStarted,
    Started,
    StillOpen,
)
from app.modules.kyc.domain.provider import ProviderDeferred, ProviderUncertain
from app.shared.actors import SYSTEM
from app.shared.clock import Clock

logger = structlog.get_logger()

_Effect = Callable[[AsyncSession], Awaitable[Mapping[str, object]]]
_FAILED = ConfirmationStatus.FAILED
_NOT_STARTED = ConfirmationStatus.NOT_STARTED


@dataclass(frozen=True)
class FaceConfirmationJobs:
    database: Database
    sealer: Sealer
    liveness: LivenessProvider
    matcher: FaceMatcher
    storage: FileStorage
    clock: Clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            confirmations.START: external_effect(self.start),
            confirmations.READ: self.read,
            confirmations.MATCH: external_effect(self.match),
            confirmations.READ_MATCH: self.read_match,
        }

    async def start(self, job: ClaimedJob, context: JobContext) -> None:
        attempt_id = _attempt(job)
        async with self.database.actor_transaction(SYSTEM) as session:
            waiting = await confirmations.to_start(session, attempt_id, self.clock)
        if not waiting:
            return
        answer: Started | NotStarted
        try:
            answer = await self.liveness.start(attempt_id)
        except ProviderDeferred:
            raise RetryableJobError(code="provider_deferred") from None
        except ProviderUncertain:
            answer = NotStarted("provider_uncertain")

        async def record(session: AsyncSession) -> Mapping[str, object]:
            if isinstance(answer, Started):
                opened = await confirmations.note_open(
                    session, self.sealer, attempt_id, answer.request_id, answer.url, self.clock
                )
                return {"outcome": "open" if opened else "moved_on"}
            await confirmations.finish(session, attempt_id, _NOT_STARTED, answer.code, self.clock)
            return {"outcome": answer.code}

        await context.apply(record)

    async def read(self, job: ClaimedJob, context: JobContext) -> None:
        attempt_id, read = _attempt(job), _read(job)
        async with self.database.actor_transaction(SYSTEM) as session:
            request_id = await confirmations.to_read(session, attempt_id, self.clock)
        if request_id is None:
            return
        try:
            answer = await self.liveness.result(request_id)
        except (ProviderDeferred, ProviderUncertain):
            raise RetryableJobError(code="provider_unavailable") from None
        if isinstance(answer, StillOpen):
            await context.apply(self._again(confirmations.READ, attempt_id, read))
            return
        if isinstance(answer, NotLive):
            await context.apply(self._end(attempt_id, _FAILED, answer.code))
            return
        await self._captured(attempt_id, answer, context)

    async def _captured(self, attempt_id: str, answer: Live, context: JobContext) -> None:
        async with self.database.actor_transaction(SYSTEM) as session:
            check_id = await confirmations.check_of(session, attempt_id)
        key = confirmations.capture_key(check_id, attempt_id)
        try:
            stored = await self.storage.put(NewFile(key, answer.photo, answer.content_type))
        except StorageError:
            # Nothing was saved. The provider still holds the answer, so read it again.
            raise RetryableJobError(code="storage_unavailable") from None
        noted = False

        async def record(session: AsyncSession) -> Mapping[str, object]:
            nonlocal noted
            noted = await confirmations.note_capture(session, attempt_id, stored, self.clock)
            return {"outcome": "matching" if noted else "moved_on"}

        try:
            await context.apply(record)
        except Exception:
            await self._discard(key, attempt_id)
            raise
        if not noted:
            await self._discard(key, attempt_id)

    async def match(self, job: ClaimedJob, context: JobContext) -> None:
        attempt_id = _attempt(job)
        async with self.database.actor_transaction(SYSTEM) as session:
            photos = await confirmations.to_match(session, attempt_id, self.clock)
        if photos is None:
            return
        live, on_file = photos
        answer: MatchSent | MatchRefused
        try:
            answer = await self.matcher.send(attempt_id, live, on_file)
        except ProviderDeferred:
            raise RetryableJobError(code="provider_deferred") from None
        except ProviderUncertain:
            # ioTec cannot be asked what it holds, and the client is waiting: end this attempt.
            await context.apply(self._end(attempt_id, _FAILED, "provider_uncertain"))
            return
        if isinstance(answer, MatchRefused):
            await context.apply(self._end(attempt_id, _FAILED, answer.code))
            return
        request_id = answer.request_id

        async def record(session: AsyncSession) -> Mapping[str, object]:
            sent = await confirmations.note_match_sent(session, attempt_id, request_id, self.clock)
            return {"outcome": "sent" if sent else "moved_on"}

        await context.apply(record)

    async def read_match(self, job: ClaimedJob, context: JobContext) -> None:
        attempt_id, read = _attempt(job), _read(job)
        async with self.database.actor_transaction(SYSTEM) as session:
            request_id = await confirmations.to_read_match(session, attempt_id, self.clock)
        if request_id is None:
            return
        answer: MatchPending | MatchAnswer | MatchNotRun
        try:
            answer = await self.matcher.result(request_id)
        except (ProviderDeferred, ProviderUncertain):
            raise RetryableJobError(code="provider_unavailable") from None
        if isinstance(answer, MatchPending):
            await context.apply(self._again(confirmations.READ_MATCH, attempt_id, read))
            return
        if isinstance(answer, MatchNotRun):
            await context.apply(self._end(attempt_id, _FAILED, answer.code))
            return
        score, identical = answer.score, answer.identical

        async def record(session: AsyncSession) -> Mapping[str, object]:
            status = await confirmations.record_match(
                session, attempt_id, score, identical, self.clock
            )
            return {"outcome": "moved_on" if status is None else status.value}

        await context.apply(record)

    def _again(self, kind: str, attempt_id: str, read: int) -> _Effect:
        async def again(session: AsyncSession) -> Mapping[str, object]:
            await confirmations.read_again(session, kind, attempt_id, read + 1, self.clock)
            return {"outcome": "still_waiting"}

        return again

    def _end(self, attempt_id: str, status: ConfirmationStatus, code: str) -> _Effect:
        async def end(session: AsyncSession) -> Mapping[str, object]:
            await confirmations.finish(session, attempt_id, status, code, self.clock)
            return {"outcome": code}

        return end

    async def _discard(self, key: str, attempt_id: str) -> None:
        try:
            await self.storage.delete(key)
        except StorageError:
            logger.warning("face_capture_orphaned", attempt_id=attempt_id)


def _attempt(job: ClaimedJob) -> str:
    attempt_id = job.payload.get("attempt_id")
    if not isinstance(attempt_id, str):
        raise PermanentJobError(code="invalid_payload")
    return attempt_id


def _read(job: ClaimedJob) -> int:
    read = job.payload.get("read")
    if type(read) is not int:
        raise PermanentJobError(code="invalid_payload")
    return read
