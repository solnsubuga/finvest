"""What a client owes for a deposit credited by mistake, once the money has left. By two people.

A deposit was confirmed but the money never arrived, and the money it credited has since left
Finvest: the investment matured and was paid out, it was withdrawn early, or a refund went
out. There is nothing to reverse (`reversals.py` handles money still waiting). The rules are in
`domain/receivables.py`. Every step is proposed by one member of staff and approved by a
different one, and the approval posts the entry in that same transaction:

    open       debit  receivables.deposit_correction   credit  the account the deposit debited
    recover    debit  the account the deposit debited  credit  receivables.deposit_correction
    write_off  debit  losses.deposit_correction        credit  receivables.deposit_correction

The receivable is the client's, with the deposit's ID as its reference, and is always for the
deposit's whole amount. It is opened once and cleared once. The database holds all of this
(migration 0262). Every function runs in the staff member's own transaction and commits nothing.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.idempotency import Receipt, run_once
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.investments import service as investments
from app.modules.investments.domain.investments import InvestmentStatus
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.ledger.domain.errors import InsufficientFunds
from app.modules.payments import service
from app.modules.payments.domain.reassignments import clean_text
from app.modules.payments.domain.receivables import Facts, Refusal, StepKind, StepStatus, refusal
from app.modules.payments.service import Deposit
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

AWAITING_START = "client.awaiting_start"
RECEIVABLE = "receivables.deposit_correction"
LOSSES = "losses.deposit_correction"
SOURCE_TYPE = "payment.receivable"
RESOURCE = "payment_receivable_step"
PROPOSE_OPERATION = "payments.propose_receivable_step"
APPROVE_OPERATION = "payments.approve_receivable_step"
SEND_BACK_OPERATION = "payments.send_back_receivable_step"
WITHDRAW_OPERATION = "payments.withdraw_receivable_step"
MAX_LISTED = 200
logger = structlog.get_logger()

_COLUMNS = (
    "id, deposit_id, kind, client_id, currency, amount_minor, reason, status, proposed_by, "
    "proposed_at, decided_by, decided_at, decision_note, entry_id"
)
_BY_ID = text(f"SELECT {_COLUMNS} FROM payment_receivable_steps WHERE id = :id")  # noqa: S608
_BY_ID_LOCKED = text(
    f"SELECT {_COLUMNS} FROM payment_receivable_steps WHERE id = :id FOR UPDATE"  # noqa: S608
)
_LIST = text(
    f"SELECT {_COLUMNS} FROM payment_receivable_steps "  # noqa: S608 - constant column list
    "WHERE (CAST(:status AS text) IS NULL OR status = :status) "
    "AND (CAST(:deposit_id AS text) IS NULL OR deposit_id = :deposit_id) "
    "ORDER BY proposed_at DESC, id DESC LIMIT :limit"
)
_APPLIED = text(
    "SELECT kind FROM payment_receivable_steps WHERE deposit_id = :deposit_id "
    "AND status = 'applied'"
)
# Receivables opened and not yet cleared, oldest first.
_OUTSTANDING = text(
    f"SELECT {_COLUMNS} FROM payment_receivable_steps AS opened "  # noqa: S608
    "WHERE opened.kind = 'open' AND opened.status = 'applied' AND NOT EXISTS ("
    "SELECT FROM payment_receivable_steps AS cleared WHERE cleared.deposit_id = opened.deposit_id "
    "AND cleared.status = 'applied' AND cleared.kind IN ('recover', 'write_off')) "
    "ORDER BY opened.decided_at, opened.id"
)
_INSERT = text(
    "INSERT INTO payment_receivable_steps (id, deposit_id, kind, client_id, currency, "
    "amount_minor, reason, proposed_by, proposed_at) VALUES (:id, :deposit_id, :kind, "
    ":client_id, :currency, :amount_minor, :reason, :proposed_by, :now)"
)
_DECIDE = text(
    "UPDATE payment_receivable_steps SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_note = :note, entry_id = :entry_id WHERE id = :id"
)


@dataclass(frozen=True, slots=True)
class Step:
    id: str
    deposit_id: str
    kind: StepKind
    client_id: str
    amount: Money
    reason: str
    status: StepStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    entry_id: str | None


@dataclass(frozen=True, slots=True)
class StepDetail:
    """A step with what the second person checks before approving."""

    step: Step
    deposit: Deposit
    # What the receivable holds now: the amount the client owes for this deposit.
    owed: Money
    # For an open proposal: why approving would be refused now. None if it would go through.
    refusal: Refusal | None


# Staff ------------------------------------------------------------------------------------------


async def propose_step(
    session: AsyncSession,
    staff: Actor,
    *,
    kind: StepKind,
    deposit_id: str,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> Step:
    """Ask a second member of staff to open, recover or write off a deposit's receivable.

    Refused with `validation_failed` and a code from `Refusal` if the step could not happen
    now, and `conflict` if another step for the deposit already waits for a second person.
    """
    _staff_only(staff)
    cleaned = clean_text(reason)
    if cleaned is None:
        raise _invalid("reason", "required")
    assert staff.id is not None
    now = clock.now()

    async def command() -> Receipt:
        deposit = await _deposit(session, deposit_id)
        problem = refusal(kind, await _facts(session, deposit, clock))
        if problem is not None:
            raise _refused(problem)
        step_id = push_id()
        try:
            async with session.begin_nested():
                await session.execute(
                    _INSERT,
                    {
                        "id": step_id,
                        "deposit_id": deposit.id,
                        "kind": kind.value,
                        "client_id": deposit.client_id,
                        "currency": deposit.amount.currency.value,
                        "amount_minor": deposit.amount.minor,
                        "reason": cleaned,
                        "proposed_by": staff.id,
                        "now": now,
                    },
                )
        except IntegrityError as error:
            # Another step for this deposit waits for a second person.
            raise ApplicationError("conflict") from error
        proposed = await _step(session, step_id)
        await _audit(session, staff, "change", "proposed", proposed, clock, reason=cleaned)
        return Receipt(201, {"step_id": step_id})

    outcome = await run_once(
        session,
        actor=staff,
        operation=PROPOSE_OPERATION,
        key=idempotency_key,
        request={"kind": kind.value, "deposit_id": deposit_id, "reason": cleaned},
        now=now,
        command=command,
    )
    return await _step(session, str(outcome.receipt.response["step_id"]))


async def approve_step(
    session: AsyncSession, staff: Actor, step_id: str, *, idempotency_key: str, clock: Clock
) -> Step:
    """Approve another person's step, and post its entry in this same transaction.

    `forbidden` for the proposer. `validation_failed` with a `Refusal` code, or
    `insufficient_funds` if the account the money comes from no longer holds it; the proposal
    stays open, to be sent back or withdrawn.
    """
    _staff_only(staff)
    now = clock.now()

    async def command() -> Receipt:
        step = await _open(session, step_id)
        if step.proposed_by == staff.id:
            raise ApplicationError("forbidden")
        deposit = await _deposit(session, step.deposit_id)
        problem = refusal(step.kind, await _facts(session, deposit, clock))
        if problem is not None:
            raise _refused(problem)
        entry_id = await _post(session, step, deposit, clock)
        await session.execute(
            _DECIDE,
            {
                "id": step.id,
                "status": StepStatus.APPLIED.value,
                "decided_by": staff.id,
                "now": now,
                "note": None,
                "entry_id": entry_id,
            },
        )
        applied = await _step(session, step.id)
        await _audit(
            session, staff, "approval", "applied", applied, clock, reason="second_person_approval"
        )
        return Receipt(200, {"step_id": step.id})

    outcome = await run_once(
        session,
        actor=staff,
        operation=APPROVE_OPERATION,
        key=idempotency_key,
        request={"step_id": step_id},
        now=now,
        command=command,
    )
    applied = await _step(session, step_id)
    if not outcome.replayed:
        logger.info(
            "receivable_step_applied",
            step_id=applied.id,
            deposit_id=applied.deposit_id,
            kind=applied.kind.value,
        )
    return applied


async def send_back_step(
    session: AsyncSession,
    staff: Actor,
    step_id: str,
    note: str,
    *,
    idempotency_key: str,
    clock: Clock,
) -> Step:
    """Decline another person's step, with a note for them. Nothing moves."""
    _staff_only(staff)
    cleaned = clean_text(note)
    if cleaned is None:
        raise _invalid("note", "required")

    async def command() -> Receipt:
        step = await _open(session, step_id)
        if step.proposed_by == staff.id:
            raise ApplicationError("forbidden")
        await _decide(session, staff, step, StepStatus.SENT_BACK, clock, cleaned)
        sent_back = await _step(session, step.id)
        await _audit(session, staff, "approval", "sent_back", sent_back, clock, reason=cleaned)
        return Receipt(200, {"step_id": step.id})

    await run_once(
        session,
        actor=staff,
        operation=SEND_BACK_OPERATION,
        key=idempotency_key,
        request={"step_id": step_id, "note": cleaned},
        now=clock.now(),
        command=command,
    )
    return await _step(session, step_id)


