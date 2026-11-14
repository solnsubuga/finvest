"""Moving a bank transfer credited to the wrong client, by two people.

A staff member matched a client's receipt to a statement line that was really another client's
payment of the same amount, and a second person approved it. The money is on the statement, but
it is credited to the wrong client. The rules are in `domain/reassignments.py`.

1. One member of staff proposes moving the deposit to the right client's investment, which must
   be waiting for exactly that amount with no payment of its own. The evidence is the statement
   line the original review matched; it is recorded with the proposal and shown to the second
   person beside both clients' account numbers. Nothing moves yet.
2. A different member of staff approves. In that same transaction, everything is checked again
   and the money moves in one ledger entry:

       debit   client.awaiting_start   first client, first investment    the amount
       credit  client.awaiting_start   right client, their investment     the amount

   A new confirmed bank transfer deposit is saved for the right client's investment, pointing at
   that entry, and `payments.deposit_confirmed` is published for it, so the investment starts and
   the client gets a receipt. There is never an approved reassignment that has not moved.
3. Or the second person sends it back with a note, or the proposer withdraws it. Nothing moves.

The first client's money must still be waiting for the first investment. If that investment has
started, it is undone first, by two people, through investments corrections. If the money has
gone, this refuses with `money_moved_on`, and the mistake is put right as a receivable instead
(docs/payments.md, "Correcting a mistaken credit").

The original deposit, its review and its ledger entry never change. A reassignment that was
itself a mistake is put right by reassigning the new deposit. The database holds all of this
(migration 0171). Every function runs in the staff member's own transaction and commits nothing.
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
from app.modules.investments import holdings
from app.modules.investments.domain.investments import can_be_paid
from app.modules.kyc import service as kyc
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.ledger.domain.errors import InsufficientFunds
from app.modules.payments import bank_transfers as bank
from app.modules.payments import service
from app.modules.payments.domain.deposits import DepositStatus, Method, Purpose
from app.modules.payments.domain.reassignments import (
    Facts,
    ReassignmentStatus,
    Refusal,
    clean_text,
    reference_names,
    refusal,
)
from app.modules.payments.service import Deposit
from app.modules.reconciliation.statements import StatementLineView
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

AWAITING_START = "client.awaiting_start"
ENTRY_TYPE = "payment.deposit_reassigned"
SOURCE_TYPE = "payment.reassignment"
RESOURCE = "payment_reassignment"
PROPOSE_OPERATION = "payments.propose_reassignment"
APPROVE_OPERATION = "payments.approve_reassignment"
SEND_BACK_OPERATION = "payments.send_back_reassignment"
WITHDRAW_OPERATION = "payments.withdraw_reassignment"
MAX_LISTED = 200
logger = structlog.get_logger()

_COLUMNS = (
    "id, deposit_id, from_client_id, from_investment_id, to_client_id, to_investment_id, "
    "currency, amount_minor, statement_line_id, reason, status, proposed_by, proposed_at, "
    "decided_by, decided_at, decision_note, entry_id, new_deposit_id"
)
_BY_ID = text(f"SELECT {_COLUMNS} FROM payment_reassignments WHERE id = :id")  # noqa: S608
_BY_ID_LOCKED = text(
    f"SELECT {_COLUMNS} FROM payment_reassignments WHERE id = :id FOR UPDATE"  # noqa: S608
)
_LIST = text(
    f"SELECT {_COLUMNS} FROM payment_reassignments "  # noqa: S608 - constant column list
    "WHERE (CAST(:status AS text) IS NULL OR status = :status) "
    "AND (CAST(:deposit_id AS text) IS NULL OR deposit_id = :deposit_id "
    "     OR new_deposit_id = :deposit_id) "
    "ORDER BY proposed_at DESC, id DESC LIMIT :limit"
)
_CREATED = text(
    "SELECT deposit_id, statement_line_id FROM payment_reassignments "
    "WHERE new_deposit_id = :deposit_id AND status = 'applied'"
)
_FOR_LINE = text(
    f"SELECT {_COLUMNS} FROM payment_reassignments "  # noqa: S608 - constant column list
    "WHERE statement_line_id = :line_id ORDER BY proposed_at, id"
)
_INSERT = text(
    "INSERT INTO payment_reassignments (id, deposit_id, from_client_id, from_investment_id, "
    "to_client_id, to_investment_id, currency, amount_minor, statement_line_id, reason, "
    "proposed_by, proposed_at) VALUES (:id, :deposit_id, :from_client_id, :from_investment_id, "
    ":to_client_id, :to_investment_id, :currency, :amount_minor, :statement_line_id, :reason, "
    ":proposed_by, :now)"
)
# A bank transfer, as the money was one, confirmed from the start by the reassignment's entry.
_INSERT_DEPOSIT = text(
    "INSERT INTO payment_deposits (id, client_id, purpose_type, purpose_id, method, provider, "
    "currency, amount_minor, payer_msisdn, status, ledger_entry_id, created_at, updated_at, "
    "settled_at) VALUES (:id, :client_id, 'investment', :purpose_id, 'bank_transfer', 'bank', "
    ":currency, :amount_minor, NULL, 'confirmed', :entry_id, :now, :now, :now)"
)
_DECIDE = text(
    "UPDATE payment_reassignments SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_note = :note, entry_id = :entry_id, "
    "new_deposit_id = :new_deposit_id WHERE id = :id"
)


@dataclass(frozen=True, slots=True)
class Reassignment:
    id: str
    deposit_id: str
    from_client_id: str
    from_investment_id: str
    to_client_id: str
    to_investment_id: str
    amount: Money
    statement_line_id: str
    reason: str
    status: ReassignmentStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    entry_id: str | None
    new_deposit_id: str | None


@dataclass(frozen=True, slots=True)
class Party:
    """A client as staff see them on a reassignment: name and Finvest account number."""

    client_id: str
    name: str | None
    account_number: str | None


@dataclass(frozen=True, slots=True)
class ReassignmentDetail:
    """A reassignment with what the second person checks before approving."""

    reassignment: Reassignment
    from_client: Party
    to_client: Party
    # The statement line the original review matched. None if it can no longer be read.
    line: StatementLineView | None
    # The line's reference carries the right client's account number, or the first client's.
    reference_names_to_client: bool
    reference_names_from_client: bool
    # For an open proposal: why approving would be refused now. None if it would go through.
    refusal: Refusal | None


# Staff ------------------------------------------------------------------------------------------


async def propose_reassignment(
    session: AsyncSession,
    staff: Actor,
    *,
    deposit_id: str,
    to_investment_id: str,
    reason: str,
    idempotency_key: str,
    clock: Clock,
) -> Reassignment:
    """Ask a second member of staff to move a deposit to another client's investment.

    Refused with `validation_failed` and a code from `Refusal` if the move could not happen now,
    and `conflict` if the deposit or the investment already has an open proposal.
    """
    _staff_only(staff)
    cleaned = clean_text(reason)
    if cleaned is None:
        raise _invalid("reason", "required")
    assert staff.id is not None
    now = clock.now()

    async def command() -> Receipt:
        deposit = await _deposit(session, deposit_id)
        target = await _target(session, to_investment_id)
        facts = await _facts(session, staff, deposit, target, clock)
        problem = refusal(facts)
        if problem is not None:
            raise _refused(problem)
        assert facts.matched_line_id is not None
        reassignment_id = push_id()
        try:
            async with session.begin_nested():
                await session.execute(
                    _INSERT,
                    {
                        "id": reassignment_id,
                        "deposit_id": deposit.id,
                        "from_client_id": deposit.client_id,
                        "from_investment_id": deposit.purpose_id,
                        "to_client_id": target.client_id,
                        "to_investment_id": target.id,
                        "currency": deposit.amount.currency.value,
                        "amount_minor": deposit.amount.minor,
                        "statement_line_id": facts.matched_line_id,
                        "reason": cleaned,
                        "proposed_by": staff.id,
                        "now": now,
                    },
                )
        except IntegrityError as error:
            # An open proposal for this deposit or this investment got there first.
            raise ApplicationError("conflict") from error
        proposed = await _reassignment(session, reassignment_id)
        await _audit(session, staff, "change", "proposed", proposed, clock, reason=cleaned)
        return Receipt(201, {"reassignment_id": reassignment_id})

    outcome = await run_once(
        session,
        actor=staff,
        operation=PROPOSE_OPERATION,
        key=idempotency_key,
        request={"deposit_id": deposit_id, "to_investment_id": to_investment_id, "reason": cleaned},
        now=now,
        command=command,
    )
    return await _reassignment(session, str(outcome.receipt.response["reassignment_id"]))


async def approve_reassignment(
    session: AsyncSession,
    staff: Actor,
    reassignment_id: str,
    *,
    idempotency_key: str,
    clock: Clock,
) -> Reassignment:
    """Approve another person's proposal, and move the money in this same transaction.

    `forbidden` for the proposer. `validation_failed` with a `Refusal` code if the money or the
    investment changed since it was proposed; the proposal stays open, to be sent back or
    withdrawn.
    """
    _staff_only(staff)
    assert staff.id is not None
    approver_id = staff.id
    now = clock.now()

    async def command() -> Receipt:
        reassignment = await _open(session, reassignment_id)
        if reassignment.proposed_by == staff.id:
            raise ApplicationError("forbidden")
        deposit = await _deposit(session, reassignment.deposit_id)
        target = await _target(session, reassignment.to_investment_id)
        facts = await _facts(session, staff, deposit, target, clock)
        problem = refusal(facts)
        if problem is None and (
            facts.matched_line_id != reassignment.statement_line_id
            or target.client_id != reassignment.to_client_id
        ):
            raise ApplicationError("conflict")
        if problem is not None:
            raise _refused(problem)
        entry_id = await _move_money(session, reassignment, clock)
        new_deposit_id = bank.new_deposit_id()
        await session.execute(
            _DECIDE,
            {
                "id": reassignment.id,
                "status": ReassignmentStatus.APPLIED.value,
                "decided_by": staff.id,
                "now": now,
                "note": None,
                "entry_id": entry_id,
                "new_deposit_id": new_deposit_id,
            },
        )
        moved = await _save_new_deposit(session, reassignment, new_deposit_id, entry_id, now)
        await service.record_event(
            session, moved.id, None, DepositStatus.CONFIRMED, None, "reassignment", now
        )
        # As for any confirmed deposit, but in the approver's name: they confirmed it.
        await audit.record(
            session,
            AuditEvent(
                category="change",
                action="payments.deposit_confirmed",
                resource_type=service.DEPOSIT_RESOURCE,
                resource_id=moved.id,
                actor=AuditActor.staff(approver_id),
                outcome="succeeded",
                details={"method": moved.method.value, "cause": "reassignment"},
            ),
            clock,
        )
        await service.publish_confirmed(session, moved, now)
        applied = await _reassignment(session, reassignment.id)
        await _audit(
            session, staff, "approval", "applied", applied, clock, reason="second_person_approval"
        )
        return Receipt(200, {"reassignment_id": reassignment.id})

    outcome = await run_once(
        session,
        actor=staff,
        operation=APPROVE_OPERATION,
        key=idempotency_key,
        request={"reassignment_id": reassignment_id},
        now=now,
        command=command,
    )
    applied = await _reassignment(session, reassignment_id)
    if not outcome.replayed:
        logger.info(
            "deposit_reassigned",
            reassignment_id=applied.id,
            deposit_id=applied.deposit_id,
            new_deposit_id=applied.new_deposit_id,
        )
    return applied


async def send_back_reassignment(
    session: AsyncSession,
    staff: Actor,
    reassignment_id: str,
    note: str,
    *,
    idempotency_key: str,
    clock: Clock,
) -> Reassignment:
    """Decline another person's proposal, with a note for them. Nothing moves."""
    _staff_only(staff)
    cleaned = clean_text(note)
    if cleaned is None:
        raise _invalid("note", "required")

    async def command() -> Receipt:
        reassignment = await _open(session, reassignment_id)
        if reassignment.proposed_by == staff.id:
            raise ApplicationError("forbidden")
        await _decide(session, staff, reassignment, ReassignmentStatus.SENT_BACK, clock, cleaned)
        sent_back = await _reassignment(session, reassignment.id)
        await _audit(session, staff, "approval", "sent_back", sent_back, clock, reason=cleaned)
        return Receipt(200, {"reassignment_id": reassignment.id})

    await run_once(
        session,
        actor=staff,
        operation=SEND_BACK_OPERATION,
        key=idempotency_key,
        request={"reassignment_id": reassignment_id, "note": cleaned},
        now=clock.now(),
        command=command,
    )
    return await _reassignment(session, reassignment_id)


