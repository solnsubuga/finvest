"""Paying a card deposit back to the card, by two people. The rules are in
`domain/provider_refunds.py`, and the database holds all of this (migration 0482).

1. One member of staff proposes paying a confirmed card deposit back, with a reason, and says
   whether it is Finvest's mistake. Only then does the card charge go back too. The whole
   amount must still wait in the client's `client.awaiting_start` account for the investment.
2. A different member of staff approves, and the worker's job is queued in that transaction.
   Or the second person sends it back with a note, or the proposer withdraws it.
3. The worker locks the investment, as the start of an investment does, checks everything
   again, holds the money and marks the refund `sending`, and commits:

       debit   client.awaiting_start            the client, the investment   the amount
       debit   payments.card_charges            when Finvest erred           the charge
       credit  payments.provider_refunds_due    the refund                   the total

   Then it asks the provider once, with no transaction open. A yes is `requested` and the
   client is told. A no, or a request that never left, releases the hold exactly and the
   refund `failed`: the money waits for the investment again. A lost answer is `uncertain`.
4. A requested refund is `done` when the provider reads the payment as reversed, or when a
   member of staff other than the proposer sees it on the provider's statement and says so.
   Staff also say what they found for an uncertain one. The worker carries out what they found:

       debit   payments.provider_refunds_due    the total
       credit  the provider's balance           the total

   and marks the deposit refunded in the same transaction, so it no longer counts as the
   investment's payment: the client can pay again, or the investment is cancelled as unpaid.

Payments never writes to investments. The staff functions run in the staff member's own
transaction and commit nothing; the worker's handlers open their own.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.idempotency import Receipt, run_once
from app.infrastructure.jobs import (
    ClaimedJob,
    Handler,
    JobContext,
    JobHandler,
    JobRequest,
    PermanentJobError,
    enqueue,
    external_effect,
)
from app.infrastructure.jobs import queue as job_queue
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.investments import service as investments
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import (
    AccountKind,
    AccountSpec,
    EntryDraft,
    Line,
    credit,
    debit,
)
from app.modules.ledger.domain.errors import InsufficientFunds
from app.modules.payments import cards, service
from app.modules.payments.domain.provider import CardCollectionsProvider
from app.modules.payments.domain.provider_refunds import (
    Facts,
    RefundRequest,
    RefundStatus,
    Refusal,
    RequestResult,
    Settlement,
    after_request,
    after_settlement,
    failure_code,
    refund_total,
    refusal,
)
from app.modules.payments.domain.reassignments import clean_text
from app.modules.payments.service import Deposit
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

AWAITING_START = "client.awaiting_start"
DUE_CODE = "payments.provider_refunds_due"
HELD_ENTRY_TYPE = "payment.provider_refund_held"
RELEASED_ENTRY_TYPE = "payment.provider_refund_released"
PAID_ENTRY_TYPE = "payment.provider_refund_paid"
SOURCE_TYPE = "payment.provider_refund"
SEND_KIND = "payments.send_provider_refund"
SETTLE_KIND = "payments.settle_provider_refund"
READ_KIND = "payments.read_provider_refunds"
RESOURCE = "payment_provider_refund"
PROPOSE_OPERATION = "payments.propose_provider_refund"
APPROVE_OPERATION = "payments.approve_provider_refund"
SEND_BACK_OPERATION = "payments.send_back_provider_refund"
WITHDRAW_OPERATION = "payments.withdraw_provider_refund"
SETTLE_OPERATION = "payments.settle_provider_refund"
# What Pesapal is told about why. Never the staff member's reason, which may name the client.
REMARKS = "Refund of a Finvest investment payment"
# A refund left `sending` this long was interrupted between holding and hearing back.
SENDING_GRACE = timedelta(minutes=10)
READ_BATCH = 100
MAX_LISTED = 200
logger = structlog.get_logger()

_COLUMNS = (
    "id, deposit_id, client_id, investment_id, provider, currency, amount_minor, charge_minor, "
    "finvest_erred, total_minor, reason, status, proposed_by, proposed_at, decided_by, "
    "decided_at, decision_note, hold_entry_id, sent_at, settlement, settled_by, settled_at, "
    "settlement_note, done_entry_id, release_entry_id, done_at, failure_code"
)
_TABLE = "payment_provider_refunds"
_BY_ID = text(f"SELECT {_COLUMNS} FROM {_TABLE} WHERE id = :id")  # noqa: S608
_BY_ID_LOCKED = text(f"SELECT {_COLUMNS} FROM {_TABLE} WHERE id = :id FOR UPDATE")  # noqa: S608
_LIST = text(
    f"SELECT {_COLUMNS} FROM {_TABLE} "  # noqa: S608 - constant column list
    "WHERE (CAST(:status AS text) IS NULL OR status = :status) "
    "AND (CAST(:deposit_id AS text) IS NULL OR deposit_id = :deposit_id) "
    "ORDER BY proposed_at DESC, id DESC LIMIT :limit"
)
_INSERT = text(
    f"INSERT INTO {_TABLE} (id, deposit_id, client_id, investment_id, provider, currency, "  # noqa: S608
    "amount_minor, charge_minor, finvest_erred, total_minor, reason, proposed_by, proposed_at) "
    "VALUES (:id, :deposit_id, :client_id, :investment_id, :provider, :currency, :amount_minor, "
    ":charge_minor, :finvest_erred, :total_minor, :reason, :proposed_by, :now)"
)
_DECIDE = text(
    f"UPDATE {_TABLE} SET status = :status, decided_by = :decided_by, "  # noqa: S608
    "decided_at = :now, decision_note = :note WHERE id = :id"
)
_SETTLE = text(
    f"UPDATE {_TABLE} SET settlement = :settlement, settled_by = :settled_by, "  # noqa: S608
    "settled_at = :now, settlement_note = :note WHERE id = :id"
)
_HOLD = text(
    f"UPDATE {_TABLE} SET status = 'sending', hold_entry_id = :entry_id, "  # noqa: S608
    "sent_at = :now WHERE id = :id"
)
_MOVE = text(f"UPDATE {_TABLE} SET status = :status WHERE id = :id")  # noqa: S608
_FINISH = text(
    f"UPDATE {_TABLE} SET status = :status, done_at = :now, "  # noqa: S608
    "done_entry_id = :done_entry_id, release_entry_id = :release_entry_id, "
    "failure_code = :failure_code WHERE id = :id"
)
_TO_READ = text(
    f"SELECT id, status FROM {_TABLE} WHERE provider = :provider AND ("  # noqa: S608
    "status = 'requested' OR (status = 'sending' AND sent_at <= :interrupted)) "
    "ORDER BY sent_at, id LIMIT :limit"
)
_MARK_DEPOSIT = text("UPDATE payment_deposits SET provider_refund_id = :refund_id WHERE id = :id")


@dataclass(frozen=True, slots=True)
class Refund:
    id: str
    deposit_id: str
    client_id: str
    investment_id: str
    provider: str
    amount: Money
    charge: Money
    finvest_erred: bool
    # What goes back to the card: the amount, and the charge when Finvest erred.
    total: Money
    reason: str
    status: RefundStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    sent_at: datetime | None
    settlement: Settlement | None
    settled_by: str | None
    settled_at: datetime | None
    settlement_note: str | None
    done_at: datetime | None
    hold_entry_id: str | None
    done_entry_id: str | None
    release_entry_id: str | None
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class RefundDetail:
    """A refund with what the second person checks before approving."""

    refund: Refund
    deposit: Deposit
    # The card that paid, as the client sees it.
    card_brand: str | None
    card_last_four: str | None
    # What waits for the investment now, which must be at least the deposit's amount.
    waiting: Money
    # For an open proposal: why approving would be refused now. None if it would go through.
    refusal: Refusal | None


# Staff ------------------------------------------------------------------------------------------


async def propose_refund(
    session: AsyncSession,
    staff: Actor,
    *,
    deposit_id: str,
    reason: str,
    finvest_erred: bool,
    idempotency_key: str,
    clock: Clock,
) -> Refund:
    """Ask a second member of staff to pay a card deposit back to the card.

    `finvest_erred` says the refund is Finvest's own mistake, which also pays back the card
    charge. Refused with `validation_failed` and a code from `Refusal` if it could not happen
    now, and `conflict` if the deposit already has a live refund.
    """
    _staff_only(staff)
    cleaned = clean_text(reason)
    if cleaned is None:
        raise _invalid("reason", "required")
    assert staff.id is not None
    now = clock.now()

    async def command() -> Receipt:
        deposit = await _deposit(session, deposit_id)
        facts, order = await _facts(session, deposit, clock)
        problem = refusal(facts)
        if problem is not None:
            raise _refused(problem)
        assert order is not None
        total = refund_total(deposit.amount, order.charge, finvest_erred=finvest_erred)
        refund_id = push_id()
        try:
            async with session.begin_nested():
                await session.execute(
                    _INSERT,
                    {
                        "id": refund_id,
                        "deposit_id": deposit.id,
                        "client_id": deposit.client_id,
                        "investment_id": deposit.purpose_id,
                        "provider": deposit.provider,
                        "currency": deposit.amount.currency.value,
                        "amount_minor": deposit.amount.minor,
                        "charge_minor": order.charge.minor,
                        "finvest_erred": finvest_erred,
                        "total_minor": total.minor,
                        "reason": cleaned,
                        "proposed_by": staff.id,
                        "now": now,
                    },
                )
        except IntegrityError as error:
            # Another refund of this deposit got there first.
            raise ApplicationError("conflict") from error
        proposed = await _refund(session, refund_id)
        await _audit(session, staff, "change", "proposed", proposed, clock, reason=cleaned)
        return Receipt(201, {"refund_id": refund_id})

    outcome = await run_once(
        session,
        actor=staff,
        operation=PROPOSE_OPERATION,
        key=idempotency_key,
        request={"deposit_id": deposit_id, "reason": cleaned, "finvest_erred": finvest_erred},
        now=now,
        command=command,
    )
    return await _refund(session, str(outcome.receipt.response["refund_id"]))


async def approve_refund(
    session: AsyncSession, staff: Actor, refund_id: str, *, idempotency_key: str, clock: Clock
) -> Refund:
    """Approve another person's proposal. The worker's job is queued in this transaction.

    `forbidden` for the proposer. `validation_failed` with a `Refusal` code if the money moved
    on since it was proposed; the proposal stays open, to be sent back or withdrawn.
    """
    _staff_only(staff)
    now = clock.now()

    async def command() -> Receipt:
        refund = await _open(session, refund_id)
        if refund.proposed_by == staff.id:
            raise ApplicationError("forbidden")
        facts, _ = await _facts(session, await _deposit(session, refund.deposit_id), clock)
        problem = refusal(facts)
        if problem is not None:
            raise _refused(problem)
        await _decide(session, staff, refund, RefundStatus.APPROVED, clock, None)
        await enqueue(
            session,
            JobRequest(
                kind=SEND_KIND,
                idempotency_key=refund.id,
                payload={"refund_id": refund.id},
                max_attempts=1,
            ),
            now=now,
        )
        approved = await _refund(session, refund.id)
        await _audit(
            session, staff, "approval", "approved", approved, clock, reason="second_person_approval"
        )
        return Receipt(200, {"refund_id": refund.id})

    await run_once(
        session,
        actor=staff,
        operation=APPROVE_OPERATION,
        key=idempotency_key,
        request={"refund_id": refund_id},
        now=now,
        command=command,
    )
    return await _refund(session, refund_id)


async def send_back_refund(
    session: AsyncSession,
    staff: Actor,
    refund_id: str,
    note: str,
    *,
    idempotency_key: str,
    clock: Clock,
) -> Refund:
    """Decline another person's proposal, with a note for them. Nothing moves."""
    _staff_only(staff)
    cleaned = clean_text(note)
    if cleaned is None:
        raise _invalid("note", "required")

    async def command() -> Receipt:
        refund = await _open(session, refund_id)
        if refund.proposed_by == staff.id:
            raise ApplicationError("forbidden")
        await _decide(session, staff, refund, RefundStatus.SENT_BACK, clock, cleaned)
        sent_back = await _refund(session, refund.id)
        await _audit(session, staff, "approval", "sent_back", sent_back, clock, reason=cleaned)
        return Receipt(200, {"refund_id": refund.id})

    await run_once(
        session,
        actor=staff,
        operation=SEND_BACK_OPERATION,
        key=idempotency_key,
        request={"refund_id": refund_id, "note": cleaned},
        now=clock.now(),
        command=command,
    )
    return await _refund(session, refund_id)