async def withdraw_step(
    session: AsyncSession, staff: Actor, step_id: str, *, idempotency_key: str, clock: Clock
) -> Step:
    """Take back your own step before anyone decides it. Nothing moves."""
    _staff_only(staff)

    async def command() -> Receipt:
        step = await _open(session, step_id)
        if step.proposed_by != staff.id:
            raise ApplicationError("forbidden")
        await _decide(session, staff, step, StepStatus.WITHDRAWN, clock, None)
        withdrawn = await _step(session, step.id)
        await _audit(session, staff, "change", "withdrawn", withdrawn, clock)
        return Receipt(200, {"step_id": step.id})

    await run_once(
        session,
        actor=staff,
        operation=WITHDRAW_OPERATION,
        key=idempotency_key,
        request={"step_id": step_id},
        now=clock.now(),
        command=command,
    )
    return await _step(session, step_id)


async def list_steps(
    session: AsyncSession,
    staff: Actor,
    *,
    status: StepStatus | None = None,
    deposit_id: str | None = None,
    limit: int = 50,
) -> list[Step]:
    """Receivable steps, newest first."""
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


async def get_step(session: AsyncSession, staff: Actor, step_id: str, clock: Clock) -> StepDetail:
    """One step, its deposit, what the client owes now and whether approving would go through."""
    _staff_only(staff)
    step = await _step(session, step_id)
    deposit = await _deposit(session, step.deposit_id)
    return StepDetail(
        step=step,
        deposit=deposit,
        owed=await _owed(session, deposit, clock),
        refusal=refusal(step.kind, await _facts(session, deposit, clock))
        if step.status.is_open
        else None,
    )


