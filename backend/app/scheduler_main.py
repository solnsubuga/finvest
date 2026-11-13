"""The scheduler process: `python -m app.scheduler_main`. Connect it as `finvest_worker`.

It turns recurring schedules into durable jobs for the worker and does no work itself. Any
number of instances may run: a leadership lease in PostgreSQL lets only one dispatch at a time,
and each fire time becomes exactly one job.
"""

import asyncio
from datetime import UTC, timedelta

from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]

from app.infrastructure.database import Database
from app.infrastructure.database.roles import WORKER, check_role
from app.infrastructure.jobs import LeaderLease, Schedule, SchedulerService
from app.infrastructure.jobs.settings import JobProcessSettings
from app.infrastructure.jobs.worker import default_worker_id
from app.infrastructure.logging import configure_logging
from app.infrastructure.process import keep_heartbeat, stop_on_signals
from app.infrastructure.settings import Settings
from app.modules.documents.service import MONTHLY_KIND
from app.modules.investments.jobs import CANCEL_UNPAID_KIND
from app.modules.kyc.service import SWEEP_UNANSWERED
from app.modules.notifications.service import PURGE_MESSAGES
from app.modules.payments import service as payments
from app.modules.payments.settings import PaymentsSettings
from app.modules.payments.wiring import ensure_allowed
from app.shared.business_day import KAMPALA
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
        # Cancels investments nobody paid for within the payment window.
        Schedule(
            name="investments.cancel_unpaid",
            trigger=IntervalTrigger(minutes=15),
            kind=CANCEL_UNPAID_KIND,
            max_age=timedelta(minutes=30),
        ),
        # Last month's statement for every client, at 06:00 in Kampala on the first. A run
        # missed while the scheduler was down is made up once; each client is asked once.
        Schedule(
            name="documents.monthly_statements",
            # Every morning: the run asks for last month only once the ledger has closed it.
            trigger=CronTrigger(hour=6, minute=0, timezone=KAMPALA),
            kind=MONTHLY_KIND,
            max_age=timedelta(hours=20),
        ),
    ]
    payments_settings = PaymentsSettings()
    ensure_allowed(payments_settings)
    if payments_settings.provider != "disabled":
        # Picks up deposits whose own job was lost. One run covers any it missed.
        runs.append(
            Schedule(
                name="payments.sweep_deposits",
                trigger=IntervalTrigger(minutes=1),
                kind=payments.SWEEP_KIND,
                max_age=timedelta(minutes=5),
            )
        )
    # Clears the content of messages that were never sent, and old addresses and numbers.
    runs.append(
        Schedule(
            name="notifications.purge",
            trigger=CronTrigger(minute=17, timezone=UTC),
            kind=PURGE_MESSAGES,
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