async def withdraw_reassignment(
    session: AsyncSession,
    staff: Actor,
    reassignment_id: str,
    *,
    idempotency_key: str,
    clock: Clock,
) -> Reassignment:
    """Take back your own proposal before anyone decides it. Nothing moves."""
    _staff_only(staff)

    async def command() -> Receipt:
        reassignment = await _open(session, reassignment_id)
        if reassignment.proposed_by != staff.id:
            raise ApplicationError("forbidden")
        await _decide(session, staff, reassignment, ReassignmentStatus.WITHDRAWN, clock, None)
        withdrawn = await _reassignment(session, reassignment.id)
        await _audit(session, staff, "change", "withdrawn", withdrawn, clock)
        return Receipt(200, {"reassignment_id": reassignment.id})

    await run_once(
        session,
        actor=staff,
        operation=WITHDRAW_OPERATION,
        key=idempotency_key,
        request={"reassignment_id": reassignment_id},
        now=clock.now(),
        command=command,
    )
    return await _reassignment(session, reassignment_id)


async def list_reassignments(
    session: AsyncSession,
    staff: Actor,
    *,
    status: ReassignmentStatus | None = None,
    deposit_id: str | None = None,
    limit: int = 50,
) -> list[Reassignment]:
    """Reassignments, newest first. `deposit_id` finds those from or to that deposit."""
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