async def withdraw_refund(
    session: AsyncSession, staff: Actor, refund_id: str, *, idempotency_key: str, clock: Clock
) -> Refund:
    """Take back your own proposal before anyone decides it. Nothing moves."""
    _staff_only(staff)

    async def command() -> Receipt:
        refund = await _open(session, refund_id)
        if refund.proposed_by != staff.id:
            raise ApplicationError("forbidden")
        await _decide(session, staff, refund, RefundStatus.WITHDRAWN, clock, None)
        withdrawn = await _refund(session, refund.id)
        await _audit(session, staff, "change", "withdrawn", withdrawn, clock)
        return Receipt(200, {"refund_id": refund.id})

    await run_once(
        session,
        actor=staff,
        operation=WITHDRAW_OPERATION,
        key=idempotency_key,
        request={"refund_id": refund_id},
        now=clock.now(),
        command=command,
    )
    return await _refund(session, refund_id)


async def record_finding(
    session: AsyncSession,
    staff: Actor,
    refund_id: str,
    found: Settlement,
    note: str,
    *,
    idempotency_key: str,
    clock: Clock,
) -> Refund:
    """Say what the provider shows for a refund the worker could not finish, with a note of
    where it was seen. The worker carries it out moments later; nothing moves here.

    `forbidden` for the proposer. `conflict` if the refund is not waiting on the provider, or
    the finding is not one it can take (a requested refund is found paid or not paid).
    """
    _staff_only(staff)
    cleaned = clean_text(note)
    if cleaned is None:
        raise _invalid("note", "required")
    assert staff.id is not None
    now = clock.now()

    async def command() -> Receipt:
        refund = await _locked(session, refund_id)
        if refund.proposed_by == staff.id:
            raise ApplicationError("forbidden")
        if after_settlement(refund.status, found) is None or (
            refund.settlement is not None
            and not (
                refund.status is RefundStatus.REQUESTED
                and refund.settlement is Settlement.REQUESTED
            )
        ):
            raise ApplicationError("conflict")
        await session.execute(
            _SETTLE,
            {
                "id": refund.id,
                "settlement": found.value,
                "settled_by": staff.id,
                "now": now,
                "note": cleaned,
            },
        )
        await enqueue(
            session,
            JobRequest(
                kind=SETTLE_KIND,
                idempotency_key=f"{refund.id}:{found.value}",
                payload={"refund_id": refund.id},
            ),
            now=now,
        )
        recorded = await _refund(session, refund.id)
        await _audit(
            session, staff, "change", f"found_{found.value}", recorded, clock, reason=cleaned
        )
        return Receipt(200, {"refund_id": refund.id})

    await run_once(
        session,
        actor=staff,
        operation=SETTLE_OPERATION,
        key=idempotency_key,
        request={"refund_id": refund_id, "found": found.value, "note": cleaned},
        now=now,
        command=command,
    )
    return await _refund(session, refund_id)


