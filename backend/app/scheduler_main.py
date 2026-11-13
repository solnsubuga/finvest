"""The scheduler process: `python -m app.scheduler_main`. Connect it as `finvest_worker`.

It turns recurring schedules into durable jobs for the worker and does no work itself. Any
number of instances may run: a leadership lease in PostgreSQL lets only one dispatch at a time,
and each fire time becomes exactly one job.
"""

import asyncio
from datetime import timedelta

from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]

from app.infrastructure.database import Database
from app.infrastructure.database.roles import WORKER, check_role
from app.infrastructure.jobs import LeaderLease, Schedule, SchedulerService
from app.infrastructure.jobs.settings import JobProcessSettings
from app.infrastructure.jobs.worker import default_worker_id
from app.infrastructure.logging import configure_logging
from app.infrastructure.process import keep_heartbeat, stop_on_signals
from app.infrastructure.settings import Settings
from app.modules.kyc.service import SWEEP_UNANSWERED
from app.modules.payments import service as payments
from app.modules.payments.settings import PaymentsSettings
from app.shared.clock import SystemClock


def schedules() -> list[Schedule]:
    """Every recurring run. A module adds its schedules here.

    A run that must happen for each day it missed, such as daily interest, sets `catch_up="all"`.
    """
    runs: list[Schedule] = [
        # Identity checks the provider never answered go to a person.
        Schedule(
            name="kyc.sweep_unanswered",
            trigger=IntervalTrigger(minutes=5),
            kind=SWEEP_UNANSWERED,
            max_age=timedelta(minutes=5),
        ),
    ]
    if PaymentsSettings().provider != "disabled":
        # Picks up deposits whose own job was lost. One run covers any it missed.
        runs.append(
            Schedule(
                name="payments.sweep_deposits",
                trigger=IntervalTrigger(minutes=1),
                kind=payments.SWEEP_KIND,
                max_age=timedelta(minutes=5),
            )
        )
    return runs


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
