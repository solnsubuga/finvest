"""The scheduler process: `python -m app.scheduler_main`. Connect it as `finvest_worker`.

It turns recurring schedules into durable jobs for the worker and does no work itself. Any
number of instances may run: a leadership lease in PostgreSQL lets only one dispatch at a time,
and each fire time becomes exactly one job.
"""

import asyncio
from datetime import timedelta

from app.infrastructure.database import Database
from app.infrastructure.database.roles import WORKER, check_role
from app.infrastructure.jobs import LeaderLease, Schedule, SchedulerService
from app.infrastructure.jobs.settings import JobProcessSettings
from app.infrastructure.jobs.worker import default_worker_id
from app.infrastructure.logging import configure_logging
from app.infrastructure.process import keep_heartbeat, stop_on_signals
from app.infrastructure.settings import Settings
from app.shared.clock import SystemClock


def schedules() -> list[Schedule]:
    """Every recurring run. A module adds its schedules here.

    A run that must happen for each day it missed, such as daily interest, sets `catch_up="all"`.
    """
    return []


async def run(settings: Settings, stop: asyncio.Event | None = None) -> None:
    stop = stop or stop_on_signals()
    jobs = JobProcessSettings()
    clock = SystemClock()
    database = Database(settings)
    try:
        await check_role(database, WORKER)
        lease = LeaderLease(
            database,
            holder=default_worker_id(),
            ttl=timedelta(seconds=jobs.leader_ttl_seconds),
            clock=clock,
        )
        service = SchedulerService(
            database,
            schedules(),
            lease,
            clock=clock,
            renew_interval=timedelta(seconds=jobs.leader_renew_seconds),
        )
        async with asyncio.TaskGroup() as group:
            group.create_task(keep_heartbeat("scheduler", database.ready, stop))
            group.create_task(service.run(stop))
    finally:
        await database.close()


def main() -> None:
    settings = Settings()
    configure_logging(settings, "scheduler")
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
