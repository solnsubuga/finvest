"""The worker's side of a card deposit: open the order, read it back, cancel it, sweep.

Each handler calls the card provider with no transaction open, then records what it said in one
transaction that also completes the job. An order is opened at most once per deposit:

* The deposit is marked `sending`, and that is committed, before the provider is called. Only a
  deposit that is still `created` is ever sent.
* The open and cancel handlers are registered as having an external effect, so the queue never
  runs them again after a crash or a lost lease.
* A deposit whose answer was lost, or that a crash left `sending`, fails: the payer can only pay
  on the page in the answer, which nobody ever received. It is never sent again.
* A page left unpaid for the card window is cancelled at the provider, then read once more. It
  fails only once the provider says it was not paid. When we cannot tell, it is `uncertain`.
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
from app.modules.identity.service import client_contact
from app.modules.payments import cards, service
from app.modules.payments.domain.cards import (
    CARD_WINDOW,
    CancelResult,
    CardOrderRequest,
    OrderOpened,
    after_card_send,
    card_window_over,
)
from app.modules.payments.domain.deposits import (
    SENDING_GRACE,
    UNCERTAIN_READ_WINDOW,
    UNSENT_EXPIRY,
    Code,
    DepositStatus,
    Method,
    Move,
    next_uncertain_read,
)
from app.modules.payments.domain.outcomes import (
    Pending,
    Rejected,
    RejectionCode,
    Uncertain,
    UncertainReason,
)
from app.modules.payments.domain.provider import CardCollectionsProvider
from app.shared.actors import SYSTEM
from app.shared.clock import Clock

SWEEP_BATCH = 200
DESCRIPTION = "Finvest investment"
_OPEN = (DepositStatus.AWAITING_APPROVAL, DepositStatus.UNCERTAIN)
_STRAGGLERS = text(
    "SELECT id FROM payment_deposits WHERE method = 'card' AND provider = :provider AND ("
    "(status = 'created' AND created_at <= :unsent) "
    "OR (status = 'sending' AND updated_at <= :interrupted) "
    "OR (status = 'awaiting_approval' AND updated_at <= :quiet)) "
    "ORDER BY updated_at LIMIT :limit FOR UPDATE SKIP LOCKED"
)
_UNCERTAIN = text(
    "SELECT id, updated_at FROM payment_deposits "
    "WHERE method = 'card' AND status = 'uncertain' AND code <> 'amount_mismatch' "
    "AND provider = :provider AND provider_reference IS NOT NULL "
    "AND updated_at <= :quiet AND updated_at > :given_up "
    "AND (updated_at, id) > (:after_at, :after_id) "
    "ORDER BY updated_at, id LIMIT :limit"
)
logger = structlog.get_logger()


class CardDepositJobs:
    def __init__(self, database: Database, provider: CardCollectionsProvider, clock: Clock) -> None:
        self._database = database
        self._provider = provider
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            cards.SEND_KIND: external_effect(self.send),
            cards.VERIFY_KIND: self.verify,
            cards.CANCEL_KIND: external_effect(self.cancel),
            cards.SWEEP_KIND: self.sweep,
        }

    # Opening the order ----------------------------------------------------------------------

    async def send(self, job: ClaimedJob, context: JobContext) -> None:
        deposit_id = _deposit_id(job.payload)
        async with self._database.actor_transaction(SYSTEM) as session:
            await job_queue.lock_fenced(session, job)
            deposit = await service.find_deposit(session, deposit_id, lock=True)
            if deposit is None:
                raise PermanentJobError(code="deposit_not_found")
            if deposit.provider != self._provider.name or deposit.method is not Method.CARD:
                raise PermanentJobError(code="provider_mismatch")
            order = await cards.find_order(session, deposit_id)
            contact = await client_contact(session, deposit.client_id)
            if order is None:
                raise PermanentJobError(code="card_order_missing")
            if deposit.status is not DepositStatus.CREATED:
                return
            if contact is None:
                # Locked or closed since asking. Nothing was sent, so no money moved.
                await service.apply_move(
                    session,
                    deposit,
                    Move(DepositStatus.FAILED, code=RejectionCode.OTHER.value),
                    self._clock,
                    cause="send",
                )
                return
            await service.apply_move(
                session, deposit, Move(DepositStatus.SENDING), self._clock, cause="send"
            )
        try:
            result = await self._provider.open_order(
                CardOrderRequest(
                    request_id=deposit.id,
                    total=order.total,
                    description=DESCRIPTION,
                    phone=contact.phone,
                )
            )
        except Exception as error:
            logger.error("card_order_raised", error_type=type(error).__name__)
            result = Uncertain(UncertainReason.INTERRUPTED)
        move = after_card_send(result, last_attempt=job.attempt >= job.max_attempts)
        page = result.page_url if isinstance(result, OrderOpened) else None

        if move.status is DepositStatus.CREATED:
            async with self._database.actor_transaction(SYSTEM) as session:
                await job_queue.lock_fenced(session, job)
                await self._record_send(session, deposit_id, move, None)
            raise RetryableJobError(code="provider_deferred")

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            status = await self._record_send(session, deposit_id, move, page)
            return {"status": status}

        await context.apply(effect)

    async def _record_send(
        self, session: AsyncSession, deposit_id: str, move: Move, page: str | None
    ) -> str:
        deposit = await service.find_deposit(session, deposit_id, lock=True)
        if deposit is None or deposit.status is not DepositStatus.SENDING:
            return "unchanged"
        if page is not None:
            # Saved before the move, so a client who sees `awaiting_approval` has the page.
            await cards.save_page(session, deposit_id, page)
        moved = await service.apply_move(session, deposit, move, self._clock, cause="send")
        if moved.status is DepositStatus.AWAITING_APPROVAL:
            await cards.schedule_card_read(session, deposit_id, self._clock, attempt=0)
        return moved.status.value

    # Reading the status ---------------------------------------------------------------------

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
            logger.warning("card_notice_unmatched")
            return
        if (
            deposit.status not in _OPEN
            or deposit.provider != self._provider.name
            or deposit.method is not Method.CARD
            or deposit.provider_reference is None
        ):
            return
        found = await self._provider.order_status(
            request_id=deposit.id, provider_reference=deposit.provider_reference
        )
        now = self._clock.now()

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            settled = await cards.settle_card_deposit(
                session, deposit.id, found.reading, found.details, self._clock, cause="status_read"
            )
            if settled.status is DepositStatus.AWAITING_APPROVAL:
                if card_window_over(settled.sent_at, now):
                    await _queue_cancel(session, settled.id, now)
                elif attempt is not None:
                    await cards.schedule_card_read(
                        session, settled.id, self._clock, attempt=attempt + 1
                    )
            return {"status": settled.status.value}

        await context.apply(effect)

    # Cancelling an order left unpaid ----------------------------------------------------------

    async def cancel(self, job: ClaimedJob, context: JobContext) -> None:
        deposit_id = _deposit_id(job.payload)
        async with self._database.actor_transaction(SYSTEM) as session:
            deposit = await service.find_deposit(session, deposit_id)
        if (
            deposit is None
            or deposit.status is not DepositStatus.AWAITING_APPROVAL
            or deposit.provider != self._provider.name
            or deposit.provider_reference is None
        ):
            return
        reference = deposit.provider_reference
        cancelled = await self._provider.cancel_order(provider_reference=reference)
        # Read after cancelling, whatever the answer: the payer may have paid at the last moment.
        found = await self._provider.order_status(
            request_id=deposit.id, provider_reference=reference
        )

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            reading = found.reading
            if cancelled is CancelResult.CANCELLED and isinstance(reading, Pending | Rejected):
                # The provider took the cancel and nothing was paid: nobody can pay now. The
                # provider may report the cancelled order as failed; the payer simply did not
                # pay in time.
                reading = Rejected(RejectionCode.APPROVAL_TIMED_OUT, reference)
            if isinstance(reading, Pending):
                # Still payable as far as we can tell. Read for a day, then reconciliation.
                locked = await service.find_deposit(session, deposit_id, lock=True)
                if locked is None or locked.status is not DepositStatus.AWAITING_APPROVAL:
                    return {"status": "unchanged"}
                moved = await service.apply_move(
                    session,
                    locked,
                    Move(DepositStatus.UNCERTAIN, code=Code.APPROVAL_OVERDUE.value),
                    self._clock,
                    cause="sweep",
                )
                return {"status": moved.status.value}
            settled = await cards.settle_card_deposit(
                session, deposit_id, reading, found.details, self._clock, cause="sweep"
            )
            return {"status": settled.status.value, "cancel": cancelled.value}

        await context.apply(effect)

    # Sweeping -------------------------------------------------------------------------------

    async def sweep(self, job: ClaimedJob, context: JobContext) -> None:
        """Pick up card deposits whose job was lost, and read every open one again."""

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            now = self._clock.now()
            rows = await session.execute(
                _STRAGGLERS,
                {
                    "provider": self._provider.name,
                    "unsent": now - UNSENT_EXPIRY,
                    "interrupted": now - SENDING_GRACE,
                    "quiet": now - SENDING_GRACE,
                    "limit": SWEEP_BATCH,
                },
            )
            moved = 0
            for row in rows.all():
                moved += await self._sweep_one(session, row.id, job.idempotency_key, now)
            given_up = now - UNCERTAIN_READ_WINDOW
            after_at, after_id = given_up, ""
            while True:
                page = (
                    await session.execute(
                        _UNCERTAIN,
                        {
                            "provider": self._provider.name,
                            "quiet": now - SENDING_GRACE,
                            "given_up": given_up,
                            "after_at": after_at,
                            "after_id": after_id,
                            "limit": SWEEP_BATCH,
                        },
                    )
                ).all()
                for row in page:
                    await _read_uncertain_again(session, row.id, row.updated_at, now)
                if len(page) < SWEEP_BATCH:
                    break
                after_at, after_id = page[-1].updated_at, page[-1].id
            return {"moved": moved}

        await context.apply(effect)

    async def _sweep_one(
        self, session: AsyncSession, deposit_id: str, run: str, now: datetime
    ) -> int:
        deposit = await service.find_deposit(session, deposit_id, lock=True)
        if deposit is None:
            return 0
        if deposit.status in (DepositStatus.CREATED, DepositStatus.SENDING):
            # Never sent, or sent with no answer saved: nobody has a page to pay on.
            await service.apply_move(
                session,
                deposit,
                Move(DepositStatus.FAILED, code=Code.PROVIDER_UNAVAILABLE.value),
                self._clock,
                cause="sweep",
            )
            return 1
        if deposit.sent_at is not None and now - deposit.sent_at >= 2 * CARD_WINDOW:
            # The cancel never settled it, for instance because its run was interrupted. It
            # may have been paid: only status reads and reconciliation decide from here.
            await service.apply_move(
                session,
                deposit,
                Move(DepositStatus.UNCERTAIN, code=Code.APPROVAL_OVERDUE.value),
                self._clock,
                cause="sweep",
            )
            return 1
        if card_window_over(deposit.sent_at, now):
            await _queue_cancel(session, deposit_id, now)
            return 1
        await enqueue(
            session,
            JobRequest(
                kind=cards.VERIFY_KIND,
                idempotency_key=f"{deposit_id}:card-sweep:{run}",
                payload={"deposit_id": deposit_id},
                max_age=CARD_WINDOW,
            ),
            now=now,
        )
        return 1


async def _queue_cancel(session: AsyncSession, deposit_id: str, now: datetime) -> None:
    """Queue the one cancel of an order left unpaid. Keyed by the deposit, so it runs once."""
    await enqueue(
        session,
        JobRequest(
            kind=cards.CANCEL_KIND,
            idempotency_key=f"{deposit_id}:card-cancel",
            payload={"deposit_id": deposit_id},
            max_attempts=1,
            max_age=CARD_WINDOW,
        ),
        now=now,
    )


async def _read_uncertain_again(
    session: AsyncSession, deposit_id: str, since: datetime, now: datetime
) -> None:
    upcoming = next_uncertain_read(since, now)
    if upcoming is None:
        return
    number, due = upcoming
    await enqueue(
        session,
        JobRequest(
            kind=cards.VERIFY_KIND,
            idempotency_key=f"{deposit_id}:card-uncertain:{int(since.timestamp())}:{number}",
            payload={"deposit_id": deposit_id},
            delay=due - now,
            max_age=(due - now) + CARD_WINDOW,
        ),
        now=now,
    )


def _deposit_id(payload: Mapping[str, object]) -> str:
    value = payload.get("deposit_id")
    if not isinstance(value, str):
        raise PermanentJobError(code="invalid_payload")
    return value
