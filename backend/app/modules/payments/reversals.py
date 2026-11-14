"""Taking back a deposit credited by mistake, while its money still waits. By two people.

A deposit was confirmed, but the money never arrived: a provider said yes in error, or staff
matched a bank transfer to a statement line that was not it. The rules are in
`domain/reversals.py`.

1. One member of staff proposes reversing the deposit, with a reason. The whole amount must
   still wait in the client's `client.awaiting_start` account for the investment. If the
   investment started, it is unwound first, by two people, through investments corrections,
   which puts the principal back there. Nothing moves yet.
2. A different member of staff approves, and the worker's job is queued in that transaction.
   Or the second person sends it back with a note, or the proposer withdraws it.
3. The worker locks the investment, as the start of an investment does, checks everything
   again and reverses the deposit's own entry exactly:

       debit   client.awaiting_start   the client, the investment       the amount
       credit  the wallet or bank account the deposit said it reached   the amount

   The deposit is marked reversed in the same transaction. It stays confirmed, but no longer
   counts as the investment's payment: the client can pay again, or the investment is
   cancelled as unpaid at its pay-by time. If anything changed, the reversal is marked failed
   with a code and nothing moves.

Payments never writes to investments. The database holds all of this (migration 0261). The
staff functions run in the staff member's own transaction and commit nothing.
"""

from dataclasses import dataclass
from datetime import datetime
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
)
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.investments import service as investments
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, Line
from app.modules.ledger.domain.errors import InsufficientFunds
from app.modules.payments import service
from app.modules.payments.domain.reassignments import clean_text
from app.modules.payments.domain.reversals import Facts, Refusal, ReversalStatus, refusal
from app.modules.payments.service import Deposit
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

AWAITING_START = "client.awaiting_start"
ENTRY_TYPE = "payment.deposit_reversed"
SOURCE_TYPE = "payment.deposit_reversal"
APPLY_KIND = "payments.apply_reversal"
RESOURCE = "payment_deposit_reversal"
PROPOSE_OPERATION = "payments.propose_reversal"
APPROVE_OPERATION = "payments.approve_reversal"
SEND_BACK_OPERATION = "payments.send_back_reversal"
WITHDRAW_OPERATION = "payments.withdraw_reversal"
MAX_LISTED = 200
logger = structlog.get_logger()

_COLUMNS = (
    "id, deposit_id, client_id, investment_id, currency, amount_minor, reason, status, "
    "proposed_by, proposed_at, decided_by, decided_at, decision_note, done_at, entry_id, "
    "failure_code"
)
_BY_ID = text(f"SELECT {_COLUMNS} FROM payment_deposit_reversals WHERE id = :id")  # noqa: S608
_BY_ID_LOCKED = text(
    f"SELECT {_COLUMNS} FROM payment_deposit_reversals WHERE id = :id FOR UPDATE"  # noqa: S608
)
_LIST = text(
    f"SELECT {_COLUMNS} FROM payment_deposit_reversals "  # noqa: S608 - constant column list
    "WHERE (CAST(:status AS text) IS NULL OR status = :status) "
    "AND (CAST(:deposit_id AS text) IS NULL OR deposit_id = :deposit_id) "
    "ORDER BY proposed_at DESC, id DESC LIMIT :limit"
)
_INSERT = text(
    "INSERT INTO payment_deposit_reversals (id, deposit_id, client_id, investment_id, currency, "
    "amount_minor, reason, proposed_by, proposed_at) VALUES (:id, :deposit_id, :client_id, "
    ":investment_id, :currency, :amount_minor, :reason, :proposed_by, :now)"
)
_DECIDE = text(
    "UPDATE payment_deposit_reversals SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_note = :note WHERE id = :id"
)
_FINISH = text(
    "UPDATE payment_deposit_reversals SET status = :status, done_at = :now, "
    "entry_id = :entry_id, failure_code = :failure_code WHERE id = :id"
)
_MARK_DEPOSIT = text("UPDATE payment_deposits SET reversal_id = :reversal_id WHERE id = :id")


@dataclass(frozen=True, slots=True)
class Reversal:
    id: str
    deposit_id: str
    client_id: str
    investment_id: str
    amount: Money
    reason: str
    status: ReversalStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    done_at: datetime | None
    entry_id: str | None
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class ReversalDetail:
    """A reversal with what the second person checks before approving."""

    reversal: Reversal
    deposit: Deposit
    # What waits for the investment now, which must be at least the deposit's amount.
    waiting: Money
    # For an open proposal: why approving would be refused now. None if it would go through.
    refusal: Refusal | None


# Staff ------------------------------------------------------------------------------------------


