"""Poll, claim and run jobs. Handlers do external work with no transaction or lock open."""

import asyncio
import contextlib
import socket
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import queue
from app.infrastructure.jobs.errors import (
    UNCERTAIN_OUTCOME,
    JobError,
    PermanentJobError,
    StaleLeaseError,
)
from app.infrastructure.jobs.queue import ClaimedJob, JsonObject
from app.infrastructure.jobs.retry import RetryPolicy
from app.shared.actors import SYSTEM
from app.shared.clock import Clock, SystemClock

Effect = Callable[[AsyncSession], Awaitable[Mapping[str, object]]]
logger = structlog.get_logger()


class JobHandler(Protocol):
    async def __call__(self, job: ClaimedJob, context: "JobContext") -> None: ...


@dataclass(frozen=True)
class Handler:
    """A handler and what the worker must know about it before running it.

    Declare `external_effect=True` for any handler that calls a provider or otherwise changes
    something outside the database that a second run would repeat. Such a job is never run
    again on a guess. An exception the handler did not sort into deferred, rejected or
    uncertain, and a lease that expired after an attempt started, both end the job as
    `uncertain_outcome`, so reconciliation asks the provider what happened.
    """

    run: JobHandler
    external_effect: bool = False


def external_effect(handler: JobHandler) -> Handler:
    """Register a handler whose work reaches outside the database."""
    return Handler(handler, external_effect=True)


def default_worker_id() -> str:
    return f"{socket.gethostname()[:100]}:{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class WorkerConfig:
    queue: str = "default"
    worker_id: str = field(default_factory=default_worker_id)
    lease: timedelta = timedelta(seconds=30)
    concurrency: int = 4
    poll_interval: timedelta = timedelta(seconds=1)
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    def __post_init__(self) -> None:
        if not 1 <= self.concurrency <= 100:
            raise ValueError("Worker concurrency must be between 1 and 100")


class JobContext:
    """What a handler may do with its claim: extend the lease and apply its result once.

    The shape of a handler that calls a provider:

    1. Do the external call with no transaction open. Sort a failure into `RetryableJobError`
       (it did not happen), `PermanentJobError` (it was refused) or `UncertainOutcomeError` (it
       may have happened).
    2. Call `apply` with the database changes that record the confirmed result. They commit
       together with the job's completion, exactly once.
    """

    def __init__(self, database: Database, job: ClaimedJob, clock: Clock, lease: timedelta):
        self._database = database
        self._job = job
        self._clock = clock
        self._lease = lease
        self.completed = False

    async def heartbeat(self) -> None:
        """Extend the lease during long external work. Raises `StaleLeaseError` if lost."""
        async with self._database.actor_transaction(SYSTEM) as session:
            await queue.extend_lease(session, self._job, now=self._clock.now(), lease=self._lease)

    @contextlib.asynccontextmanager
    async def kept_alive(self) -> AsyncIterator[None]:
        """Extend the lease in the background while the body runs.

        A lost lease raises `StaleLeaseError` once the body is done, so this worker records
        nothing and the new holder owns the outcome.
        """
        lost: list[StaleLeaseError] = []

        async def beat() -> None:
            while not self.completed:
                await asyncio.sleep(self._lease.total_seconds() / 3)
                if self.completed:
                    return
                try:
                    await self.heartbeat()
                except StaleLeaseError as error:
                    lost.append(error)
                    return
                except Exception as error:  # a missed beat is retried on the next one
                    logger.warning("job_heartbeat_failed", error_type=type(error).__name__)

        beating = asyncio.create_task(beat())
        try:
            yield
        finally:
            beating.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beating
        if lost and not self.completed:
            raise lost[0]

    async def apply(self, effect: Effect) -> JsonObject:
        """Apply the job's database result and complete the job in one fenced transaction.

        If this consumer already applied the same idempotency key, in this job or an earlier
        one for the same logical delivery, the saved result is returned and `effect` is not
        called. A stale claim raises `StaleLeaseError` and nothing is written.
        """
        if self.completed:
            raise RuntimeError("A job applies its result at most once")
        job = self._job
        async with self._database.actor_transaction(SYSTEM) as session:
            await queue.lock_fenced(session, job)
            saved = await queue.read_receipt(session, consumer=job.kind, key=job.idempotency_key)
            if saved is None:
                result = dict(await effect(session))
                await queue.write_receipt(session, job, result, now=self._clock.now())
            else:
                result = saved
            await queue.finish(session, job, now=self._clock.now(), outcome="succeeded")
        self.completed = True
        return result


