"""The worker process: `python -m app.worker_main`. Connect it as `finvest_worker`.

It claims jobs from the PostgreSQL jobs table and runs their handlers. Every provider call in
the system happens here, never in the API and never inside a database transaction.
"""

import asyncio
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.database.roles import WORKER, check_role
from app.infrastructure.email import EmailSettings
from app.infrastructure.jobs import Handler, JobHandler, Worker, WorkerConfig
from app.infrastructure.jobs.settings import JobProcessSettings
from app.infrastructure.logging import configure_logging
from app.infrastructure.process import keep_heartbeat, stop_on_signals
from app.infrastructure.push import PushSettings
from app.infrastructure.settings import Settings
from app.infrastructure.sms import SmsSettings
from app.infrastructure.storage import StorageSettings
from app.modules.autoinvest.jobs import AutoinvestJobs
from app.modules.dealing.jobs import DealingJobs
from app.modules.documents.jobs import DocumentJobs
from app.modules.goals.jobs import GoalJobs
from app.modules.identity.service import client_contact
from app.modules.investments.jobs import InvestmentJobs
from app.modules.kyc.face_confirmation_jobs import FaceConfirmationJobs
from app.modules.kyc.jobs import ProviderJobs, sweep_unanswered
from app.modules.kyc.liveness_jobs import LivenessJobs
from app.modules.kyc.phone_name_jobs import PhoneNameJobs
from app.modules.kyc.service import SWEEP_UNANSWERED
from app.modules.kyc.settings import KycSettings
from app.modules.migration.jobs import MigrationJobs
from app.modules.notifications import jobs as notification_jobs
from app.modules.notifications.announcements import AnnouncementJobs
from app.modules.notifications.delivery import DeliveryJobs
from app.modules.notifications.email_delivery import DeliveryEventJobs
from app.modules.notifications.keyed_backfill import KeyedBackfillJobs
from app.modules.notifications.settings import EmailHashSettings
from app.modules.organisations.jobs import OrganisationJobs
from app.modules.payments.bank_transfers import BankTransferJobs
from app.modules.payments.jobs import DepositJobs
from app.modules.payments.reversals import ReversalJobs
from app.modules.payments.settings import PaymentsSettings
from app.modules.payments.wiring import collections_provider
from app.modules.payouts.jobs import PayoutJobs, release_screened
from app.modules.payouts.name_lookup_jobs import NameLookupJobs
from app.modules.payouts.wiring import PayoutNameSettings, disbursements_provider
from app.modules.reports.jobs import ReportJobs
from app.modules.screening.domain.provider import ScreeningSubject
from app.modules.screening.jobs import ScreeningJobs
from app.modules.screening.monitor_jobs import MonitoringJobs
from app.modules.screening.settings import ScreeningSettings
from app.modules.screening.wiring import approved_clients, subjects_from_identity_checks
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
        **DealingJobs(clock).handlers(),
        **GoalJobs(database, clock).handlers(),
        **OrganisationJobs(database, clock).handlers(),
    }
    identity = KycSettings()
    kinds.update(
        DocumentJobs(
            database,
            clock,
            email=EmailSettings().transport(),
            # Tax certificates print the TIN a client gave, which only this key opens.
            sealer=identity.sealer() if identity.identity_data_key is not None else None,
            links=settings.email_links(),
        ).handlers()
    )
    kinds.update(
        ReportJobs(
            database,
            clock,
            email=EmailSettings().transport(),
            console=settings.staff_console(),
            links=settings.email_links(),
        ).handlers()
    )
    if identity.identity_data_key is not None:
        # The switch's sheets are sealed with the identity data key.
        kinds.update(
            MigrationJobs(
                database,
                identity.sealer(),
                clock,
                settings.client_app(),
            ).handlers()
        )
    if identity.identity_provider != "manual":
        # The manual provider sends every client to a person and queues no provider jobs.
        storage = StorageSettings().storage()
        kinds.update(
            ProviderJobs(
                database,
                identity.sealer(),
                # The provider reads the photos it sends from private storage.
                identity.provider(storage),
                clock,
                automatic_approval=identity.identity_automatic_approval,
            ).handlers()
        )
        live = identity.liveness()
        if live is not None:
            # The face a liveness check captured is stored as the check's selfie.
            kinds.update(LivenessJobs(database, identity.sealer(), live, storage, clock).handlers())
            matcher = identity.face_matcher(storage)
            if matcher is not None:
                # A live selfie, matched to the approved check, confirms a sign-in change.
                kinds.update(
                    FaceConfirmationJobs(
                        database, identity.sealer(), live, matcher, storage, clock
                    ).handlers()
                )
    screening = ScreeningSettings().provider()
    if screening is not None and identity.identity_data_key is None:
        # The date of birth a screen sends is sealed with the identity data key.
        raise ValueError("A screening provider needs FINVEST_IDENTITY_DATA_KEY")
    # Runs with no provider too, so every screen asked for is recorded as not run.
    kinds.update(
        ScreeningJobs(
            database,
            screening,
            subjects_from_identity_checks(identity.sealer())
            if identity.identity_data_key is not None
            else _no_subjects,
            approved_clients(),
            clock,
        ).handlers()
    )
    kinds.update(release_screened(database, clock))
    kinds.update(MonitoringJobs(database, clock).handlers())
    # Bank transfers are settled from a staff review and call no provider, so they run even
    # where mobile money is turned off.
    kinds.update(BankTransferJobs(database, clock).handlers())
    kinds.update(ReversalJobs(database, clock).handlers())
    collections = collections_provider(PaymentsSettings(), clock)
    if collections is not None:
        kinds.update(DepositJobs(database, collections, clock).handlers())
    # Runs ask for mobile money payments, so they are asked for only with a provider.
    kinds.update(
        AutoinvestJobs(
            database, clock, collections.name if collections is not None else None
        ).handlers()
    )
    disbursements = disbursements_provider(PaymentsSettings(), clock)
    if disbursements is not None:
        kinds.update(PayoutJobs(database, disbursements, clock).handlers())
    names = PayoutNameSettings().provider()
    if names is not None:
        # Without a provider, a mobile money number waits for its name and its lookup waits.
        kinds.update(NameLookupJobs(database, names, clock).handlers())
        # The same lookup, for the phone number of a client handing in their identity check.
        kinds.update(PhoneNameJobs(database, names, clock).handlers())
    kinds.update(
        notification_jobs.handlers(
            database,
            clock,
            EmailSettings().transport(),
            SmsSettings().transport(),
            # A link in a message may only point to the console or the client app.
            link_origins=[*settings.staff_allowed_origins, *settings.client_allowed_origins],
            links=settings.email_links(),
        )
    )
    # Read at start-up, so production refuses to start without the key.
    email_hash_key = EmailHashSettings().key()
    kinds.update(DeliveryEventJobs(database, clock, email_hash_key).handlers())
    kinds.update(KeyedBackfillJobs(database, clock, email_hash_key).handlers())
    kinds.update(
        DeliveryJobs(database, clock, PushSettings().transport(), client_contact).handlers()
    )
    kinds.update(AnnouncementJobs(database, clock).handlers())
    return kinds


async def _no_subjects(session: AsyncSession, client_id: str) -> ScreeningSubject | None:
    """Without the identity data key nobody can be screened; only used with no provider."""
    return None


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