async def proposals_awaiting_approval(
    session: AsyncSession, *, limit: int = 100
) -> list[Reassignment]:
    """Open proposals, oldest first, for the approvals inbox. Empty outside a staff transaction."""
    rows = await session.execute(
        _LIST, {"status": ReassignmentStatus.PROPOSED.value, "deposit_id": None, "limit": limit}
    )
    return sorted((_from_row(row) for row in rows.all()), key=lambda r: (r.proposed_at, r.id))


async def reassignments_for_line(
    session: AsyncSession, statement_line_id: str
) -> list[Reassignment]:
    """Every reassignment citing this statement line, oldest first. For reconciliation's
    screens, beside the line. Empty outside a staff or worker transaction."""
    rows = await session.execute(_FOR_LINE, {"line_id": statement_line_id})
    return [_from_row(row) for row in rows.all()]


async def get_reassignment(
    session: AsyncSession, staff: Actor, reassignment_id: str, clock: Clock
) -> ReassignmentDetail:
    """One reassignment and what the second person checks: both clients, the statement line and,
    while it is open, whether approving would go through now.

    The line's narrative can name the sender and both clients are named, so each look is written
    to the audit trail.
    """
    _staff_only(staff)
    reassignment = await _reassignment(session, reassignment_id)
    line = await _evidence_line(session, staff, reassignment)
    from_client = await _party(session, reassignment.from_client_id)
    to_client = await _party(session, reassignment.to_client_id)
    problem: Refusal | None = None
    if reassignment.status.is_open:
        deposit = await _deposit(session, reassignment.deposit_id)
        target = await _target(session, reassignment.to_investment_id)
        problem = refusal(await _facts(session, staff, deposit, target, clock))
    await _audit(session, staff, "access", "opened", reassignment, clock)
    return ReassignmentDetail(
        reassignment=reassignment,
        from_client=from_client,
        to_client=to_client,
        line=line,
        reference_names_to_client=line is not None
        and reference_names(line.reference, to_client.account_number),
        reference_names_from_client=line is not None
        and reference_names(line.reference, from_client.account_number),
        refusal=problem,
    )