async def list_refunds(
    session: AsyncSession,
    staff: Actor,
    *,
    status: RefundStatus | None = None,
    deposit_id: str | None = None,
    limit: int = 50,
) -> list[Refund]:
    """Refunds, newest first."""
    _staff_only(staff)
    rows = await session.execute(
        _LIST,
        {
            "status": status.value if status else None,
            "deposit_id": deposit_id,
            "limit": max(1, min(limit, MAX_LISTED)),
        },
    )
    return [_from_row(row) for row in rows.all()]


async def proposals_awaiting_approval(session: AsyncSession, *, limit: int = 100) -> list[Refund]:
    """Open proposals, oldest first, for the approvals inbox. Empty outside a staff transaction."""
    rows = await session.execute(
        _LIST, {"status": RefundStatus.PROPOSED.value, "deposit_id": None, "limit": limit}
    )
    return sorted((_from_row(row) for row in rows.all()), key=lambda r: (r.proposed_at, r.id))


async def get_refund(
    session: AsyncSession, staff: Actor, refund_id: str, clock: Clock
) -> RefundDetail:
    """One refund, its deposit and card, the money waiting now and whether approving would go
    through."""
    _staff_only(staff)
    refund = await _refund(session, refund_id)
    deposit = await _deposit(session, refund.deposit_id)
    facts, order = await _facts(session, deposit, clock)
    card = order.card if order is not None else None
    return RefundDetail(
        refund=refund,
        deposit=deposit,
        card_brand=card.brand if card else None,
        card_last_four=card.last_four if card else None,
        waiting=facts.waiting,
        refusal=refusal(facts) if refund.status.is_open else None,
    )


