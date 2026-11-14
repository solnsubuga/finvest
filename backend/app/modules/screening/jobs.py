"""The worker's side of screening: screen one client, and the daily rescreening sweep.

The provider is called with no transaction open. Screening only reads lists, so a screen that
failed is simply asked again; nothing here is an external effect that must not repeat.
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
from app.modules.screening import service
from app.modules.screening.domain.provider import (
    ProviderRefused,
    ProviderUnavailable,
    ScreeningProvider,
    ScreeningResult,
    ScreeningSubject,
)
from app.modules.screening.domain.standing import Reason, RunStatus, due_for_rescreen
from app.shared.actors import SYSTEM
from app.shared.business_day import business_date
from app.shared.clock import Clock

# Who to screen: the details on the client's handed-in or approved identity check.
SubjectReader = Callable[[AsyncSession, str], Awaitable[ScreeningSubject | None]]
# Approved clients a page at a time, by client ID after `after`.
ClientPager = Callable[[AsyncSession, str | None, int], Awaitable[list[str]]]

PAGE = 500
# The sweep asks for at most this many screens a day, so a first run over every client, or a
# provider back after an outage, is spread over days rather than sent at once.
MAX_PER_SWEEP = 5_000


@dataclass(frozen=True)
class ScreeningJobs:
    database: Database
    # None until a provider is set up: every screen is then recorded as not run.
    provider: ScreeningProvider | None
    subjects: SubjectReader
    clients: ClientPager
    clock: Clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            service.SCREEN_KIND: self.screen,
            service.RESCREEN_KIND: self.rescreen_due,
        }

    async def screen(self, job: ClaimedJob, context: JobContext) -> None:
        run_id = job.payload.get("run_id")
        if not isinstance(run_id, str):
            raise PermanentJobError(code="invalid_payload")
        async with self.database.actor_transaction(SYSTEM) as session:
            run = await service.pending_run(session, run_id)
            subject = (
                None
                if run is None or self.provider is None
                else await self.subjects(session, run.client_id)
            )
        if run is None:
            return  # already finished, for example given up on while the provider was slow
        provider = self.provider
        if provider is None:
            await self._fail(context, run_id, RunStatus.NOT_RUN, None, None)
            return
        if subject is None:
            await self._fail(context, run_id, RunStatus.NO_SUBJECT, provider.name, None)
            return
        try:
            result = await provider.screen(subject, key=run_id)
        except ProviderUnavailable:
            # Never chain the cause: a provider error can quote the client's name.
            raise RetryableJobError(code="screening_provider_unavailable") from None
        except ProviderRefused as refused:
            await self._fail(context, run_id, RunStatus.UNAVAILABLE, provider.name, refused.code)
            return
        await context.apply(self._record(run_id, provider.name, result))

    def _record(
        self, run_id: str, provider: str, result: ScreeningResult
    ) -> Callable[[AsyncSession], Awaitable[Mapping[str, object]]]:
        async def record(session: AsyncSession) -> Mapping[str, object]:
            run = await service.record_result(
                session, run_id, provider=provider, result=result, clock=self.clock
            )
            return {"status": run.status.value if run else "moved_on"}

        return record

    async def _fail(
        self,
        context: JobContext,
        run_id: str,
        status: RunStatus,
        provider: str | None,
        code: str | None,
    ) -> None:
        async def record(session: AsyncSession) -> Mapping[str, object]:
            await service.record_failure(
                session, run_id, status, provider=provider, failure_code=code, now=self.clock.now()
            )
            return {"status": status.value}

        await context.apply(record)

    async def rescreen_due(self, job: ClaimedJob, context: JobContext) -> None:
        """Ask for a screen of every approved client whose last one is a month old or more.

        Each page is its own transaction. The key names the client and the day, so a sweep run
        twice the same day asks once, and a client already waiting for a screen is skipped.
        """
        now = self.clock.now()
        today = business_date(now).isoformat()
        async with self.database.actor_transaction(SYSTEM) as session:
            gave_up = await service.give_up_on_stale(session, now)
        asked = 0
        after: str | None = None
        while asked < MAX_PER_SWEEP:
            async with self.database.actor_transaction(SYSTEM) as session:
                page = await self.clients(session, after, PAGE)
                if not page:
                    break
                last = await service.last_screened(session, page)
                for client_id in page:
                    seen = last.get(client_id)
                    if seen is not None and seen.pending:
                        continue
                    if not due_for_rescreen(seen.finished_at if seen else None, now):
                        continue
                    run_id = await service.request_screening(
                        session,
                        client_id=client_id,
                        reason=Reason.RESCREEN,
                        idempotency_key=f"rescreen:{client_id}:{today}",
                        now=now,
                    )
                    asked += run_id is not None
                    if asked >= MAX_PER_SWEEP:
                        break
            after = page[-1]

        async def done(session: AsyncSession) -> Mapping[str, object]:
            return {"asked": asked, "gave_up": gave_up}

        await context.apply(done)
