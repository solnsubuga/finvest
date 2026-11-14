"""The worker's side of keeping the Cloudflare Access list in step with the team.

Each attempt finds out before it changes anything. It reads what the team says now, asks
Cloudflare whether the address is on the list, adds or removes it only if it is not where it
should be, and asks again. An attempt that timed out half way is therefore safe to run again:
the next one starts by asking what happened. That is why this handler is not registered as
`external_effect`: there is no blind second send to guard against, and an address already on
the list or already gone counts as done.

The result is recorded in the transaction that completes the job, and only if the team still
says the same thing; otherwise the job runs again. A refusal (a wrong token, a missing list) or
the last failed attempt marks the member `failed`, and an admin can ask for it to be tried again
from the team view. Logs carry staff IDs and codes, never the address.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import structlog
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
from app.modules.identity import staff_access
from app.modules.identity.domain.staff_access import (
    AccessRefused,
    AccessState,
    AccessUnavailable,
    StaffAccessList,
    Target,
)
from app.shared.actors import SYSTEM
from app.shared.business_day import KAMPALA
from app.shared.clock import Clock

logger = structlog.get_logger()


@dataclass(frozen=True)
class StaffAccessJobs:
    database: Database
    # None until the worker has the Cloudflare account, list and token.
    access_list: StaffAccessList | None
    clock: Clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {staff_access.SYNC_KIND: self.sync, staff_access.CHECK_KIND: self.check}

    async def sync(self, job: ClaimedJob, context: JobContext) -> None:
        staff_id = job.payload.get("staff_id")
        if not isinstance(staff_id, str):
            raise PermanentJobError(code="invalid_payload")
        async with self.database.actor_transaction(SYSTEM) as session:
            wanted = await staff_access.target(session, staff_id)
        if wanted is None:
            raise PermanentJobError(code="staff_not_found")
        if self.access_list is None:

            async def not_set_up(session: AsyncSession) -> Mapping[str, object]:
                await staff_access.record_problem(
                    session,
                    staff_id,
                    state=AccessState.NOT_SET_UP,
                    error_code=None,
                    now=self.clock.now(),
                )
                return {"outcome": AccessState.NOT_SET_UP.value}

            await context.apply(not_set_up)
            return
        try:
            await self._bring_in_line(self.access_list, wanted)
        except AccessUnavailable as problem:
            last = job.attempt >= job.max_attempts
            await self._note(staff_id, AccessState.FAILED if last else AccessState.WAITING, problem)
            raise RetryableJobError(code=problem.code) from None
        except AccessRefused as problem:
            await self._note(staff_id, AccessState.FAILED, problem)
            raise PermanentJobError(code=problem.code) from None

        async def settled(session: AsyncSession) -> Mapping[str, object]:
            state = await staff_access.record_settled(
                session, staff_id, on_list=wanted.on_list, now=self.clock.now()
            )
            return {"outcome": state.value}

        await context.apply(settled)
        logger.info("staff_access_synced", staff_id=staff_id, on_list=wanted.on_list)

    async def check(self, job: ClaimedJob, context: JobContext) -> None:
        """Queue a sync for every member of staff, at most once a day each."""
        today = self.clock.now().astimezone(KAMPALA).date().isoformat()

        async def queue_all(session: AsyncSession) -> Mapping[str, object]:
            ids = await staff_access.everyone(session)
            for staff_id in ids:
                await staff_access.request_sync(
                    session,
                    staff_id,
                    key=f"check:{today}:{staff_id}",
                    now=self.clock.now(),
                    mark_waiting=False,
                )
            return {"queued": len(ids)}

        await context.apply(queue_all)

    async def _bring_in_line(self, access_list: StaffAccessList, wanted: Target) -> None:
        on_list = await access_list.contains(wanted.email)
        if on_list == wanted.on_list:
            return
        refusal: AccessRefused | None = None
        try:
            if wanted.on_list:
                await access_list.add(wanted.email)
            else:
                await access_list.remove(wanted.email)
        except AccessRefused as refused:
            # An address already there, or already gone, may be refused as a duplicate. The
            # list is asked again below, so only a refusal that left it wrong counts.
            if refused.code != "cloudflare_refused":
                raise
            refusal = refused
        if await access_list.contains(wanted.email) != wanted.on_list:
            # Refused and still wrong is a refusal. Accepted and not showing yet is tried again.
            raise refusal or AccessUnavailable("access_not_confirmed")

    async def _note(
        self, staff_id: str, state: AccessState, problem: AccessUnavailable | AccessRefused
    ) -> None:
        async with self.database.actor_transaction(SYSTEM) as session:
            await staff_access.record_problem(
                session, staff_id, state=state, error_code=problem.code, now=self.clock.now()
            )
        logger.warning("staff_access_not_synced", staff_id=staff_id, error_code=problem.code)
