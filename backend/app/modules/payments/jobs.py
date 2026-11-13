"""The worker's side of a deposit: send the request, read the status back, sweep stragglers.

Each handler calls the provider with no transaction open, then records what it said in one
transaction that also completes the job. A request is sent at most once per deposit:

* The deposit is marked `sending`, and that is committed, before the provider is called. Only
  a deposit that is still `created` is ever sent.
* The send handler is registered as having an external effect, so the queue never runs it
  again after a crash or a lost lease. The deposit keeps its `sending` mark, and the sweep
  turns that into `uncertain`.
* When the answer is unclear the deposit becomes `uncertain`. Status reads may settle it, and
  reconciliation settles what they cannot. Nothing here ever sends it again.
"""

from collections.abc import Mapping
from datetime import datetime

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.jobs import (
    ClaimedJob,
    Handler,
    JobContext,
    JobHandler,
    JobRequest,
    PermanentJobError,
    RetryableJobError,
    enqueue,
    external_effect,
)
from app.infrastructure.jobs import queue as job_queue
from app.modules.payments import service
from app.modules.payments.domain.deposits import (
    APPROVAL_WINDOW,
    SENDING_GRACE,
    UNSENT_EXPIRY,
    Code,
    DepositStatus,
    Move,
    after_send,
)
from app.modules.payments.domain.outcomes import Uncertain, UncertainReason
from app.modules.payments.domain.provider import CollectionRequest, CollectionsProvider
from app.shared.actors import SYSTEM
from app.shared.clock import Clock

SWEEP_BATCH = 200
_OPEN = (DepositStatus.AWAITING_APPROVAL, DepositStatus.UNCERTAIN)
_STRAGGLERS = text(
    "SELECT id, status, created_at, updated_at FROM payment_deposits "
    "WHERE (status = 'created' AND created_at <= :unsent) "
    "OR (status = 'sending' AND updated_at <= :interrupted) "
    "OR (status = 'awaiting_approval' AND updated_at <= :quiet) "
    "ORDER BY updated_at LIMIT :limit FOR UPDATE SKIP LOCKED"
)
logger = structlog.get_logger()


