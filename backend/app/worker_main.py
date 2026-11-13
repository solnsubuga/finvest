"""The worker process: `python -m app.worker_main`. Connect it as `finvest_worker`.

It claims jobs from the PostgreSQL jobs table and runs their handlers. Every provider call in
the system happens here, never in the API and never inside a database transaction.
"""

import asyncio
from datetime import timedelta

from app.infrastructure.database import Database
from app.infrastructure.database.roles import WORKER, check_role
from app.infrastructure.email import EmailSettings
from app.infrastructure.jobs import Handler, JobHandler, Worker, WorkerConfig
from app.infrastructure.jobs.settings import JobProcessSettings
from app.infrastructure.logging import configure_logging
from app.infrastructure.process import keep_heartbeat, stop_on_signals
from app.infrastructure.settings import Settings
from app.infrastructure.sms import SmsSettings
from app.modules.autoinvest.jobs import AutoinvestJobs
from app.modules.dealing.jobs import DealingJobs
from app.modules.documents.jobs import DocumentJobs
from app.modules.goals.jobs import GoalJobs
from app.modules.investments.jobs import InvestmentJobs
from app.modules.kyc.jobs import ProviderJobs, sweep_unanswered
from app.modules.kyc.service import SWEEP_UNANSWERED
from app.modules.kyc.settings import KycSettings
from app.modules.notifications import jobs as notification_jobs
from app.modules.notifications.email_delivery import DeliveryEventJobs
from app.modules.payments.bank_transfers import BankTransferJobs
from app.modules.payments.jobs import DepositJobs
from app.modules.payments.settings import PaymentsSettings
from app.modules.payments.wiring import collections_provider
from app.modules.payouts.jobs import PayoutJobs
from app.modules.payouts.wiring import disbursements_provider
from app.shared.clock import Clock, SystemClock


def handlers(
    database: Database, clock: Clock, settings: Settings
) -> dict[str, JobHandler | Handler]:
    """Every job kind this worker runs. A module adds its handlers here, keyed by kind.

    A handler that calls a provider is registered with `external_effect(handler)`, so a crash
    or an unsorted exception stops the job for reconciliation and it is never run twice.
    """
    kinds: dict[str, JobHandler | Handler] = {
        SWEEP_UNANSWERED: sweep_unanswered(clock),
        **InvestmentJobs(database, clock).handlers(),
        **AutoinvestJobs(database, clock).handlers(),
        **DealingJobs(clock).handlers(),
        **DocumentJobs(database, clock).handlers(),
        **GoalJobs(database, clock).handlers(),
    }
    identity = KycSettings()
    if identity.identity_provider != "manual":
        # The manual provider sends every client to a person and queues no provider jobs.
        kinds.update(
            ProviderJobs(
                database,
                identity.sealer(),
                identity.provider(),
                clock,
                automatic_approval=identity.identity_automatic_approval,
            ).handlers()
        )
    # Bank transfers are settled from a staff review and call no provider, so they run even
    # where mobile money is turned off.
    kinds.update(BankTransferJobs(database, clock).handlers())
    collections = collections_provider(PaymentsSettings(), clock)
    if collections is not None:
        kinds.update(DepositJobs(database, collections, clock).handlers())
    disbursements = disbursements_provider(PaymentsSettings(), clock)
    if disbursements is not None:
        kinds.update(PayoutJobs(database, disbursements, clock).handlers())
    kinds.update(
        notification_jobs.handlers(
            database,
            clock,
            EmailSettings().transport(),
            SmsSettings().transport(),
            # A link in a message may only point to the console or the client app.
            link_origins=[*settings.staff_allowed_origins, *settings.client_allowed_origins],
        )
    )
    kinds.update(DeliveryEventJobs(database, clock).handlers())
    return kinds


async def run(settings: Settings, stop: asyncio.Event | None = None) -> None:
    stop = stop or stop_on_signals()
    jobs = JobProcessSettings()
    clock = SystemClock()
    database = Database(settings)
    try:
        await check_role(database, WORKER)
        worker = Worker(
            database,
            handlers(database, clock, settings),
            WorkerConfig(
                queue=jobs.queue,
                lease=jobs.lease,
                concurrency=jobs.concurrency,
                poll_interval=timedelta(seconds=jobs.poll_seconds),
            ),
            clock,
        )
        async with asyncio.TaskGroup() as group:
            group.create_task(keep_heartbeat("worker", database.ready, stop))
            group.create_task(worker.run(stop))
    finally:
        await database.close()


def main() -> None:
    settings = Settings()
    configure_logging(settings, "worker")
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
