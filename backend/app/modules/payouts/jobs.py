"""The worker's side of a payout: send it, read its status back, sweep up what was lost.

Each handler calls the provider with no transaction open, then records what it said in one
transaction that also completes the job. A payout is sent at most once:

* A send attempt is saved and committed before the provider is called. The database allows
  only one attempt per payout that did not end `deferred`, and a payout leaves the queue for
  sent, paid, failed or uncertain only through such an attempt.
* The send handler is registered as having an external effect, so the queue never runs it
  again after a crash or a lost lease. The attempt stays open, and the sweep marks it
  `interrupted` and the payout uncertain.
* An uncertain payout is only ever read. Reads go on for `READ_WINDOW`, further apart as they
  go, and two staff members settle from the statement what the provider cannot answer.

Logs carry payout IDs and codes only: never the number paid, a name or an amount.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import Row, text
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
from app.modules.ledger.domain.errors import InsufficientFunds
from app.modules.notifications import contract as notifications
from app.modules.payments.domain.deposits import Network
from app.modules.payments.domain.outcomes import SendResult, Uncertain, UncertainReason
from app.modules.payouts import destinations, payouts
from app.modules.payouts.domain.destinations import DestinationKind
from app.modules.payouts.domain.payouts import (
    PayoutEvent,
    PayoutStatus,
    may_send,
    needs_status_read,
)
from app.modules.payouts.domain.provider import DisbursementRequest, DisbursementsProvider
from app.modules.payouts.domain.sending import (
    AMOUNT_MISMATCH,
    READ_WINDOW,
    RESEND_AFTER,
    SEND_GRACE,
    AttemptOutcome,
    SendStep,
    after_send,
    after_status,
    destination_label,
    next_read,
)
from app.modules.payouts.payouts import PayoutView
from app.shared.actors import SYSTEM
from app.shared.clock import Clock

SWEEP_BATCH = 200
# A sent or uncertain payout that nothing has read for this long gets a read from the sweep.
QUIET_READS = timedelta(hours=1)

_LOCK_PAYOUT = text("SELECT id FROM payouts WHERE id = :id FOR UPDATE")
_ATTEMPT_COLUMNS = "payout_id, attempt, provider, started_at, outcome, provider_reference"
_LAST_ATTEMPT = text(
    f"SELECT {_ATTEMPT_COLUMNS} FROM payout_send_attempts "  # noqa: S608 - constant columns
    "WHERE payout_id = :payout_id ORDER BY attempt DESC LIMIT 1"
)
_START = text(
    "INSERT INTO payout_send_attempts (payout_id, attempt, provider, started_at) "
    "VALUES (:payout_id, :attempt, :provider, :now)"
)
_FINISH = text(
    "UPDATE payout_send_attempts SET outcome = :outcome, finished_at = :now, "
    "provider_reference = :provider_reference "
    "WHERE payout_id = :payout_id AND attempt = :attempt AND outcome IS NULL"
)
# Attempts that never had their answer saved. Locked before the payout, as the send does.
_INTERRUPTED = text(
    "SELECT a.payout_id, a.attempt FROM payout_send_attempts a "
    "JOIN payouts p ON p.id = a.payout_id "
    "WHERE a.outcome IS NULL AND a.started_at <= :interrupted AND p.status = 'queued' "
    "ORDER BY a.started_at LIMIT :limit FOR UPDATE OF a SKIP LOCKED"
)
# Queued payouts whose send job was lost, or whose provider took nothing every time.
_UNSENT = text(
    "SELECT p.id, (SELECT count(*) FROM payout_send_attempts a WHERE a.payout_id = p.id) "
    "AS attempts FROM payouts p WHERE p.status = 'queued' AND p.kind = 'mobile_money' "
    "AND p.updated_at <= :quiet AND NOT EXISTS (SELECT FROM payout_send_attempts a "
    "WHERE a.payout_id = p.id AND (a.outcome IS NULL OR a.outcome <> 'deferred')) "
    "ORDER BY p.updated_at LIMIT :limit"
)
# Sent or uncertain payouts nothing has read for a while, still inside the read window.
_UNREAD = text(
    "SELECT p.id FROM payouts p JOIN payout_send_attempts a ON a.payout_id = p.id "
    "AND (a.outcome IS NULL OR a.outcome <> 'deferred') "
    "WHERE p.status IN ('sent', 'uncertain') AND p.updated_at <= :quiet "
    "AND a.provider = :provider AND a.started_at > :given_up "
    "ORDER BY p.updated_at LIMIT :limit"
)
logger = structlog.get_logger()


class PayoutJobs:
    def __init__(self, database: Database, provider: DisbursementsProvider, clock: Clock) -> None:
        self._database = database
        self._provider = provider
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            payouts.SEND_KIND: external_effect(self.send),
            payouts.READ_KIND: self.read,
            payouts.SWEEP_KIND: self.sweep,
        }

    async def send(self, job: ClaimedJob, context: JobContext) -> None:
        payout_id = _payout_id(job.payload)
        async with self._database.actor_transaction(SYSTEM) as session:
            # Fenced: only the attempt that still owns the job may start a send.
            await job_queue.lock_fenced(session, job)
            payout = await _locked_payout(session, payout_id)
            if payout is None:
                raise PermanentJobError(code="payout_not_found")
            if not may_send(payout.status):
                return
            if payout.kind is not DestinationKind.MOBILE_MONEY:
                raise PermanentJobError(code="not_mobile_money")
            last = (await session.execute(_LAST_ATTEMPT, {"payout_id": payout_id})).one_or_none()
            if last is not None and last.outcome != AttemptOutcome.DEFERRED:
                # An open attempt may have gone. The sweep settles it; it is not sent again.
                return
            network, msisdn = await destinations.mobile_money_number(session, payout.destination_id)
            attempt = (last.attempt if last is not None else 0) + 1
            await session.execute(
                _START,
                {
                    "payout_id": payout_id,
                    "attempt": attempt,
                    "provider": self._provider.name,
                    "now": self._clock.now(),
                },
            )
        # The attempt is committed. From here on this payout is never sent by anyone else.
        result: SendResult
        try:
            result = await self._provider.send_disbursement(
                DisbursementRequest(
                    request_id=payout.id,
                    network=Network(network),
                    msisdn=msisdn,
                    amount=payout.amount,
                )
            )
        except Exception as error:
            # An adapter must sort its own failures. If one escapes, the money may have gone,
            # so the only safe reading is "we do not know".
            logger.error("disbursement_request_raised", error_type=type(error).__name__)
            result = Uncertain(UncertainReason.INTERRUPTED)
        step = after_send(result)

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            return {"status": await self._record_send(session, payout_id, attempt, step)}

        if step.outcome is AttemptOutcome.DEFERRED and job.attempt < job.max_attempts:
            # Nothing reached the provider. Save that, and let the queue try later.
            async with self._database.actor_transaction(SYSTEM) as session:
                await job_queue.lock_fenced(session, job)
                await effect(session)
            raise RetryableJobError(code="provider_deferred")
        # A last deferral leaves the payout queued, and the sweep queues a fresh send later.
        await context.apply(effect)

    async def _record_send(
        self, session: AsyncSession, payout_id: str, attempt: int, step: SendStep
    ) -> str:
        now = self._clock.now()
        finished = await finish_attempt(
            session, payout_id, attempt, step.outcome, now, step.provider_reference
        )
        if not finished:
            # The sweep found this attempt first and made the payout uncertain. Its reads will
            # find what the provider has.
            return "unchanged"
        moved = await payouts.apply_event(
            session,
            SYSTEM,
            payout_id,
            step.event,
            self._clock,
            provider_reference=step.provider_reference,
            failure_code=step.failure_code,
        )
        await self._after_move(session, moved, read_number=0, sent_at=now)
        return moved.status.value

    async def read(self, job: ClaimedJob, context: JobContext) -> None:
        payout_id = _payout_id(job.payload)
        number = job.payload.get("number")
        number = number if type(number) is int else None
        async with self._database.actor_transaction(SYSTEM) as session:
            payout = await _find_payout(session, payout_id)
            last = (await session.execute(_LAST_ATTEMPT, {"payout_id": payout_id})).one_or_none()
        if payout is None or not needs_status_read(payout.status) or last is None:
            return
        if last.provider != self._provider.name:
            # Sent through another provider. Only that provider can say how it ended.
            return
        reading = await self._provider.disbursement_status(
            request_id=payout.id,
            provider_reference=payout.provider_reference or last.provider_reference,
        )
        step = after_status(reading, payout.amount)
        sent_at: datetime = last.started_at

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            current = await _locked_payout(session, payout_id)
            if current is None or not needs_status_read(current.status):
                return {"status": "unchanged"}
            if step.failure_code == AMOUNT_MISMATCH:
                # A confirmation for another amount is for two staff members, not a machine.
                logger.error("payout_amount_mismatch", payout_id=payout_id)
                return {"status": current.status.value, "code": AMOUNT_MISMATCH}
            if step.event is None:
                return {"status": current.status.value}
            try:
                moved = await payouts.apply_event(
                    session,
                    SYSTEM,
                    payout_id,
                    step.event,
                    self._clock,
                    provider_reference=step.provider_reference,
                    failure_code=step.failure_code,
                )
            except InsufficientFunds:
                # The pot does not hold what the provider says it paid. That is for
                # reconciliation to look at, never something to force.
                raise PermanentJobError(code="pot_short") from None
            # A read the sweep queued has no number and does not start a second chain.
            if step.read_again and number is not None:
                await self._read_later(session, payout_id, number + 1, sent_at)
            await self._tell_client(session, moved)
            return {"status": moved.status.value}

        await context.apply(effect)

    async def sweep(self, job: ClaimedJob, context: JobContext) -> None:
        """Settle sends that were interrupted, and queue sends and reads that were lost."""

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            now = self._clock.now()
            interrupted = (
                await session.execute(
                    _INTERRUPTED, {"interrupted": now - SEND_GRACE, "limit": SWEEP_BATCH}
                )
            ).all()
            for row in interrupted:
                await finish_attempt(
                    session, row.payout_id, row.attempt, AttemptOutcome.INTERRUPTED, now
                )
                moved = await payouts.apply_event(
                    session, SYSTEM, row.payout_id, PayoutEvent.SEND_UNCERTAIN, self._clock
                )
                await self._after_move(session, moved, read_number=0, sent_at=now)
            unsent = (
                await session.execute(_UNSENT, {"quiet": now - RESEND_AFTER, "limit": SWEEP_BATCH})
            ).all()
            hour = now.strftime("%Y%m%d%H")
            for row in unsent:
                # One fresh send per payout per hour, whatever happened to the last one.
                await payouts.queue_send(
                    session,
                    row.id,
                    DestinationKind.MOBILE_MONEY,
                    now,
                    key=f"{row.id}:resend:{row.attempts}:{hour}",
                )
            unread = (
                await session.execute(
                    _UNREAD,
                    {
                        "quiet": now - QUIET_READS,
                        "provider": self._provider.name,
                        "given_up": now - READ_WINDOW,
                        "limit": SWEEP_BATCH,
                    },
                )
            ).all()
            for row in unread:
                await enqueue(
                    session,
                    JobRequest(
                        kind=payouts.READ_KIND,
                        idempotency_key=f"{row.id}:sweep-read:{hour}",
                        payload={"payout_id": row.id},
                        max_age=QUIET_READS,
                    ),
                    now=now,
                )
            return {
                "interrupted": len(interrupted),
                "resent": len(unsent),
                "read": len(unread),
            }

        await context.apply(effect)

    async def _after_move(
        self, session: AsyncSession, moved: PayoutView, *, read_number: int, sent_at: datetime
    ) -> None:
        if needs_status_read(moved.status):
            await self._read_later(session, moved.id, read_number, sent_at)
        await self._tell_client(session, moved)

    async def _read_later(
        self, session: AsyncSession, payout_id: str, number: int, sent_at: datetime
    ) -> None:
        now = self._clock.now()
        due = next_read(number, sent_at, now)
        if due is None:
            logger.warning("payout_reads_given_up", payout_id=payout_id)
            return
        await enqueue(
            session,
            JobRequest(
                kind=payouts.READ_KIND,
                idempotency_key=f"{payout_id}:read:{number}",
                payload={"payout_id": payout_id, "number": number},
                delay=due - now,
                max_age=(due - now) + QUIET_READS,
            ),
            now=now,
        )

    async def _tell_client(self, session: AsyncSession, payout: PayoutView) -> None:
        event = _CLIENT_EVENTS.get(payout.status)
        if event is None:
            return
        destination = await destinations.get_destination(session, payout.destination_id)
        await notifications.notify(
            session,
            event_name=event,
            client_id=payout.client_id,
            idempotency_key=payout.id,
            variables={
                "amount": payout.amount,
                "destination": destination_label(destination.network or ""),
            },
            link=notifications.Link("payout", payout.id),
            now=self._clock.now(),
        )


# What the client is told at each step. A payout that is being read is "being processed",
# whether the provider has it or we are still finding out.
_CLIENT_EVENTS = {
    PayoutStatus.SENT: "payout_processing",
    PayoutStatus.UNCERTAIN: "payout_processing",
    PayoutStatus.PAID: "payout_sent",
    PayoutStatus.FAILED: "payout_failed",
}


async def start_attempt(session: AsyncSession, payout_id: str, provider: str, now: datetime) -> int:
    """Save a new send attempt for a queued payout and return its number."""
    last = (await session.execute(_LAST_ATTEMPT, {"payout_id": payout_id})).one_or_none()
    attempt = (last.attempt if last is not None else 0) + 1
    await session.execute(
        _START, {"payout_id": payout_id, "attempt": attempt, "provider": provider, "now": now}
    )
    return int(attempt)


async def finish_attempt(
    session: AsyncSession,
    payout_id: str,
    attempt: int,
    outcome: AttemptOutcome,
    now: datetime,
    provider_reference: str | None = None,
) -> bool:
    """Save how an attempt ended. False if its end was already saved, which changes nothing."""
    result = await session.execute(
        _FINISH,
        {
            "payout_id": payout_id,
            "attempt": attempt,
            "outcome": outcome.value,
            "provider_reference": provider_reference,
            "now": now,
        },
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def _locked_payout(session: AsyncSession, payout_id: str) -> PayoutView | None:
    if (await session.execute(_LOCK_PAYOUT, {"id": payout_id})).one_or_none() is None:
        return None
    return await payouts.get_payout(session, payout_id)


async def _find_payout(session: AsyncSession, payout_id: str) -> PayoutView | None:
    row: Row[Any] | None = (
        await session.execute(text("SELECT id FROM payouts WHERE id = :id"), {"id": payout_id})
    ).one_or_none()
    return None if row is None else await payouts.get_payout(session, payout_id)


def _payout_id(payload: Mapping[str, object]) -> str:
    value = payload.get("payout_id")
    if not isinstance(value, str):
        raise PermanentJobError(code="invalid_payload")
    return value