# The worker -------------------------------------------------------------------------------------


async def hold_refund(session: AsyncSession, refund_id: str, clock: Clock) -> Refund:
    """Check an approved refund again and hold its money, in the worker's transaction.

    Takes the investment's row lock first, the lock the start of an investment holds while it
    reads the money waiting, so the two cannot interleave. If anything changed, the refund is
    marked failed with a code and nothing moves. A refund past `approved` is left as it is.
    """
    refund = await _locked(session, refund_id)
    if refund.status is not RefundStatus.APPROVED:
        return refund
    await investments.lock_investment(session, refund.investment_id)
    deposit = await service.find_deposit(session, refund.deposit_id, lock=True)
    if deposit is None:
        raise ApplicationError("not_found")
    facts, _ = await _facts(session, deposit, clock)
    problem = refusal(facts)
    if problem is not None:
        return await _finish(session, refund, RefundStatus.FAILED, clock, failure=problem.value)
    lines = [debit(await _account(session, _awaiting_start(refund), clock), refund.amount)]
    if refund.finvest_erred and refund.charge.is_positive:
        lines.append(debit(await _account(session, _charges(refund), clock), refund.charge))
    lines.append(credit(await _account(session, _due(refund.total.currency), clock), refund.total))
    draft = EntryDraft(
        entry_type=HELD_ENTRY_TYPE,
        effective_date=business_date(clock.now()),
        lines=tuple(lines),
        idempotency_key=f"provider-refund-hold:{refund.id}",
        source_type=SOURCE_TYPE,
        source_id=refund.id,
    )
    try:
        async with session.begin_nested():
            entry = await ledger.post_entry(session, draft, clock)
    except InsufficientFunds:
        return await _finish(
            session, refund, RefundStatus.FAILED, clock, failure="insufficient_funds"
        )
    await session.execute(_HOLD, {"id": refund.id, "entry_id": entry.id, "now": clock.now()})
    held = await _refund(session, refund.id)
    await _system_audit(session, "held", held, clock)
    return held


