"""One active scheduler: a database leadership lease gates APScheduler and every dispatch.

APScheduler 3.x only times the dispatch calls in the leader process. PostgreSQL owns what is
durable: schedule progress, deterministic schedule instances and the jobs that do the work.
A dispatch commits only while its leadership token is current, so a deposed or partitioned
leader cannot create instances, and each instance is unique by schedule name and fire time.
"""

import asyncio
import contextlib
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.base import BaseTrigger  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import queue
from app.infrastructure.jobs.errors import LeadershipLostError
from app.infrastructure.jobs.queue import JobRequest
from app.shared.clock import Clock, SystemClock

logger = structlog.get_logger()

# Bounds the catch-up walk over missed fire times after a long outage.
MAX_FIRE_TIME_SCAN = 100_000


@dataclass(frozen=True)
class Schedule:
    """A recurring system job. `catch_up` decides what happens to fire times missed in downtime.

    `latest` coalesces missed fire times into one instance for the most recent. `all` creates an
    instance for each, up to the most recent `max_catch_up`; older ones count as skipped. A run
    that must happen for every day, such as daily interest, uses `all`.
    """

    name: str
    trigger: BaseTrigger
    kind: str
    queue: str = "default"
    payload: Mapping[str, object] = field(default_factory=dict)
    catch_up: Literal["latest", "all"] = "latest"
    max_catch_up: int = 10
    max_attempts: int = 5
    max_age: timedelta = timedelta(hours=1)

    def __post_init__(self) -> None:
        if not 1 <= self.max_catch_up <= 1000:
            raise ValueError("max_catch_up must be between 1 and 1000")


def instance_key(schedule: str, fire_time: datetime) -> str:
    """Deterministic identity of one schedule instance."""
    return f"{schedule}@{fire_time.astimezone(UTC).isoformat()}"


class LeaderLease:
    """A renewable, fenced leadership lease stored in `scheduler_leadership`."""

    def __init__(
        self,
        database: Database,
        *,
        holder: str,
        name: str = "default",
        ttl: timedelta = timedelta(seconds=30),
        clock: Clock | None = None,
    ) -> None:
        self.database = database
        self.holder = holder
        self.name = name
        self.ttl = ttl
        self.clock = clock or SystemClock()
        self.token: int | None = None

    async def acquire(self) -> int | None:
        """Take leadership if it is free, expired or already ours; returns a new token."""
        now = self.clock.now()
        async with self.database.transaction() as session:
            token: int | None = await session.scalar(
                text(
                    "INSERT INTO scheduler_leadership "
                    "(name, holder, fencing_token, expires_at, acquired_at, renewed_at) "
                    "VALUES (:name, :holder, 1, :expires, :now, :now) "
                    "ON CONFLICT (name) DO UPDATE SET holder = EXCLUDED.holder, "
                    "fencing_token = scheduler_leadership.fencing_token + 1, "
                    "expires_at = EXCLUDED.expires_at, acquired_at = EXCLUDED.acquired_at, "
                    "renewed_at = EXCLUDED.renewed_at "
                    "WHERE scheduler_leadership.expires_at <= :now "
                    "OR scheduler_leadership.holder = :holder "
                    "RETURNING fencing_token"
                ),
                {"name": self.name, "holder": self.holder, "now": now, "expires": now + self.ttl},
            )
        self.token = token
        return token

    async def renew(self) -> bool:
        """Extend the current lease; False means leadership was lost and must stop."""
        if self.token is None:
            return False
        now = self.clock.now()
        async with self.database.transaction() as session:
            renewed = await session.scalar(
                text(
                    "UPDATE scheduler_leadership SET expires_at = :expires, renewed_at = :now "
                    "WHERE name = :name AND holder = :holder AND fencing_token = :token "
                    "AND expires_at > :now RETURNING fencing_token"
                ),
                {
                    "name": self.name,
                    "holder": self.holder,
                    "token": self.token,
                    "now": now,
                    "expires": now + self.ttl,
                },
            )
        if renewed is None:
            self.token = None
            return False
        return True

    async def release(self) -> None:
        """Expire our lease now so a standby can take over without waiting for the TTL."""
        if self.token is None:
            return
        now = self.clock.now()
        async with self.database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE scheduler_leadership SET expires_at = :now "
                    "WHERE name = :name AND holder = :holder AND fencing_token = :token"
                ),
                {"name": self.name, "holder": self.holder, "token": self.token, "now": now},
            )
        self.token = None