async def proposals_awaiting_approval(session: AsyncSession, *, limit: int = 100) -> list[Step]:
    """Open proposals, oldest first, for the approvals inbox. Empty outside a staff transaction."""
    rows = await session.execute(
        _LIST, {"status": StepStatus.PROPOSED.value, "deposit_id": None, "limit": limit}
    )
    return sorted((_from_row(row) for row in rows.all()), key=lambda s: (s.proposed_at, s.id))


async def outstanding(session: AsyncSession) -> list[Step]:
    """The opening step of every receivable not yet cleared, oldest first. For the daily close,
    which reports them until they are cleared. Empty outside a staff or worker transaction."""
    rows = await session.execute(_OUTSTANDING)
    return [_from_row(row) for row in rows.all()]


# Internals --------------------------------------------------------------------------------------


async def _facts(session: AsyncSession, deposit: Deposit, clock: Clock) -> Facts:
    entry_type = None
    if deposit.ledger_entry_id is not None:
        entry_type = (await ledger.get_entry(session, deposit.ledger_entry_id)).entry_type
    waiting = await ledger.open_account(
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
    running = False
    if deposit.purpose_id:
        try:
            investment = await investments.get_investment(session, deposit.purpose_id)
            running = investment.status is InvestmentStatus.ACTIVE
        except ApplicationError as error:
            if error.code != "not_found":
                raise
    applied = {row.kind for row in (await session.execute(_APPLIED, {"deposit_id": deposit.id}))}
    return Facts(
        status=deposit.status,
        purpose=deposit.purpose,
        reversed=deposit.reversal_id is not None,
        entry_type=entry_type,
        amount=deposit.amount,
        waiting=await ledger.get_balance(session, waiting.id),
        investment_running=running,
        opened=StepKind.OPEN.value in applied,
        cleared=bool(applied & {StepKind.RECOVER.value, StepKind.WRITE_OFF.value}),
    )


def _receivable(deposit: Deposit) -> AccountSpec:
    return AccountSpec(
        RECEIVABLE,
        AccountKind.ASSET,
        deposit.amount.currency,
        "Owed for a payment credited by mistake",
        owner_client_id=deposit.client_id,
        reference=deposit.id,
    )


async def _owed(session: AsyncSession, deposit: Deposit, clock: Clock) -> Money:
    account = await ledger.open_account(session, _receivable(deposit), clock)
    return await ledger.get_balance(session, account.id)


async def _cash_account_id(session: AsyncSession, deposit: Deposit) -> str:
    """The wallet or bank account the deposit's own entry debited."""
    assert deposit.ledger_entry_id is not None
    entry = await ledger.get_entry(session, deposit.ledger_entry_id)
    [line] = [line for line in entry.lines if line.amount.minor > 0]
    return line.account_id


async def _post(session: AsyncSession, step: Step, deposit: Deposit, clock: Clock) -> str:
    receivable = await ledger.open_account(session, _receivable(deposit), clock)
    if step.kind is StepKind.WRITE_OFF:
        losses = await ledger.open_account(
            session,
            AccountSpec(
                LOSSES,
                AccountKind.EXPENSE,
                step.amount.currency,
                "Payments credited by mistake, written off",
            ),
            clock,
        )
        other = losses.id
    else:
        other = await _cash_account_id(session, deposit)
    if step.kind is StepKind.OPEN:
        lines = (debit(receivable.id, step.amount), credit(other, step.amount))
    else:
        lines = (debit(other, step.amount), credit(receivable.id, step.amount))
    try:
        async with session.begin_nested():
            entry = await ledger.post_entry(
                session,
                EntryDraft(
                    entry_type=step.kind.entry_type,
                    effective_date=business_date(clock.now()),
                    lines=lines,
                    idempotency_key=f"deposit-receivable:{step.id}",
                    source_type=SOURCE_TYPE,
                    source_id=step.id,
                ),
                clock,
            )
    except InsufficientFunds as error:
        # Opening takes the money out of the account the deposit said it reached. If that
        # account no longer holds it, the books are short already: a matter for finance first.
        raise _invalid("deposit_id", "insufficient_funds") from error
    return entry.id


async def _decide(
    session: AsyncSession,
    staff: Actor,
    step: Step,
    status: StepStatus,
    clock: Clock,
    note: str | None,
) -> None:
    await session.execute(
        _DECIDE,
        {
            "id": step.id,
            "status": status.value,
            "decided_by": staff.id,
            "now": clock.now(),
            "note": note,
            "entry_id": None,
        },
    )


async def _deposit(session: AsyncSession, deposit_id: str) -> Deposit:
    deposit = await service.find_deposit(session, deposit_id)
    if deposit is None:
        raise ApplicationError("not_found")
    return deposit


async def _step(session: AsyncSession, step_id: str) -> Step:
    row = (await session.execute(_BY_ID, {"id": step_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _from_row(row)


async def _open(session: AsyncSession, step_id: str) -> Step:
    row = (await session.execute(_BY_ID_LOCKED, {"id": step_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    step = _from_row(row)
    if not step.status.is_open:
        raise ApplicationError("conflict")
    return step


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=["body", field], code=code)]
    )


def _refused(problem: Refusal) -> ApplicationError:
    return _invalid("deposit_id", problem.value)


def _from_row(row: Row[Any]) -> Step:
    return Step(
        id=row.id,
        deposit_id=row.deposit_id,
        kind=StepKind(row.kind),
        client_id=row.client_id,
        amount=Money(int(row.amount_minor), Currency(row.currency)),
        reason=row.reason,
        status=StepStatus(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        entry_id=row.entry_id,
    )


def _details(step: Step) -> dict[str, object]:
    # IDs only. The amount stays in the step and the ledger, never on the trail.
    return {
        "deposit_id": step.deposit_id,
        "kind": step.kind.value,
        "ledger_entry_id": step.entry_id,
    }


async def _audit(
    session: AsyncSession,
    staff: Actor,
    category: AuditCategory,
    step_name: str,
    step: Step,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    assert staff.id is not None
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=f"payments.receivable_{step_name}",
            resource_type=RESOURCE,
            resource_id=step.id,
            actor=AuditActor.staff(staff.id),
            reason=reason,
            details=_details(step),
        ),
        clock,
    )