# Internals --------------------------------------------------------------------------------------


async def _facts(
    session: AsyncSession, staff: Actor, deposit: Deposit, target: holdings.Investment, clock: Clock
) -> Facts:
    matched = await _evidence_line_id(session, staff, deposit)
    return Facts(
        method=deposit.method,
        status=deposit.status,
        purpose=deposit.purpose,
        amount=deposit.amount,
        from_client_id=deposit.client_id,
        matched_line_id=matched,
        still_waiting=await _waiting(
            session, deposit.client_id, deposit.purpose_id, deposit.amount.currency, clock
        ),
        to_client_id=target.client_id,
        to_payable=can_be_paid(target.status, target.pay_by, clock.now()),
        to_amount=target.amount,
        to_has_payment=await service.has_live_deposit(
            session, purpose=Purpose.INVESTMENT, purpose_id=target.id
        ),
    )


async def _evidence_line_id(session: AsyncSession, staff: Actor, deposit: Deposit) -> str | None:
    """The statement line that proves this money arrived: the one its approved review matched,
    or for a deposit saved by a reassignment, the line that reassignment cited."""
    if deposit.method is not Method.BANK_TRANSFER:
        return None
    review = await bank.get_review(session, staff, deposit.id)
    if review is not None:
        matched = review.outcome == "matched" and review.approved_by is not None
        return review.statement_line_id if matched else None
    row = (await session.execute(_CREATED, {"deposit_id": deposit.id})).one_or_none()
    return row.statement_line_id if row else None