@dataclass(frozen=True)
class DispatchResult:
    schedule: str
    instances: tuple[datetime, ...]
    skipped: int
    next_fire_at: datetime | None


class ScheduleDispatcher:
    def __init__(self, database: Database, lease: LeaderLease, clock: Clock | None = None):
        self.database = database
        self.lease = lease
        self.clock = clock or SystemClock()

    async def dispatch_due(self, schedule: Schedule) -> DispatchResult:
        """Enqueue due instances of `schedule` and advance its progress atomically."""
        token = self.lease.token
        if token is None:
            raise LeadershipLostError(self.lease.name)
        now = self.clock.now()
        async with self.database.transaction() as session:
            await self._hold_leadership(session, token, now)
            row = (
                await session.execute(
                    text(
                        "SELECT next_fire_at FROM schedule_progress WHERE name = :name FOR UPDATE"
                    ),
                    {"name": schedule.name},
                )
            ).one_or_none()
            if row is None:
                # First sight of a schedule starts from now; it never backfills history.
                first = schedule.trigger.get_next_fire_time(None, now)
                await session.execute(
                    text(
                        "INSERT INTO schedule_progress (name, next_fire_at, updated_at) "
                        "VALUES (:name, :next, :now) ON CONFLICT (name) DO NOTHING"
                    ),
                    {"name": schedule.name, "next": first, "now": now},
                )
                return DispatchResult(schedule.name, (), 0, first)
            due, total, following = self._due(schedule, row.next_fire_at, now)
            for fire_time in due:
                await self._create_instance(session, schedule, fire_time, token, now)
            await session.execute(
                text(
                    "UPDATE schedule_progress SET next_fire_at = :next, "
                    "last_fire_at = coalesce(:last, last_fire_at), "
                    "dispatched_count = dispatched_count + :dispatched, "
                    "skipped_count = skipped_count + :skipped, updated_at = :now "
                    "WHERE name = :name"
                ),
                {
                    "name": schedule.name,
                    "next": following,
                    "last": due[-1] if due else None,
                    "dispatched": len(due),
                    "skipped": total - len(due),
                    "now": now,
                },
            )
        return DispatchResult(schedule.name, tuple(due), total - len(due), following)

    async def _hold_leadership(self, session: AsyncSession, token: int, now: datetime) -> None:
        # FOR SHARE blocks a competing takeover until this dispatch commits or rolls back.
        held = await session.scalar(
            text(
                "SELECT fencing_token FROM scheduler_leadership WHERE name = :name "
                "AND holder = :holder AND fencing_token = :token AND expires_at > :now FOR SHARE"
            ),
            {"name": self.lease.name, "holder": self.lease.holder, "token": token, "now": now},
        )
        if held is None:
            raise LeadershipLostError(self.lease.name)

    @staticmethod
    def _due(
        schedule: Schedule, next_fire_at: datetime | None, now: datetime
    ) -> tuple[list[datetime], int, datetime | None]:
        keep = 1 if schedule.catch_up == "latest" else schedule.max_catch_up
        due: deque[datetime] = deque(maxlen=keep)
        total = 0
        fire_time = next_fire_at
        while fire_time is not None and fire_time <= now:
            due.append(fire_time)
            total += 1
            if total >= MAX_FIRE_TIME_SCAN:
                fire_time = schedule.trigger.get_next_fire_time(None, now)
                if fire_time is not None and fire_time <= now:
                    fire_time = schedule.trigger.get_next_fire_time(fire_time, now)
                break
            fire_time = schedule.trigger.get_next_fire_time(fire_time, now)
        return list(due), total, fire_time

    @staticmethod
    async def _create_instance(
        session: AsyncSession, schedule: Schedule, fire_time: datetime, token: int, now: datetime
    ) -> None:
        # The progress row lock serialises dispatches of one schedule, so this check is exact.
        exists = await session.scalar(
            text(
                "SELECT 1 FROM schedule_instances "
                "WHERE schedule_name = :name AND scheduled_for = :fire_time"
            ),
            {"name": schedule.name, "fire_time": fire_time},
        )
        if exists is not None:
            return
        key = instance_key(schedule.name, fire_time)
        job_id = await queue.enqueue(
            session,
            JobRequest(
                kind=schedule.kind,
                queue=schedule.queue,
                idempotency_key=key,
                payload={
                    "schedule": schedule.name,
                    "scheduled_for": fire_time.astimezone(UTC).isoformat(),
                    "data": dict(schedule.payload),
                },
                max_attempts=schedule.max_attempts,
                max_age=schedule.max_age,
            ),
            now=now,
        )
        if job_id is None:
            raise RuntimeError(f"An unrecorded job already exists for {key}")
        await session.execute(
            text(
                "INSERT INTO schedule_instances "
                "(schedule_name, scheduled_for, job_id, leader_token, created_at) "
                "VALUES (:name, :fire_time, :job_id, :token, :now)"
            ),
            {
                "name": schedule.name,
                "fire_time": fire_time,
                "job_id": job_id,
                "token": token,
                "now": now,
            },
        )