async def record_answer(
    session: AsyncSession, refund_id: str, result: RequestResult, clock: Clock
) -> Refund:
    """Record the provider's answer to the one request for a `sending` refund."""
    refund = await _locked(session, refund_id)
    if refund.status is not RefundStatus.SENDING:
        # Most often the read job made it uncertain first. The answer changes nothing now, but
        # staff settling it should know Pesapal did answer, and how.
        logger.warning(
            "provider_refund_answer_late",
            refund_id=refund.id,
            status=refund.status.value,
            answer=result.value,
        )
        return refund
    status = after_request(result)
    if status is RefundStatus.FAILED:
        code = failure_code(result)
        assert code is not None
        return await _release(session, refund, clock, failure=code)
    await session.execute(_MOVE, {"id": refund.id, "status": status.value})
    moved = await _refund(session, refund.id)
    if status is RefundStatus.REQUESTED:
        deposit = await _deposit(session, refund.deposit_id)
        await service.tell_client_refunded(session, deposit, clock.now())
    await _system_audit(session, status.value, moved, clock)
    log = logger.warning if status is RefundStatus.UNCERTAIN else logger.info
    log("provider_refund_answered", refund_id=refund.id, status=status.value)
    return moved


async def carry_out_finding(session: AsyncSession, refund_id: str, clock: Clock) -> Refund:
    """Carry out what staff found at the provider, in the worker's transaction."""
    refund = await _locked(session, refund_id)
    if refund.settlement is None:
        return refund
    status = after_settlement(refund.status, refund.settlement)
    if status is None:
        return refund
    if status is RefundStatus.DONE:
        return await _pay_out(session, refund, clock)
    if status is RefundStatus.FAILED:
        return await _release(session, refund, clock, failure="not_paid_by_provider")
    await session.execute(_MOVE, {"id": refund.id, "status": status.value})
    moved = await _refund(session, refund.id)
    deposit = await _deposit(session, refund.deposit_id)
    await service.tell_client_refunded(session, deposit, clock.now())
    await _system_audit(session, status.value, moved, clock)
    return moved


