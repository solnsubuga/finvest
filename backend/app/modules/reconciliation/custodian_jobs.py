"""The worker's side of a custodian statement check: read the uploaded file and compare it.

The file is read from storage and the reader is called with no transaction open. Reading only
copies, so a read that failed is asked again, a few times. On the last try, or when the reader
refuses the file, the check is recorded as unreadable so it never waits for ever.
"""

from collections.abc import Awaitable, Callable, Mapping
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
)
from app.infrastructure.storage import FileStorage, StorageError
from app.modules.reconciliation import custodian
from app.modules.reconciliation.domain.custodian import (
    ReaderRefused,
    ReaderUnavailable,
    Reading,
    StatementFile,
    StatementReader,
)
from app.shared.actors import SYSTEM
from app.shared.clock import Clock


@dataclass(frozen=True)
class CustodianJobs:
    database: Database
    # None until a reader is set up: every check is then recorded as unreadable.
    reader: StatementReader | None
    storage: FileStorage
    clock: Clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {custodian.READ_KIND: self.read}

    async def read(self, job: ClaimedJob, context: JobContext) -> None:
        check_id = job.payload.get("check_id")
        if not isinstance(check_id, str):
            raise PermanentJobError(code="invalid_payload")
        async with self.database.actor_transaction(SYSTEM) as session:
            pending = await custodian.pending_read(session, check_id)
        if pending is None:
            return  # already read
        reader = self.reader
        if reader is None:
            await self._unreadable(context, check_id, "reader_off", None)
            return
        last_try = job.attempt >= job.max_attempts
        try:
            data = await self.storage.read(pending.file_key, sha256=pending.file_sha256)
        except StorageError as error:
            if last_try or error.code in {"file_not_found", "file_mismatch"}:
                await self._unreadable(context, check_id, error.code, reader.name)
                return
            raise RetryableJobError(code="custodian_file_unavailable") from None
        try:
            # A long statement can take the reader a few minutes.
            async with context.kept_alive():
                reading = await reader.read(
                    StatementFile(pending.file_content_type, data), key=check_id
                )
        except ReaderUnavailable:
            if last_try:
                await self._unreadable(context, check_id, "reader_unavailable", reader.name)
                return
            raise RetryableJobError(code="custodian_reader_unavailable") from None
        except ReaderRefused as refused:
            await self._unreadable(context, check_id, refused.code, reader.name)
            return
        await context.apply(self._record(check_id, reading))

    def _record(
        self, check_id: str, reading: Reading
    ) -> Callable[[AsyncSession], Awaitable[Mapping[str, object]]]:
        async def record(session: AsyncSession) -> Mapping[str, object]:
            saved = await custodian.record_reading(session, check_id, reading, self.clock)
            return {"rows": len(reading.rows), "saved": saved}

        return record

    async def _unreadable(
        self, context: JobContext, check_id: str, code: str, reader: str | None
    ) -> None:
        async def record(session: AsyncSession) -> Mapping[str, object]:
            await custodian.record_unreadable(session, check_id, code, reader, self.clock)
            return {"status": "unreadable", "code": code}

        await context.apply(record)
