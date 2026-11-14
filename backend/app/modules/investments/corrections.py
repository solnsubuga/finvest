"""Corrections staff make to investments, each with a second person.

Two kinds for money that should not be invested, and one for money a matured investment holds:

- **Unwind.** A running investment was started by a deposit credited by mistake. Its
  principal goes back to the client's money awaiting start for it, and the interest it has
  earned, none of which has been paid out, is reversed:

      debit   client.holding               the principal
      credit  client.awaiting_start        the principal
      debit   client.interest_accrued      interest after tax so far
      debit   tax.withholding_payable      the tax held on it
      credit  interest.expense             interest before tax so far

  The investment becomes `unwound`. Payments then deals with the deposit itself.

- **Refund.** Money is waiting in the client's awaiting-start account for an investment that
  will never start: it was cancelled, or undone. It goes to the client's payout pending account
  for this correction, and a payout sends it to the payout account staff named.

- **Pay out.** A matured investment's money waits in the client's payout pending account,
  because the payout the client chose could not be made. When the client asks staff instead of
  choosing in the app, staff send it to another approved payout account of the client's. The
  worker creates the payout as the client's own request would (`pay_out_matured`), under the
  same key, so the client and staff can never both send it. No entry is posted here: the
  payout's own reserve entry moves the money.

One member of staff proposes, with a reason. A different one approves, sends it back with a
note, or the proposer withdraws it. The database holds them to that (migration 0066). An
approval queues the worker's job in the same transaction. The worker checks again that the
amount is exactly what was proposed and the investment is still in the state it was, then
moves the money in one entry. Anything else marks the correction failed and moves nothing.
No provider is called here: payouts sends the refund.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.investments.accrual import (
    INTEREST_EXPENSE,
    TAX_PAYABLE,
    interest_account,
)
from app.modules.investments.catalogue import invalid
from app.modules.investments.domain.investments import InvestmentStatus
from app.modules.investments.holdings import (
    AWAITING_START,
    INVESTMENT_RESOURCE,
    Investment,
    holding_account,
    investment_from_row,
    select_investments,
)
from app.modules.investments.withdrawal import check_payable_destination
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.payouts import payouts
from app.modules.payouts.domain import posting as payout_posting
from app.shared.actors import SYSTEM, Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

APPLY_KIND = "investments.apply_correction"
RESOURCE = "investment_correction"
MAX_REASON_LENGTH = 500
MAX_CORRECTIONS = 200
REFUNDABLE = frozenset({InvestmentStatus.CANCELLED, InvestmentStatus.UNWOUND})
logger = structlog.get_logger()


class CorrectionKind(StrEnum):
    UNWIND = "unwind"
    REFUND = "refund"
    PAY_OUT = "pay_out"


class CorrectionStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    SENT_BACK = "sent_back"
    WITHDRAWN = "withdrawn"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Correction:
    id: str
    kind: CorrectionKind
    investment_id: str
    client_id: str
    amount: Money
    destination_id: str | None
    reason: str
    status: CorrectionStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    done_at: datetime | None
    entry_id: str | None
    payout_id: str | None
    failure_code: str | None


_COLUMNS = (
    "id, kind, investment_id, client_id, currency, amount_minor, destination_id, reason, "
    "status, proposed_by, proposed_at, decided_by, decided_at, decision_note, done_at, "
    "entry_id, payout_id, failure_code"
)
_BY_ID = text(f"SELECT {_COLUMNS} FROM investment_corrections WHERE id = :id")  # noqa: S608
_BY_ID_LOCKED = text(
    f"SELECT {_COLUMNS} FROM investment_corrections WHERE id = :id FOR UPDATE"  # noqa: S608
)
_LIST = text(
    f"SELECT {_COLUMNS} FROM investment_corrections "  # noqa: S608
    "WHERE (CAST(:status AS text) IS NULL OR status = :status) "
    "AND (CAST(:investment_id AS text) IS NULL OR investment_id = :investment_id) "
    "ORDER BY proposed_at DESC, id DESC LIMIT :limit"
)
_INSERT = text(
    "INSERT INTO investment_corrections (id, kind, investment_id, client_id, currency, "
    "amount_minor, destination_id, reason, proposed_by, proposed_at) VALUES "
    "(:id, :kind, :investment_id, :client_id, :currency, :amount_minor, :destination_id, "
    ":reason, :proposed_by, :now)"
)
_DECIDE = text(
    "UPDATE investment_corrections SET status = :status, decided_by = :decided_by, "
    "decided_at = :now, decision_note = :note WHERE id = :id"
)
_FINISH = text(
    "UPDATE investment_corrections SET status = :status, done_at = :now, entry_id = :entry_id, "
    "payout_id = :payout_id, failure_code = :failure_code WHERE id = :id"
)
_INVESTMENT_LOCKED = select_investments("WHERE i.id = :id FOR UPDATE OF i")
_INVESTMENT = select_investments("WHERE i.id = :id")
_CARRIED_OVER = text("SELECT legacy_reference IS NOT NULL FROM investments WHERE id = :id")
_SETTLE = text(
    "UPDATE investments SET payout_id = :payout_id, payout_refusal = NULL, updated_at = :now "
    "WHERE id = :id"
)
_UNWIND = text(
    "UPDATE investments SET status = 'unwound', unwound_at = :now, unwind_entry_id = :entry_id, "
    "updated_at = :now WHERE id = :id"
)


# Staff ------------------------------------------------------------------------------------------


async def propose_correction(
    session: AsyncSession,
    staff: Actor,
    *,
    kind: CorrectionKind,
    investment_id: str,
    reason: str,
    destination_id: str | None = None,
    clock: Clock,
) -> Correction:
    """Save a correction for a second member of staff to approve. Nothing moves yet.

    An unwind is for a running investment the client has not asked to withdraw. A refund is
    for money waiting for an investment that was cancelled or undone, and a pay out for the
    money of a matured investment that waits for the client. Both name one of the client's
    payout accounts that is past its first day's safety wait and can carry the amount. `conflict`
    if a correction of the same kind is already open for the investment, or the investment is
    not in the state the kind is for.
    """
    _staff_only(staff)
    if not reason.strip() or len(reason.strip()) > MAX_REASON_LENGTH:
        raise invalid("reason", "required")
    investment = await _investment(session, investment_id)
    now = clock.now()
    if kind is CorrectionKind.UNWIND:
        if destination_id is not None:
            raise invalid("destination_id", "not_for_unwind")
        if (
            investment.status is not InvestmentStatus.ACTIVE
            or investment.withdrawal_requested_at is not None
        ):
            raise ApplicationError("conflict")
        amount = investment.amount
        # A carried-over investment was never paid for here, so its principal has no deposit
        # to go back to. A mistake in the migration is put right by the migration.
        if (await session.execute(_CARRIED_OVER, {"id": investment.id})).scalar_one():
            raise invalid("investment_id", "carried_over")
    else:
        if destination_id is None:
            raise invalid("destination_id", "required")
        if kind is CorrectionKind.REFUND:
            if investment.status not in REFUNDABLE:
                raise ApplicationError("conflict")
            amount = await _waiting(session, investment, clock)
            if not amount.is_positive:
                raise invalid("investment_id", "nothing_waiting")
        else:
            if not _held(investment):
                raise ApplicationError("conflict")
            amount = _proceeds(investment)
        await check_payable_destination(session, investment.client_id, destination_id, amount, now)
    correction_id = push_id()
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": correction_id,
                    "kind": kind.value,
                    "investment_id": investment.id,
                    "client_id": investment.client_id,
                    "currency": amount.currency.value,
                    "amount_minor": amount.minor,
                    "destination_id": destination_id,
                    "reason": reason.strip(),
                    "proposed_by": staff.id,
                    "now": now,
                },
            )
    except IntegrityError as error:
        # One of this kind is already waiting for this investment.
        raise ApplicationError("conflict") from error
    correction = await _correction(session, correction_id)
    await _audit(session, staff, "change", "proposed", correction, clock, reason=reason.strip())
    return correction


async def approve_correction(
    session: AsyncSession, staff: Actor, correction_id: str, clock: Clock
) -> Correction:
    """Approve as the second person. The worker carries it out, queued in this transaction."""
    correction = await _for_decision(session, staff, correction_id)
    if correction.proposed_by == staff.id:
        raise ApplicationError("forbidden")
    now = clock.now()
    await session.execute(
        _DECIDE,
        {
            "id": correction.id,
            "status": CorrectionStatus.APPROVED.value,
            "decided_by": staff.id,
            "now": now,
            "note": None,
        },
    )
    await enqueue(
        session,
        JobRequest(
            kind=APPLY_KIND,
            idempotency_key=correction.id,
            payload={"correction_id": correction.id},
        ),
        now=now,
    )
    approved = await _correction(session, correction.id)
    await _audit(session, staff, "approval", "approved", approved, clock, reason=approved.reason)
    return approved


async def send_back_correction(
    session: AsyncSession, staff: Actor, correction_id: str, note: str, clock: Clock
) -> Correction:
    """Decline as the second person, with a note for the proposer. Nothing moves."""
    correction = await _for_decision(session, staff, correction_id)
    if correction.proposed_by == staff.id:
        raise ApplicationError("forbidden")
    if not note.strip() or len(note.strip()) > MAX_REASON_LENGTH:
        raise invalid("note", "required")
    await session.execute(
        _DECIDE,
        {
            "id": correction.id,
            "status": CorrectionStatus.SENT_BACK.value,
            "decided_by": staff.id,
            "now": clock.now(),
            "note": note.strip(),
        },
    )
    sent_back = await _correction(session, correction.id)
    await _audit(session, staff, "approval", "sent_back", sent_back, clock, reason=note.strip())
    return sent_back


async def withdraw_correction(
    session: AsyncSession, staff: Actor, correction_id: str, clock: Clock
) -> Correction:
    """Take back a correction nobody has decided. Only its proposer can."""
    correction = await _for_decision(session, staff, correction_id)
    if correction.proposed_by != staff.id:
        raise ApplicationError("forbidden")
    await session.execute(
        _DECIDE,
        {
            "id": correction.id,
            "status": CorrectionStatus.WITHDRAWN.value,
            "decided_by": staff.id,
            "now": clock.now(),
            "note": None,
        },
    )
    withdrawn = await _correction(session, correction.id)
    await _audit(session, staff, "change", "withdrawn", withdrawn, clock)
    return withdrawn


async def list_corrections(
    session: AsyncSession,
    staff: Actor,
    *,
    status: CorrectionStatus | None = None,
    investment_id: str | None = None,
    limit: int = 50,
) -> list[Correction]:
    """Corrections, newest first. For the console and the approvals inbox."""
    _staff_only(staff)
    rows = await session.execute(
        _LIST,
        {
            "status": status.value if status else None,
            "investment_id": investment_id,
            "limit": max(1, min(limit, MAX_CORRECTIONS)),
        },
    )
    return [_from_row(row) for row in rows.all()]


async def get_correction(session: AsyncSession, staff: Actor, correction_id: str) -> Correction:
    _staff_only(staff)
    return await _correction(session, correction_id)


# The worker -------------------------------------------------------------------------------------


async def apply_correction(session: AsyncSession, correction_id: str, clock: Clock) -> Correction:
    """Carry out an approved correction. Worker only. Safe to repeat: a finished one is left.

    The investment must still be as it was when the correction was proposed and the amount
    exactly what was proposed. If not, or payouts refuses the refund, the correction is marked
    failed with a code and nothing moves.
    """
    row = (await session.execute(_BY_ID_LOCKED, {"id": correction_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    correction = _from_row(row)
    if correction.status is not CorrectionStatus.APPROVED:
        return correction
    investment = investment_from_row(
        (await session.execute(_INVESTMENT_LOCKED, {"id": correction.investment_id})).one()
    )
    if correction.kind is CorrectionKind.UNWIND:
        outcome = await _unwind(session, correction, investment, clock)
    elif correction.kind is CorrectionKind.REFUND:
        outcome = await _refund(session, correction, investment, clock)
    else:
        outcome = await _pay_out(session, correction, investment, clock)
    entry_id, payout_id, failure = outcome
    now = clock.now()
    await session.execute(
        _FINISH,
        {
            "id": correction.id,
            "status": (CorrectionStatus.FAILED if failure else CorrectionStatus.DONE).value,
            "now": now,
            "entry_id": entry_id,
            "payout_id": payout_id,
            "failure_code": failure,
        },
    )
    finished = await _correction(session, correction.id)
    await audit.record(
        session,
        AuditEvent(
            category="change",
            action=f"investments.correction.{'failed' if failure else 'done'}",
            outcome="failed" if failure else "succeeded",
            resource_type=RESOURCE,
            resource_id=correction.id,
            actor=AuditActor.system(),
            details=_details(finished),
        ),
        clock,
    )
    log = logger.warning if failure else logger.info
    log(
        "investment_correction_finished",
        correction_id=correction.id,
        investment_id=correction.investment_id,
        kind=correction.kind.value,
        error_code=failure,
    )
    return finished


_Outcome = tuple[str | None, str | None, str | None]


async def _unwind(
    session: AsyncSession, correction: Correction, investment: Investment, clock: Clock
) -> _Outcome:
    if (
        investment.status is not InvestmentStatus.ACTIVE
        or investment.withdrawal_requested_at is not None
    ):
        return None, None, "investment_not_running"
    if investment.amount != correction.amount:
        return None, None, "amount_changed"
    currency = investment.amount.currency
    holding = await ledger.open_account(session, holding_account(investment), clock)
    awaiting = await ledger.open_account(session, _awaiting_start(investment), clock)
    if (await ledger.get_balance(session, holding.id)) != investment.amount:
        return None, None, "holding_changed"
    lines = [debit(holding.id, investment.amount), credit(awaiting.id, investment.amount)]
    gross, tax = investment.accrued_gross, investment.accrued_tax
    if gross.is_positive:
        accrued = await ledger.open_account(session, interest_account(investment), clock)
        if (await ledger.get_balance(session, accrued.id)) != investment.accrued_net:
            return None, None, "interest_changed"
        expense = await ledger.open_account(
            session,
            AccountSpec(
                INTEREST_EXPENSE, AccountKind.EXPENSE, currency, "Interest paid to clients"
            ),
            clock,
        )
        if investment.accrued_net.is_positive:
            lines.append(debit(accrued.id, investment.accrued_net))
        if tax.is_positive:
            payable = await ledger.open_account(
                session,
                AccountSpec(
                    TAX_PAYABLE, AccountKind.LIABILITY, currency, "Tax withheld from interest"
                ),
                clock,
            )
            # The tax is held in one pool for URA. If what is left in it no longer covers this
            # investment's tax, it was paid over, and staff put that right with URA first.
            if (await ledger.get_balance(session, payable.id)) < tax:
                return None, None, "tax_already_remitted"
            lines.append(debit(payable.id, tax))
        lines.append(credit(expense.id, gross))
    now = clock.now()
    entry = await ledger.post_entry(
        session,
        EntryDraft(
            entry_type="investment.unwound",
            effective_date=business_date(now),
            lines=tuple(lines),
            idempotency_key=f"investment-unwind:{correction.id}",
            source_type="investment",
            source_id=investment.id,
        ),
        clock,
    )
    await session.execute(_UNWIND, {"id": investment.id, "entry_id": entry.id, "now": now})
    return entry.id, None, None


async def _refund(
    session: AsyncSession, correction: Correction, investment: Investment, clock: Clock
) -> _Outcome:
    if investment.status not in REFUNDABLE or correction.destination_id is None:
        return None, None, "investment_not_refundable"
    if await _waiting(session, investment, clock) != correction.amount:
        return None, None, "amount_changed"
    awaiting = await ledger.open_account(session, _awaiting_start(investment), clock)
    pending = await ledger.open_account(
        session,
        payout_posting.funding_account(
            investment.client_id, correction.id, correction.amount.currency
        ),
        clock,
    )
    try:
        # Nothing stays behind if payouts refuses: the entry and the payout go together.
        async with session.begin_nested():
            entry = await ledger.post_entry(
                session,
                EntryDraft(
                    entry_type="investment.refunded",
                    effective_date=business_date(clock.now()),
                    lines=(
                        debit(awaiting.id, correction.amount),
                        credit(pending.id, correction.amount),
                    ),
                    idempotency_key=f"investment-refund:{correction.id}",
                    source_type="investment",
                    source_id=investment.id,
                ),
                clock,
            )
            payout = await payouts.create_payout(
                session,
                SYSTEM,
                client_id=investment.client_id,
                destination_id=correction.destination_id,
                amount=correction.amount,
                source_reference=correction.id,
                request_key=f"refund:{correction.id}",
                clock=clock,
            )
    except payouts.PayoutRefused as refused:
        return None, None, refused.code
    return entry.id, payout.id, None


async def _pay_out(
    session: AsyncSession, correction: Correction, investment: Investment, clock: Clock
) -> _Outcome:
    # Still waiting: the client may have sent it on from the app since it was proposed.
    if not _held(investment) or correction.destination_id is None:
        return None, None, "investment_not_held"
    if _proceeds(investment) != correction.amount:
        return None, None, "amount_changed"
    try:
        # A refusal leaves nothing behind, and the money keeps waiting for the client.
        async with session.begin_nested():
            payout = await payouts.create_payout(
                session,
                SYSTEM,
                client_id=investment.client_id,
                destination_id=correction.destination_id,
                amount=correction.amount,
                source_reference=investment.id,
                # The key the client's own request uses for this destination, so the same
                # money can never be sent twice to it by the client and by staff.
                request_key=f"maturity:{investment.id}:{correction.destination_id}",
                clock=clock,
            )
            await session.execute(
                _SETTLE, {"id": investment.id, "payout_id": payout.id, "now": clock.now()}
            )
    except payouts.PayoutRefused as refused:
        return None, None, refused.code
    except ApplicationError as error:
        # The destination was removed or is no longer the client's.
        return None, None, f"destination_{error.code}"
    return None, payout.id, None


# Internals --------------------------------------------------------------------------------------


def _held(investment: Investment) -> bool:
    """Matured, and its money waits for the client: neither rolled over nor paid out."""
    return (
        investment.status is InvestmentStatus.MATURED
        and investment.rolled_into_id is None
        and investment.payout_id is None
    )


def _proceeds(investment: Investment) -> Money:
    """What a matured investment handed to payouts: the principal and the interest after tax."""
    return investment.amount + investment.accrued_net


def _awaiting_start(investment: Investment) -> AccountSpec:
    return AccountSpec(
        AWAITING_START,
        AccountKind.LIABILITY,
        investment.amount.currency,
        "Paid in, awaiting start",
        owner_client_id=investment.client_id,
        reference=investment.id,
    )


async def _waiting(session: AsyncSession, investment: Investment, clock: Clock) -> Money:
    account = await ledger.open_account(session, _awaiting_start(investment), clock)
    return await ledger.get_balance(session, account.id)


async def _investment(session: AsyncSession, investment_id: str) -> Investment:
    row = (await session.execute(_INVESTMENT, {"id": investment_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return investment_from_row(row)


async def _correction(session: AsyncSession, correction_id: str) -> Correction:
    row = (await session.execute(_BY_ID, {"id": correction_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _from_row(row)


async def _for_decision(session: AsyncSession, staff: Actor, correction_id: str) -> Correction:
    _staff_only(staff)
    row = (await session.execute(_BY_ID_LOCKED, {"id": correction_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    correction = _from_row(row)
    if correction.status is not CorrectionStatus.PROPOSED:
        raise ApplicationError("conflict")
    return correction


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


def _from_row(row: Row[Any]) -> Correction:
    currency = Currency(row.currency)
    return Correction(
        id=row.id,
        kind=CorrectionKind(row.kind),
        investment_id=row.investment_id,
        client_id=row.client_id,
        amount=Money(row.amount_minor, currency),
        destination_id=row.destination_id,
        reason=row.reason,
        status=CorrectionStatus(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        done_at=row.done_at,
        entry_id=row.entry_id,
        payout_id=row.payout_id,
        failure_code=row.failure_code,
    )


def _details(correction: Correction) -> dict[str, object]:
    # IDs and codes only. The amount stays in the correction and the ledger, not the trail.
    return {
        "kind": correction.kind.value,
        "investment_id": correction.investment_id,
        "destination_id": correction.destination_id,
        "ledger_entry_id": correction.entry_id,
        "payout_id": correction.payout_id,
        "error_code": correction.failure_code,
    }


async def _audit(
    session: AsyncSession,
    staff: Actor,
    category: AuditCategory,
    step: str,
    correction: Correction,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    assert staff.id is not None
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=f"investments.correction.{step}",
            resource_type=RESOURCE,
            resource_id=correction.id,
            actor=AuditActor.staff(staff.id),
            reason=reason,
            details=_details(correction),
        ),
        clock,
    )


__all__ = [
    "APPLY_KIND",
    "INVESTMENT_RESOURCE",
    "Correction",
    "CorrectionKind",
    "CorrectionStatus",
    "apply_correction",
    "approve_correction",
    "get_correction",
    "list_corrections",
    "propose_correction",
    "send_back_correction",
    "withdraw_correction",
]