async def record_reversed(session: AsyncSession, refund_id: str, clock: Clock) -> Refund:
    """The provider reads the payment as paid back: a requested refund is done."""
    refund = await _locked(session, refund_id)
    if refund.status is not RefundStatus.REQUESTED:
        return refund
    return await _pay_out(session, refund, clock)


async def interrupted(session: AsyncSession, refund_id: str, clock: Clock) -> Refund:
    """A refund left `sending`, its answer never recorded, may have reached the provider: it is
    `uncertain`, for staff to look up. It is never asked again."""
    return await record_answer(session, refund_id, RequestResult.UNKNOWN, clock)


class ProviderRefundJobs:
    """The worker's side: send an approved refund once, carry out findings, read requested ones."""

    def __init__(self, database: Database, provider: CardCollectionsProvider, clock: Clock) -> None:
        self._database = database
        self._provider = provider
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {
            SEND_KIND: external_effect(self.send),
            SETTLE_KIND: self.settle,
            READ_KIND: self.read,
        }

    async def send(self, job: ClaimedJob, context: JobContext) -> None:
        refund_id = _refund_id(job.payload)
        async with self._database.actor_transaction(SYSTEM) as session:
            await job_queue.lock_fenced(session, job)
            try:
                held = await hold_refund(session, refund_id, self._clock)
            except ApplicationError as error:
                raise PermanentJobError(code=f"provider_refund_{error.code}") from error
            if held.status is not RefundStatus.SENDING or held.provider != self._provider.name:
                return
            order = await cards.find_order(session, held.deposit_id)
        # The hold is committed before the provider hears of it. The job runs once, so a crash
        # from here leaves the refund `sending`, and the read job makes it `uncertain`.
        code = order.card.confirmation_code if order and order.card else None
        if code is None:
            result = RequestResult.NOT_SENT
        else:
            try:
                result = await self._provider.refund_payment(
                    RefundRequest(held.id, code, held.total, REMARKS)
                )
            except Exception as error:
                logger.error("provider_refund_raised", error_type=type(error).__name__)
                result = RequestResult.UNKNOWN

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            answered = await record_answer(session, refund_id, result, self._clock)
            return {"status": answered.status.value}

        await context.apply(effect)

    async def settle(self, job: ClaimedJob, context: JobContext) -> None:
        refund_id = _refund_id(job.payload)

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            try:
                finished = await carry_out_finding(session, refund_id, self._clock)
            except ApplicationError as error:
                raise PermanentJobError(code=f"provider_refund_{error.code}") from error
            return {"status": finished.status.value}

        await context.apply(effect)

    async def read(self, job: ClaimedJob, context: JobContext) -> None:
        """Read every requested refund at the provider, and pick up interrupted sends."""
        now = self._clock.now()
        async with self._database.actor_transaction(SYSTEM) as session:
            rows = (
                await session.execute(
                    _TO_READ,
                    {
                        "provider": self._provider.name,
                        "interrupted": now - SENDING_GRACE,
                        "limit": READ_BATCH,
                    },
                )
            ).all()
            refunds = [await _refund(session, row.id) for row in rows]
            deposits = {r.id: await _deposit(session, r.deposit_id) for r in refunds}
        reversed_ids: list[str] = []
        for refund in refunds:
            deposit = deposits[refund.id]
            if refund.status is not RefundStatus.REQUESTED or deposit.provider_reference is None:
                continue
            seen = await self._provider.payment_reversed(
                request_id=deposit.id, provider_reference=deposit.provider_reference
            )
            if seen is True:
                reversed_ids.append(refund.id)

        async def effect(session: AsyncSession) -> Mapping[str, object]:
            done = 0
            for refund in refunds:
                if refund.status is RefundStatus.SENDING:
                    await interrupted(session, refund.id, self._clock)
                elif refund.id in reversed_ids:
                    finished = await record_reversed(session, refund.id, self._clock)
                    done += finished.status is RefundStatus.DONE
            return {"read": len(refunds), "done": done}

        await context.apply(effect)


# Internals --------------------------------------------------------------------------------------