class SchedulerService:
    """Hold leadership, run APScheduler only while leader and stop it when leadership is lost."""

    def __init__(
        self,
        database: Database,
        schedules: Sequence[Schedule],
        lease: LeaderLease,
        *,
        clock: Clock | None = None,
        renew_interval: timedelta = timedelta(seconds=10),
    ) -> None:
        if renew_interval >= lease.ttl:
            raise ValueError("Leadership must be renewed before the lease expires")
        names = [schedule.name for schedule in schedules]
        if len(set(names)) != len(names):
            raise ValueError("Schedule names must be unique")
        self.schedules = list(schedules)
        self.lease = lease
        self.dispatcher = ScheduleDispatcher(database, lease, clock)
        self.renew_interval = renew_interval
        self.scheduler: AsyncIOScheduler | None = None
        self.log = logger.bind(scheduler=lease.holder)
        # Dispatches in progress; shutdown waits for them before handing leadership on.
        self._dispatching = 0
        self._idle = asyncio.Event()
        self._idle.set()
        # Bounded wait for an in-flight dispatch at shutdown; its transaction is short.
        self.drain_timeout = timedelta(seconds=10)

    @property
    def leading(self) -> bool:
        return self.scheduler is not None

    async def step(self) -> None:
        """One leadership round: acquire or renew, then start or stop dispatching."""
        try:
            if self.leading:
                if not await self.lease.renew():
                    self.log.warning("scheduler_leadership_lost")
                    self.stop_dispatch()
            elif await self.lease.acquire() is not None:
                self.log.info("scheduler_leadership_acquired", token=self.lease.token)
                await self.start_dispatch()
        except Exception as error:  # cannot prove leadership, so stop dispatching
            self.log.warning("scheduler_lease_check_failed", error_type=type(error).__name__)
            self.lease.token = None
            self.stop_dispatch()

    async def run(self, stop: asyncio.Event) -> None:
        self.log.info("scheduler_started", schedules=[s.name for s in self.schedules])
        try:
            while not stop.is_set():
                await self.step()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self.renew_interval.total_seconds())
        finally:
            # Stop scheduling first, let any dispatch in hand commit, then hand over.
            self.stop_dispatch()
            try:
                await asyncio.wait_for(self._idle.wait(), self.drain_timeout.total_seconds())
            except TimeoutError:
                self.log.warning("scheduler_drain_timed_out", dispatching=self._dispatching)
            try:
                await self.lease.release()
            except Exception as error:  # the lease then simply expires
                self.log.warning("scheduler_release_failed", error_type=type(error).__name__)
            self.log.info("scheduler_stopped")

    async def start_dispatch(self) -> None:
        scheduler = AsyncIOScheduler(timezone=UTC)
        for schedule in self.schedules:
            scheduler.add_job(
                self.dispatch,
                trigger=schedule.trigger,
                args=[schedule],
                id=schedule.name,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=None,
            )
        scheduler.start()
        self.scheduler = scheduler
        # Catch up from persisted progress rather than APScheduler's in-memory state.
        for schedule in self.schedules:
            await self.dispatch(schedule)

    def stop_dispatch(self) -> None:
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None

    async def dispatch(self, schedule: Schedule) -> None:
        if not self.leading:
            return
        self._dispatching += 1
        self._idle.clear()
        try:
            await self._dispatch(schedule)
        finally:
            self._dispatching -= 1
            if self._dispatching == 0:
                self._idle.set()

    async def _dispatch(self, schedule: Schedule) -> None:
        try:
            result = await self.dispatcher.dispatch_due(schedule)
        except LeadershipLostError:
            self.log.warning("scheduler_leadership_lost", schedule=schedule.name)
            self.lease.token = None
            self.stop_dispatch()
            return
        except Exception as error:  # the next firing retries from progress
            self.log.error(
                "schedule_dispatch_failed", schedule=schedule.name, error_type=type(error).__name__
            )
            return
        if result.instances or result.skipped:
            self.log.info(
                "schedule_dispatched",
                schedule=schedule.name,
                instances=len(result.instances),
                skipped=result.skipped,
            )