async def propose_reversal(
    session: AsyncSession,
    staff: Actor,
    *,
    deposit_id: str,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> Reversal:
    """Ask a second member of staff to take back a deposit credited by mistake.

    Refused with `validation_failed` and a code from `Refusal` if it could not happen now, and
    `conflict` if the deposit already has an open, approved or done reversal.
    """
    _staff_only(staff)
    cleaned = clean_text(reason)
    if cleaned is None:
        raise _invalid("reason", "required")
    assert staff.id is not None
    now = clock.now()

    async def command() -> Receipt:
        deposit = await _deposit(session, deposit_id)
        problem = refusal(await _facts(session, deposit, clock))
        if problem is not None:
            raise _refused(problem)
        reversal_id = push_id()
        try:
            async with session.begin_nested():
                await session.execute(
                    _INSERT,
                    {
                        "id": reversal_id,
                        "deposit_id": deposit.id,
                        "client_id": deposit.client_id,
                        "investment_id": deposit.purpose_id,
                        "currency": deposit.amount.currency.value,
                        "amount_minor": deposit.amount.minor,
                        "reason": cleaned,
                        "proposed_by": staff.id,
                        "now": now,
                    },
                )
        except IntegrityError as error:
            # Another proposal for this deposit got there first.
            raise ApplicationError("conflict") from error
        proposed = await _reversal(session, reversal_id)
        await _audit(session, staff, "change", "proposed", proposed, clock, reason=cleaned)
        return Receipt(201, {"reversal_id": reversal_id})

    outcome = await run_once(
        session,
        actor=staff,
        operation=PROPOSE_OPERATION,
        key=idempotency_key,
        request={"deposit_id": deposit_id, "reason": cleaned},
        now=now,
        command=command,
    )
    return await _reversal(session, str(outcome.receipt.response["reversal_id"]))


async def approve_reversal(
    session: AsyncSession,
    staff: Actor,
    reversal_id: str,
    *,
    idempotency_key: str,
    clock: Clock,
) -> Reversal:
    """Approve another person's proposal. The worker's job is queued in this transaction.

    `forbidden` for the proposer. `validation_failed` with a `Refusal` code if the money moved
    on since it was proposed; the proposal stays open, to be sent back or withdrawn.
    """
    _staff_only(staff)
    now = clock.now()

    async def command() -> Receipt:
        reversal = await _open(session, reversal_id)
        if reversal.proposed_by == staff.id:
            raise ApplicationError("forbidden")
        problem = refusal(
            await _facts(session, await _deposit(session, reversal.deposit_id), clock)
        )
        if problem is not None:
            raise _refused(problem)
        await _decide(session, staff, reversal, ReversalStatus.APPROVED, clock, None)
        await enqueue(
            session,
            JobRequest(
                kind=APPLY_KIND,
                idempotency_key=reversal.id,
                payload={"reversal_id": reversal.id},
            ),
            now=now,
        )
        approved = await _reversal(session, reversal.id)
        await _audit(
            session, staff, "approval", "approved", approved, clock, reason="second_person_approval"
        )
        return Receipt(200, {"reversal_id": reversal.id})

    await run_once(
        session,
        actor=staff,
        operation=APPROVE_OPERATION,
        key=idempotency_key,
        request={"reversal_id": reversal_id},
        now=now,
        command=command,
    )
    return await _reversal(session, reversal_id)


async def send_back_reversal(
    session: AsyncSession,
    staff: Actor,
    reversal_id: str,
    note: str,
    *,
    idempotency_key: str,
    clock: Clock,
) -> Reversal:
    """Decline another person's proposal, with a note for them. Nothing moves."""
    _staff_only(staff)
    cleaned = clean_text(note)
    if cleaned is None:
        raise _invalid("note", "required")

    async def command() -> Receipt:
        reversal = await _open(session, reversal_id)
        if reversal.proposed_by == staff.id:
            raise ApplicationError("forbidden")
        await _decide(session, staff, reversal, ReversalStatus.SENT_BACK, clock, cleaned)
        sent_back = await _reversal(session, reversal.id)
        await _audit(session, staff, "approval", "sent_back", sent_back, clock, reason=cleaned)
        return Receipt(200, {"reversal_id": reversal.id})

    await run_once(
        session,
        actor=staff,
        operation=SEND_BACK_OPERATION,
        key=idempotency_key,
        request={"reversal_id": reversal_id, "note": cleaned},
        now=clock.now(),
        command=command,
    )
    return await _reversal(session, reversal_id)


async def withdraw_reversal(
    session: AsyncSession,
    staff: Actor,
    reversal_id: str,
    *,
    idempotency_key: str,
    clock: Clock,
) -> Reversal:
    """Take back your own proposal before anyone decides it. Nothing moves."""
    _staff_only(staff)

    async def command() -> Receipt:
        reversal = await _open(session, reversal_id)
        if reversal.proposed_by != staff.id:
            raise ApplicationError("forbidden")
        await _decide(session, staff, reversal, ReversalStatus.WITHDRAWN, clock, None)
        withdrawn = await _reversal(session, reversal.id)
        await _audit(session, staff, "change", "withdrawn", withdrawn, clock)
        return Receipt(200, {"reversal_id": reversal.id})

    await run_once(
        session,
        actor=staff,
        operation=WITHDRAW_OPERATION,
        key=idempotency_key,
        request={"reversal_id": reversal_id},
        now=clock.now(),
        command=command,
    )
    return await _reversal(session, reversal_id)


async def list_reversals(
    session: AsyncSession,
    staff: Actor,
    *,
    status: ReversalStatus | None = None,
    deposit_id: str | None = None,
    limit: int = 50,
) -> list[Reversal]:
    """Reversals, newest first."""
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


async def proposals_awaiting_approval(session: AsyncSession, *, limit: int = 100) -> list[Reversal]:
    """Open proposals, oldest first, for the approvals inbox. Empty outside a staff transaction."""
    rows = await session.execute(
        _LIST, {"status": ReversalStatus.PROPOSED.value, "deposit_id": None, "limit": limit}
    )
    return sorted((_from_row(row) for row in rows.all()), key=lambda r: (r.proposed_at, r.id))


async def get_reversal(
    session: AsyncSession, staff: Actor, reversal_id: str, clock: Clock
) -> ReversalDetail:
    """One reversal, its deposit, the money waiting now and whether approving would go through."""
    _staff_only(staff)
    reversal = await _reversal(session, reversal_id)
    deposit = await _deposit(session, reversal.deposit_id)
    facts = await _facts(session, deposit, clock)
    return ReversalDetail(
        reversal=reversal,
        deposit=deposit,
        waiting=facts.waiting,
        refusal=refusal(facts) if reversal.status.is_open else None,
    )


# The worker -------------------------------------------------------------------------------------


async def apply_reversal(session: AsyncSession, reversal_id: str, clock: Clock) -> Reversal:
    """Carry out an approved reversal. Worker only. Safe to repeat: a finished one is left.

    Takes the investment's row lock first, the lock the start of an investment holds while it
    reads the money waiting, so the two cannot interleave. If the deposit or the money changed,
    the reversal is marked failed with a code and nothing moves.
    """
    row = (await session.execute(_BY_ID_LOCKED, {"id": reversal_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    reversal = _from_row(row)
    if reversal.status is not ReversalStatus.APPROVED:
        return reversal
    # Locked before the money waiting is read, in this same transaction, as the start does.
    await investments.lock_investment(session, reversal.investment_id)
    deposit = await service.find_deposit(session, reversal.deposit_id, lock=True)
    if deposit is None:
        raise ApplicationError("not_found")
    problem = refusal(await _facts(session, deposit, clock))
    entry_id: str | None = None
    failure = problem.value if problem is not None else None
    if failure is None:
        entry_id, failure = await _post_reversal(session, reversal, deposit, clock)
    now = clock.now()
    await session.execute(
        _FINISH,
        {
            "id": reversal.id,
            "status": (ReversalStatus.FAILED if failure else ReversalStatus.DONE).value,
            "now": now,
            "entry_id": entry_id,
            "failure_code": failure,
        },
    )
    if failure is None:
        await session.execute(_MARK_DEPOSIT, {"id": deposit.id, "reversal_id": reversal.id})
        await audit.record(
            session,
            AuditEvent(
                category="change",
                action="payments.deposit_reversed",
                resource_type=service.DEPOSIT_RESOURCE,
                resource_id=deposit.id,
                actor=AuditActor.system(),
                outcome="succeeded",
                details={"reversal_id": reversal.id, "ledger_entry_id": entry_id},
            ),
            clock,
        )
    finished = await _reversal(session, reversal.id)
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action=f"payments.reversal_{'failed' if failure else 'done'}",
            outcome="failed" if failure else "succeeded",
            resource_type=RESOURCE,
            resource_id=reversal.id,
            actor=AuditActor.system(),
            details=_details(finished),
        ),
        clock,
    )
    log = logger.warning if failure else logger.info
    log(
        "deposit_reversal_finished",
        reversal_id=reversal.id,
        deposit_id=reversal.deposit_id,
        error_code=failure,
    )
    return finished


class ReversalJobs:
    """The worker's side: carry out an approved reversal. Calls no provider."""

    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    def handlers(self) -> dict[str, JobHandler | Handler]:
        return {APPLY_KIND: self.apply}

    async def apply(self, job: ClaimedJob, context: JobContext) -> None:
        reversal_id = job.payload.get("reversal_id")
        if not isinstance(reversal_id, str):
            raise PermanentJobError(code="invalid_payload")
        async with self._database.actor_transaction(SYSTEM) as session:
            try:
                await apply_reversal(session, reversal_id, self._clock)
            except ApplicationError as error:
                raise PermanentJobError(code=f"reversal_{error.code}") from error


# Internals --------------------------------------------------------------------------------------


async def _facts(session: AsyncSession, deposit: Deposit, clock: Clock) -> Facts:
    entry_type = None
    if deposit.ledger_entry_id is not None:
        entry_type = (await ledger.get_entry(session, deposit.ledger_entry_id)).entry_type
    account = await ledger.open_account(
        session,
        _awaiting_start(deposit.client_id, deposit.purpose_id, deposit.amount.currency),
        clock,
    )
    return Facts(
        status=deposit.status,
        purpose=deposit.purpose,
        reversed=deposit.reversal_id is not None,
        entry_type=entry_type,
        amount=deposit.amount,
        waiting=await ledger.get_balance(session, account.id),
    )


def _awaiting_start(client_id: str, investment_id: str, currency: Currency) -> AccountSpec:
    return AccountSpec(
        AWAITING_START,
        AccountKind.LIABILITY,
        currency,
        "Paid in, awaiting start",
        owner_client_id=client_id,
        reference=investment_id,
    )


async def _post_reversal(
    session: AsyncSession, reversal: Reversal, deposit: Deposit, clock: Clock
) -> tuple[str | None, str | None]:
    """Reverse the deposit's entry exactly. The entry, or the code for why it could not post."""
    assert deposit.ledger_entry_id is not None
    original = await ledger.get_entry(session, deposit.ledger_entry_id)
    draft = EntryDraft(
        entry_type=ENTRY_TYPE,
        effective_date=business_date(clock.now()),
        lines=tuple(Line(line.account_id, -line.amount) for line in original.lines),
        idempotency_key=f"deposit-reversal:{reversal.id}",
        source_type=SOURCE_TYPE,
        source_id=reversal.id,
        reverses_entry_id=original.id,
    )
    try:
        async with session.begin_nested():
            entry = await ledger.post_entry(session, draft, clock)
    except InsufficientFunds:
        # The wallet or bank account the money was said to reach no longer holds it.
        return None, "insufficient_funds"
    return entry.id, None


async def _decide(
    session: AsyncSession,
    staff: Actor,
    reversal: Reversal,
    status: ReversalStatus,
    clock: Clock,
    note: str | None,
) -> None:
    await session.execute(
        _DECIDE,
        {
            "id": reversal.id,
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


async def _reversal(session: AsyncSession, reversal_id: str) -> Reversal:
    row = (await session.execute(_BY_ID, {"id": reversal_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _from_row(row)


async def _open(session: AsyncSession, reversal_id: str) -> Reversal:
    row = (await session.execute(_BY_ID_LOCKED, {"id": reversal_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    reversal = _from_row(row)
    if not reversal.status.is_open:
        raise ApplicationError("conflict")
    return reversal


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=["body", field], code=code)]
    )


def _refused(problem: Refusal) -> ApplicationError:
    return _invalid("deposit_id", problem.value)


def _from_row(row: Row[Any]) -> Reversal:
    return Reversal(
        id=row.id,
        deposit_id=row.deposit_id,
        client_id=row.client_id,
        investment_id=row.investment_id,
        amount=Money(int(row.amount_minor), Currency(row.currency)),
        reason=row.reason,
        status=ReversalStatus(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        done_at=row.done_at,
        entry_id=row.entry_id,
        failure_code=row.failure_code,
    )


def _details(reversal: Reversal) -> dict[str, object]:
    # IDs only. The amount stays in the reversal and the ledger, never on the trail.
    return {
        "deposit_id": reversal.deposit_id,
        "investment_id": reversal.investment_id,
        "ledger_entry_id": reversal.entry_id,
        "failure": reversal.failure_code,
    }


async def _audit(
    session: AsyncSession,
    staff: Actor,
    category: AuditCategory,
    step: str,
    reversal: Reversal,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    assert staff.id is not None
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=f"payments.reversal_{step}",
            resource_type=RESOURCE,
            resource_id=reversal.id,
            actor=AuditActor.staff(staff.id),
            reason=reason,
            details=_details(reversal),
        ),
        clock,
    )