async def _pay_out(session: AsyncSession, refund: Refund, clock: Clock) -> Refund:
    """The provider paid it: the refunds due are met from the provider's balance, and the
    deposit is marked refunded. A balance too low to meet it is left for reconciliation."""
    draft = EntryDraft(
        entry_type=PAID_ENTRY_TYPE,
        effective_date=business_date(clock.now()),
        lines=(
            debit(await _account(session, _due(refund.total.currency), clock), refund.total),
            credit(
                await _account(
                    session, cards.provider_balance(refund.provider, refund.total.currency), clock
                ),
                refund.total,
            ),
        ),
        idempotency_key=f"provider-refund-paid:{refund.id}",
        source_type=SOURCE_TYPE,
        source_id=refund.id,
    )
    try:
        async with session.begin_nested():
            entry = await ledger.post_entry(session, draft, clock)
    except InsufficientFunds:
        # The ledger says the provider holds less than it paid out: a difference for
        # reconciliation to find, never a reason to lose the record. Tried again on the next read.
        logger.warning("provider_refund_balance_short", refund_id=refund.id)
        return refund
    finished = await _finish(session, refund, RefundStatus.DONE, clock, done_entry_id=entry.id)
    await session.execute(_MARK_DEPOSIT, {"id": refund.deposit_id, "refund_id": refund.id})
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action="payments.deposit_refunded",
            resource_type=service.DEPOSIT_RESOURCE,
            resource_id=refund.deposit_id,
            actor=AuditActor.system(),
            outcome="succeeded",
            details={"refund_id": refund.id, "ledger_entry_id": entry.id},
        ),
        clock,
    )
    return finished


async def _release(session: AsyncSession, refund: Refund, clock: Clock, *, failure: str) -> Refund:
    """Nothing went back to the card: reverse the hold exactly, and the refund failed."""
    assert refund.hold_entry_id is not None
    hold = await ledger.get_entry(session, refund.hold_entry_id)
    entry = await ledger.post_entry(
        session,
        EntryDraft(
            entry_type=RELEASED_ENTRY_TYPE,
            effective_date=business_date(clock.now()),
            lines=tuple(Line(line.account_id, -line.amount) for line in hold.lines),
            idempotency_key=f"provider-refund-release:{refund.id}",
            source_type=SOURCE_TYPE,
            source_id=refund.id,
            reverses_entry_id=hold.id,
        ),
        clock,
    )
    return await _finish(
        session, refund, RefundStatus.FAILED, clock, release_entry_id=entry.id, failure=failure
    )


async def _finish(
    session: AsyncSession,
    refund: Refund,
    status: RefundStatus,
    clock: Clock,
    *,
    done_entry_id: str | None = None,
    release_entry_id: str | None = None,
    failure: str | None = None,
) -> Refund:
    await session.execute(
        _FINISH,
        {
            "id": refund.id,
            "status": status.value,
            "now": clock.now(),
            "done_entry_id": done_entry_id,
            "release_entry_id": release_entry_id,
            "failure_code": failure,
        },
    )
    finished = await _refund(session, refund.id)
    await _system_audit(session, status.value, finished, clock)
    log = logger.warning if failure else logger.info
    log(
        "provider_refund_finished",
        refund_id=refund.id,
        deposit_id=refund.deposit_id,
        error_code=failure,
    )
    return finished


async def _facts(
    session: AsyncSession, deposit: Deposit, clock: Clock
) -> tuple[Facts, cards.CardOrder | None]:
    entry_type = None
    if deposit.ledger_entry_id is not None:
        entry_type = (await ledger.get_entry(session, deposit.ledger_entry_id)).entry_type
    order = await cards.find_order(session, deposit.id)
    account = await ledger.open_account(
        session,
        AccountSpec(
            AWAITING_START,
            AccountKind.LIABILITY,
            deposit.amount.currency,
            "Paid in, awaiting start",
            owner_client_id=deposit.client_id,
            reference=deposit.purpose_id,
        ),
        clock,
    )
    facts = Facts(
        status=deposit.status,
        purpose=deposit.purpose,
        method=deposit.method,
        provider=deposit.provider,
        reversed=deposit.reversal_id is not None,
        refunded=deposit.provider_refund_id is not None,
        entry_type=entry_type,
        confirmation_code=order.card.confirmation_code if order and order.card else None,
        amount=deposit.amount,
        waiting=await ledger.get_balance(session, account.id),
    )
    return facts, order


def _awaiting_start(refund: Refund) -> AccountSpec:
    return AccountSpec(
        AWAITING_START,
        AccountKind.LIABILITY,
        refund.amount.currency,
        "Paid in, awaiting start",
        owner_client_id=refund.client_id,
        reference=refund.investment_id,
    )