async def _evidence_line(
    session: AsyncSession, staff: Actor, reassignment: Reassignment
) -> StatementLineView | None:
    """The line a reassignment cites, read from the statement through the original deposit."""
    deposit_id = reassignment.deposit_id
    # Follow a chain of reassignments back to the transfer a person matched.
    for _ in range(MAX_LISTED):
        row = (await session.execute(_CREATED, {"deposit_id": deposit_id})).one_or_none()
        if row is None:
            break
        deposit_id = row.deposit_id
    line = await bank.matched_line(session, staff, deposit_id)
    return line if line is not None and line.id == reassignment.statement_line_id else None


def _awaiting_start(client_id: str, investment_id: str, currency: Currency) -> AccountSpec:
    return AccountSpec(
        AWAITING_START,
        AccountKind.LIABILITY,
        currency,
        "Paid in, awaiting start",
        owner_client_id=client_id,
        reference=investment_id,
    )


async def _waiting(
    session: AsyncSession, client_id: str, investment_id: str, currency: Currency, clock: Clock
) -> Money:
    account = await ledger.open_account(
        session, _awaiting_start(client_id, investment_id, currency), clock
    )
    return await ledger.get_balance(session, account.id)


async def _move_money(session: AsyncSession, reassignment: Reassignment, clock: Clock) -> str:
    currency = reassignment.amount.currency
    source = await ledger.open_account(
        session,
        _awaiting_start(reassignment.from_client_id, reassignment.from_investment_id, currency),
        clock,
    )
    target = await ledger.open_account(
        session,
        _awaiting_start(reassignment.to_client_id, reassignment.to_investment_id, currency),
        clock,
    )
    try:
        entry = await ledger.post_entry(
            session,
            EntryDraft(
                entry_type=ENTRY_TYPE,
                effective_date=business_date(clock.now()),
                lines=(
                    debit(source.id, reassignment.amount),
                    credit(target.id, reassignment.amount),
                ),
                idempotency_key=f"deposit-reassignment:{reassignment.id}",
                source_type=SOURCE_TYPE,
                source_id=reassignment.id,
            ),
            clock,
        )
    except InsufficientFunds as error:
        # Checked just now, so something moved it in between. The ledger never overdraws.
        raise _refused(Refusal.MONEY_MOVED_ON) from error
    return entry.id