class Worker:
    def __init__(
        self,
        database: Database,
        handlers: Mapping[str, JobHandler | Handler],
        config: WorkerConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.database = database
        self.handlers = {
            kind: handler if isinstance(handler, Handler) else Handler(handler)
            for kind, handler in handlers.items()
        }
        self.external_kinds = sorted(
            kind for kind, handler in self.handlers.items() if handler.external_effect
        )
        self.config = config or WorkerConfig()
        self.clock = clock or SystemClock()
        self.log = logger.bind(worker=self.config.worker_id, queue=self.config.queue)

    async def _claim(self, limit: int) -> list[ClaimedJob]:
        return await queue.claim(
            self.database,
            queue=self.config.queue,
            kinds=sorted(self.handlers),
            external_kinds=self.external_kinds,
            worker=self.config.worker_id,
            now=self.clock.now(),
            lease=self.config.lease,
            limit=limit,
        )

    async def run_once(self) -> int:
        """Claim up to `concurrency` due jobs, run them concurrently and return the count."""
        if not self.handlers:
            return 0
        jobs = await self._claim(self.config.concurrency)
        async with asyncio.TaskGroup() as group:
            for job in jobs:
                group.create_task(self.execute(job))
        return len(jobs)

    async def run(self, stop: asyncio.Event) -> None:
        """Poll until `stop` is set. Jobs in hand finish before returning.

        A slot is filled again as soon as its job ends, so one long job does not hold back the
        quick ones queued behind it.
        """
        self.log.info("worker_started", kinds=sorted(self.handlers))
        running: set[asyncio.Task[None]] = set()
        stopping = asyncio.create_task(stop.wait())
        try:
            while not stop.is_set():
                free = self.config.concurrency - len(running)
                claimed = 0
                if free > 0 and self.handlers:
                    try:
                        jobs = await self._claim(free)
                    except Exception as error:  # keep polling through outages
                        self.log.warning("job_claim_failed", error_type=type(error).__name__)
                        jobs = []
                    claimed = len(jobs)
                    running.update(asyncio.create_task(self.execute(job)) for job in jobs)
                if claimed:
                    continue  # more may be due at once; ask again straight away
                done, _ = await asyncio.wait(
                    {*running, stopping},
                    timeout=self.config.poll_interval.total_seconds(),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in running & done:
                    running.discard(task)
                    failure = None if task.cancelled() else task.exception()
                    if failure is not None:
                        self.log.error("job_task_failed", error_type=type(failure).__name__)
        finally:
            stopping.cancel()
            if running:
                await asyncio.gather(*running, return_exceptions=True)
        self.log.info("worker_stopped")

    async def execute(self, job: ClaimedJob) -> None:
        # The job and the outbox event it serves tie this work to the request that staged it.
        with structlog.contextvars.bound_contextvars(
            job_id=job.id, event_id=job.event_id, kind=job.kind, attempt=job.attempt
        ):
            await self._execute(job)

    async def _execute(self, job: ClaimedJob) -> None:
        log = self.log
        context = JobContext(self.database, job, self.clock, self.config.lease)
        try:
            await self.handlers[job.kind].run(job, context)
            if not context.completed:
                async with self.database.actor_transaction(SYSTEM) as session:
                    await queue.finish(session, job, now=self.clock.now(), outcome="succeeded")
            log.info("job_succeeded")
        except StaleLeaseError:
            # Another worker holds a newer claim, or the job already finished. It owns the
            # outcome now, so this worker records nothing.
            log.warning("job_lease_lost")
        except Exception as error:  # every failure is recorded, never raised
            if context.completed:
                # The result was already committed. A later error cannot undo or retry it.
                log.warning("job_handler_failed_after_apply", error_type=type(error).__name__)
                return
            try:
                await self._record_failure(job, error, log)
            except Exception as recording:  # the lease expiry recovers the job
                log.error(
                    "job_failure_not_recorded",
                    handler_error_type=type(error).__name__,
                    error_type=type(recording).__name__,
                )

    async def _record_failure(
        self, job: ClaimedJob, error: Exception, log: structlog.typing.FilteringBoundLogger
    ) -> None:
        now = self.clock.now()
        # An exception the handler did not sort says nothing about how far the work got. With
        # an external effect it may have happened, so the job stops for reconciliation.
        uncertain = not isinstance(error, JobError) and self.handlers[job.kind].external_effect
        if isinstance(error, JobError):
            code, message = error.code, error.safe_message
        else:
            # Arbitrary exception text may contain personal data or secrets. Keep the type only.
            code = UNCERTAIN_OUTCOME if uncertain else "unhandled_error"
            message = type(error).__name__
        retry_at = now + self.config.retry.delay(job.attempt)
        # Without an external effect an unrecognised exception is retried: running the handler
        # again repeats nothing outside the database.
        retryable = (
            not uncertain
            and not isinstance(error, PermanentJobError)
            and job.attempt < job.max_attempts
            and retry_at < job.expires_at
        )
        async with self.database.actor_transaction(SYSTEM) as session:
            try:
                await queue.finish(
                    session,
                    job,
                    now=now,
                    outcome="retry" if retryable else "failed",
                    run_at=retry_at if retryable else None,
                    error_code=code,
                    error_message=message,
                )
            except StaleLeaseError:
                log.warning("job_lease_lost", error_code=code)
                return
        if retryable:
            log.warning("job_retry_scheduled", error_code=code, retry_at=retry_at.isoformat())
        else:
            log.error("job_failed", error_code=code, error_type=type(error).__name__)