def _due(currency: Currency) -> AccountSpec:
    # Money on its way back to clients' cards: held from the client's investment until the
    # provider pays it, then met from the provider's balance.
    return AccountSpec(DUE_CODE, AccountKind.LIABILITY, currency, "Card refunds due")


def _charges(refund: Refund) -> AccountSpec:
    return AccountSpec(
        service.CARD_CHARGES_CODE, AccountKind.INCOME, refund.charge.currency, "Card charges"
    )


async def _account(session: AsyncSession, spec: AccountSpec, clock: Clock) -> str:
    return (await ledger.open_account(session, spec, clock)).id


async def _decide(
    session: AsyncSession,
    staff: Actor,
    refund: Refund,
    status: RefundStatus,
    clock: Clock,
    note: str | None,
) -> None:
    await session.execute(
        _DECIDE,
        {
            "id": refund.id,
            "status": status.value,
            "decided_by": staff.id,
            "now": clock.now(),
            "note": note,
        },
    )


async def _deposit(session: AsyncSession, deposit_id: str) -> Deposit:
    deposit = await service.find_deposit(session, deposit_id)
    if deposit is None:
        raise ApplicationError("not_found")
    return deposit


async def _refund(session: AsyncSession, refund_id: str) -> Refund:
    row = (await session.execute(_BY_ID, {"id": refund_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _from_row(row)


async def _locked(session: AsyncSession, refund_id: str) -> Refund:
    row = (await session.execute(_BY_ID_LOCKED, {"id": refund_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _from_row(row)


async def _open(session: AsyncSession, refund_id: str) -> Refund:
    refund = await _locked(session, refund_id)
    if not refund.status.is_open:
        raise ApplicationError("conflict")
    return refund


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=["body", field], code=code)]
    )


def _refused(problem: Refusal) -> ApplicationError:
    return _invalid("deposit_id", problem.value)


def _refund_id(payload: Mapping[str, object]) -> str:
    value = payload.get("refund_id")
    if not isinstance(value, str):
        raise PermanentJobError(code="invalid_payload")
    return value


def _from_row(row: Row[Any]) -> Refund:
    currency = Currency(row.currency)
    return Refund(
        id=row.id,
        deposit_id=row.deposit_id,
        client_id=row.client_id,
        investment_id=row.investment_id,
        provider=row.provider,
        amount=Money(int(row.amount_minor), currency),
        charge=Money(int(row.charge_minor), currency),
        finvest_erred=bool(row.finvest_erred),
        total=Money(int(row.total_minor), currency),
        reason=row.reason,
        status=RefundStatus(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        sent_at=row.sent_at,
        settlement=Settlement(row.settlement) if row.settlement else None,
        settled_by=row.settled_by,
        settled_at=row.settled_at,
        settlement_note=row.settlement_note,
        done_at=row.done_at,
        hold_entry_id=row.hold_entry_id,
        done_entry_id=row.done_entry_id,
        release_entry_id=row.release_entry_id,
        failure_code=row.failure_code,
    )


def _details(refund: Refund) -> dict[str, object]:
    # IDs and flags only. The amount stays in the refund and the ledger, never on the trail.
    return {
        "deposit_id": refund.deposit_id,
        "investment_id": refund.investment_id,
        "finvest_erred": refund.finvest_erred,
        "hold_entry_id": refund.hold_entry_id,
        "done_entry_id": refund.done_entry_id,
        "release_entry_id": refund.release_entry_id,
        "failure": refund.failure_code,
    }


async def _audit(
    session: AsyncSession,
    staff: Actor,
    category: AuditCategory,
    step: str,
    refund: Refund,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    assert staff.id is not None
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=f"payments.provider_refund_{step}",
            resource_type=RESOURCE,
            resource_id=refund.id,
            actor=AuditActor.staff(staff.id),
            reason=reason,
            details=_details(refund),
        ),
        clock,
    )


async def _system_audit(session: AsyncSession, step: str, refund: Refund, clock: Clock) -> None:
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action=f"payments.provider_refund_{step}",
            outcome="failed" if step == "failed" else "succeeded",
            resource_type=RESOURCE,
            resource_id=refund.id,
            actor=AuditActor.system(),
            details=_details(refund),
        ),
        clock,
    )