async def _save_new_deposit(
    session: AsyncSession,
    reassignment: Reassignment,
    deposit_id: str,
    entry_id: str,
    now: datetime,
) -> Deposit:
    try:
        # The live deposit index refuses this if the right client started a payment of their own
        # a moment ago, as it does for any second payment of one investment.
        async with session.begin_nested():
            await session.execute(
                _INSERT_DEPOSIT,
                {
                    "id": deposit_id,
                    "client_id": reassignment.to_client_id,
                    "purpose_id": reassignment.to_investment_id,
                    "currency": reassignment.amount.currency.value,
                    "amount_minor": reassignment.amount.minor,
                    "entry_id": entry_id,
                    "now": now,
                },
            )
    except IntegrityError as error:
        if service.LIVE_INVESTMENT_INDEX not in str(error.orig):
            raise
        raise _refused(Refusal.ALREADY_HAS_A_PAYMENT) from None
    return await _deposit(session, deposit_id)


async def _decide(
    session: AsyncSession,
    staff: Actor,
    reassignment: Reassignment,
    status: ReassignmentStatus,
    clock: Clock,
    note: str | None,
) -> None:
    await session.execute(
        _DECIDE,
        {
            "id": reassignment.id,
            "status": status.value,
            "decided_by": staff.id,
            "now": clock.now(),
            "note": note,
            "entry_id": None,
            "new_deposit_id": None,
        },
    )


async def _party(session: AsyncSession, client_id: str) -> Party:
    holder = await kyc.account_holder(session, client_id)
    if holder is None:
        return Party(client_id, None, None)
    name = " ".join(f"{holder.given_names} {holder.surname}".split())
    return Party(client_id, name or None, holder.account_number)


async def _deposit(session: AsyncSession, deposit_id: str) -> Deposit:
    deposit = await service.find_deposit(session, deposit_id)
    if deposit is None:
        raise ApplicationError("not_found")
    return deposit


async def _target(session: AsyncSession, investment_id: str) -> holdings.Investment:
    try:
        return await holdings.get_investment(session, investment_id)
    except ApplicationError as error:
        if error.code == "not_found":
            raise _invalid("to_investment_id", "not_found") from None
        raise


async def _reassignment(session: AsyncSession, reassignment_id: str) -> Reassignment:
    row = (await session.execute(_BY_ID, {"id": reassignment_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _from_row(row)


async def _open(session: AsyncSession, reassignment_id: str) -> Reassignment:
    row = (await session.execute(_BY_ID_LOCKED, {"id": reassignment_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    reassignment = _from_row(row)
    if not reassignment.status.is_open:
        raise ApplicationError("conflict")
    return reassignment


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=["body", field], code=code)]
    )


def _refused(problem: Refusal) -> ApplicationError:
    return _invalid(problem.field, problem.value)


def _from_row(row: Row[Any]) -> Reassignment:
    return Reassignment(
        id=row.id,
        deposit_id=row.deposit_id,
        from_client_id=row.from_client_id,
        from_investment_id=row.from_investment_id,
        to_client_id=row.to_client_id,
        to_investment_id=row.to_investment_id,
        amount=Money(int(row.amount_minor), Currency(row.currency)),
        statement_line_id=row.statement_line_id,
        reason=row.reason,
        status=ReassignmentStatus(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        entry_id=row.entry_id,
        new_deposit_id=row.new_deposit_id,
    )


def _details(reassignment: Reassignment) -> dict[str, object]:
    # IDs only. The amount stays in the reassignment and the ledger, never on the trail.
    return {
        "deposit_id": reassignment.deposit_id,
        "from_investment_id": reassignment.from_investment_id,
        "to_investment_id": reassignment.to_investment_id,
        "statement_line_id": reassignment.statement_line_id,
        "ledger_entry_id": reassignment.entry_id,
        "new_deposit_id": reassignment.new_deposit_id,
    }


async def _audit(
    session: AsyncSession,
    staff: Actor,
    category: AuditCategory,
    step: str,
    reassignment: Reassignment,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    assert staff.id is not None
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=f"payments.reassignment_{step}",
            resource_type=RESOURCE,
            resource_id=reassignment.id,
            actor=AuditActor.staff(staff.id),
            reason=reason,
            details=_details(reassignment),
        ),
        clock,
    )
