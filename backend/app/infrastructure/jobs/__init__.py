"""Durable outbox, job workers and the single active scheduler, all in PostgreSQL."""

from app.infrastructure.jobs.errors import (
    JobError,
    LeadershipLostError,
    PermanentJobError,
    RetryableJobError,
    StaleLeaseError,
    UncertainOutcomeError,
)
from app.infrastructure.jobs.outbox import Consumer, publish
from app.infrastructure.jobs.queue import ClaimedJob, JobRecord, JobRequest, enqueue, is_active
from app.infrastructure.jobs.retry import RetryPolicy
from app.infrastructure.jobs.scheduler import (
    LeaderLease,
    Schedule,
    ScheduleDispatcher,
    SchedulerService,
)
from app.infrastructure.jobs.worker import (
    Handler,
    JobContext,
    JobHandler,
    Worker,
    WorkerConfig,
    external_effect,
)

__all__ = [
    "ClaimedJob",
    "Consumer",
    "Handler",
    "JobContext",
    "JobError",
    "JobHandler",
    "JobRecord",
    "JobRequest",
    "LeaderLease",
    "LeadershipLostError",
    "PermanentJobError",
    "RetryPolicy",
    "RetryableJobError",
    "Schedule",
    "ScheduleDispatcher",
    "SchedulerService",
    "StaleLeaseError",
    "UncertainOutcomeError",
    "Worker",
    "WorkerConfig",
    "enqueue",
    "external_effect",
    "is_active",
    "publish",
]