class DepositJobs:
    def __init__(self, database: Database, provider: CollectionsProvider, clock: Clock) -> None:
        self._database = database
        self._provider = provider
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            service.SEND_KIND: external_effect(self.send),
            service.VERIFY_KIND: self.verify,
            service.SWEEP_KIND: self.sweep,
        }

    async def send(self, job: ClaimedJob, context: JobContext) -> None:
        deposit_id = _deposit_id(job.payload)
        async with self._database.actor_transaction(SYSTEM) as session:
            # Fenced: only the attempt that still owns the job may mark the deposit.
            await job_queue.lock_fenced(session, job)
            deposit = await service.find_deposit(session, deposit_id, lock=True)
            if deposit is None:
                raise PermanentJobError(code="deposit_not_found")
            if deposit.provider != self._provider.name:
                raise PermanentJobError(code="provider_mismatch")
            if deposit.status is not DepositStatus.CREATED:
                # Already marked or moved on. A marked deposit may have been sent, so it is
                # left for the sweep and a status read. It is not sent from here.
                return
            await service.apply_move(
                session, deposit, Move(DepositStatus.SENDING), self._clock, cause="send"
            )
        # The mark is committed. From here on this deposit is never sent by anyone else.
        try:
            result = await self._provider.request_collection(
                CollectionRequest(
                    request_id=deposit.id,
                    network=deposit.network,
                    msisdn=deposit.payer_msisdn,
                    amount=deposit.amount,
                )
            )
        except Exception as error:
            # An adapter must sort its own failures. If one escapes, the request may have gone
            # out, so the only safe reading is "we do not know".
            logger.error("collection_request_raised", error_type=type(error).__name__)
            result = Uncertain(UncertainReason.INTERRUPTED)
        move = after_send(result, last_attempt=job.attempt >= job.max_attempts)

        if move.status is DepositStatus.CREATED:
            # Nothing reached the provider. Put the deposit back and let the queue try later.
            async with self._database.actor_transaction(SYSTEM) as session:
                await job_queue.lock_fenced(session, job)
                await self._record_send(session, deposit_id, move)
            raise RetryableJobError(code="provider_deferred")

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            status = await self._record_send(session, deposit_id, move)
            return {"status": status}

        await context.apply(effect)

    async def _record_send(self, session: AsyncSession, deposit_id: str, move: Move) -> str:
        deposit = await service.find_deposit(session, deposit_id, lock=True)
        if deposit is None or deposit.status is not DepositStatus.SENDING:
            return "unchanged"
        moved = await service.apply_move(session, deposit, move, self._clock, cause="send")
        if moved.status in _OPEN:
            await service.schedule_status_read(session, deposit_id, self._clock, attempt=0)
        return moved.status.value

    async def verify(self, job: ClaimedJob, context: JobContext) -> None:
        attempt = job.payload.get("attempt")
        attempt = attempt if type(attempt) is int else None
        async with self._database.actor_transaction(SYSTEM) as session:
            deposit_id = job.payload.get("deposit_id")
            reference = job.payload.get("provider_reference")
            if not isinstance(deposit_id, str) and isinstance(reference, str):
                deposit_id = await service.deposit_id_for_reference(
                    session, self._provider.name, reference
                )
            deposit = (
                await service.find_deposit(session, deposit_id)
                if isinstance(deposit_id, str)
                else None
            )
        if deposit is None:
            # A callback about something we never asked for. Recorded already; nothing to do.
            logger.warning("deposit_notice_unmatched")
            return
        if deposit.status not in _OPEN or deposit.provider != self._provider.name:
            return
        reading = await self._provider.collection_status(
            request_id=deposit.id, provider_reference=deposit.provider_reference
        )
        found = deposit.id

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            settled = await service.settle_deposit(
                session, found, reading, self._clock, cause="status_read"
            )
            # Keep reading while the payer still has the prompt. A callback's read does not
            # start a second chain: the chain started when the request was sent.
            if settled.status is DepositStatus.AWAITING_APPROVAL and (
                attempt is not None or deposit.status is DepositStatus.UNCERTAIN
            ):
                await service.schedule_status_read(
                    session, found, self._clock, attempt=(attempt or 0) + 1
                )
            return {"status": settled.status.value}

        await context.apply(effect)

    async def sweep(self, job: ClaimedJob, context: JobContext) -> None:
        """Pick up deposits whose job was lost: a crash, an expired job or a long outage."""

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            now = self._clock.now()
            rows = await session.execute(
                _STRAGGLERS,
                {
                    "unsent": now - UNSENT_EXPIRY,
                    "interrupted": now - SENDING_GRACE,
                    "quiet": now - SENDING_GRACE,
                    "limit": SWEEP_BATCH,
                },
            )
            moved = 0
            for row in rows.all():
                moved += await self._sweep_one(session, row.id, job.idempotency_key, now)
            return {"moved": moved}

        await context.apply(effect)

    async def _sweep_one(
        self, session: AsyncSession, deposit_id: str, run: str, now: datetime
    ) -> int:
        deposit = await service.find_deposit(session, deposit_id, lock=True)
        if deposit is None:
            return 0
        if deposit.status is DepositStatus.CREATED:
            # Never marked `sending`, so never sent. Its send job has expired by now.
            await service.apply_move(
                session,
                deposit,
                Move(DepositStatus.FAILED, code=Code.PROVIDER_UNAVAILABLE.value),
                self._clock,
                cause="sweep",
            )
            return 1
        if deposit.status is DepositStatus.SENDING:
            # Marked and never answered: it may have been sent. Read, never resend.
            await service.apply_move(
                session,
                deposit,
                Move(DepositStatus.UNCERTAIN, code=Code.SEND_UNCERTAIN.value),
                self._clock,
                cause="sweep",
            )
        await enqueue(
            session,
            JobRequest(
                kind=service.VERIFY_KIND,
                idempotency_key=f"{deposit_id}:sweep:{run}",
                payload={"deposit_id": deposit_id},
                max_age=APPROVAL_WINDOW,
            ),
            now=now,
        )
        return 1


def _deposit_id(payload: Mapping[str, object]) -> str:
    value = payload.get("deposit_id")
    if not isinstance(value, str):
        raise PermanentJobError(code="invalid_payload")
    return value
