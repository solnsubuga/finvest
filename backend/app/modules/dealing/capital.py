"""Finvest's own money in the liquidity buffer, paid in or taken out by two people. Works in the
caller's transaction.

The rules are in `domain/capital.py`. Migration 0087 holds them whatever this code does: the
second person is never the first, what was proposed never changes, and an approved movement
names exactly the ledger entry it posted.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.dealing import withdrawals
from app.modules.dealing.domain.capital import (
    CAPITAL,
    Direction,
    MovementStatus,
    Proposal,
    Refusal,
    accounts,
    check_decision,
    check_proposal,
    check_taking_out,
    posting,
)
from app.modules.ledger import closing as ledger_closing
from app.modules.ledger import service as ledger
from app.modules.ledger import statements
from app.modules.ledger.domain.errors import DayClosed, InsufficientFunds
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

RESOURCE = "dealing_buffer_capital"

_COLUMNS = (
    "id, direction, currency, amount_minor, bank_reference, value_date, reason, status, "
    "proposed_by, proposed_at, decided_by, decided_at, decision_reason, ledger_entry_id"
)
_GET = text(f"SELECT {_COLUMNS} FROM dealing_buffer_capital WHERE id = :id")  # noqa: S608
_LOCK = text(
    f"SELECT {_COLUMNS} FROM dealing_buffer_capital WHERE id = :id FOR UPDATE"  # noqa: S608
)
_LIST = text(
    f"SELECT {_COLUMNS} FROM dealing_buffer_capital "  # noqa: S608
    "WHERE currency = :currency ORDER BY proposed_at DESC, id DESC LIMIT :limit"
)
_WAITING = text(
    f"SELECT {_COLUMNS} FROM dealing_buffer_capital "  # noqa: S608
    "WHERE status = 'proposed' ORDER BY proposed_at, id LIMIT :limit"
)
_INSERT = text(
    "INSERT INTO dealing_buffer_capital (id, direction, currency, amount_minor, bank_reference, "
    "value_date, reason, status, proposed_by, proposed_at) VALUES (:id, :direction, :currency, "
    ":amount, :reference, :value_date, :reason, 'proposed', :by, :now)"
)
_DECIDE = text(
    "UPDATE dealing_buffer_capital SET status = :status, decided_by = :by, decided_at = :now, "
    "decision_reason = :reason, value_date = :value_date, ledger_entry_id = :entry WHERE id = :id"
)
# The lock the desk takes before reading the buffer to fund a withdrawal, so money never leaves
# while a withdrawal is being decided against it.
_LOCK_BUFFER = text("SELECT pg_advisory_xact_lock(hashtext('dealing_buffer'), hashtext(:currency))")


@dataclass(frozen=True, slots=True)
class MovementView:
    id: str
    direction: Direction
    amount: Money
    # Paying in: the bank's reference. Taking out quotes `id` on the transfer.
    bank_reference: str | None
    # Paying in: the day it arrived. Taking out: the day it was approved, once it is.
    value_date: date | None
    reason: str
    status: MovementStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    ledger_entry_id: str | None


# Queries -----------------------------------------------------------------------------------------


async def get_movement(session: AsyncSession, movement_id: str) -> MovementView:
    row = (await session.execute(_GET, {"id": movement_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def movements(
    session: AsyncSession, currency: Currency, *, limit: int = 50
) -> list[MovementView]:
    """Movements of Finvest's money in a currency, newest first."""
    rows = await session.execute(_LIST, {"currency": currency.value, "limit": limit})
    return [_view(row) for row in rows.all()]


async def waiting(session: AsyncSession, *, limit: int = 100) -> list[MovementView]:
    """Movements waiting for a second person, oldest first."""
    rows = await session.execute(_WAITING, {"limit": limit})
    return [_view(row) for row in rows.all()]


async def paid_in(session: AsyncSession, currency: Currency) -> Money:
    """What Finvest has in the buffer: paid in, less taken out."""
    return await statements.total_balance(session, CAPITAL, currency)


# Commands ----------------------------------------------------------------------------------------


async def propose(
    session: AsyncSession,
    staff: Actor,
    *,
    direction: Direction,
    amount: Money,
    reason: str,
    bank_reference: str | None = None,
    value_date: date | None = None,
    clock: Clock,
) -> MovementView:
    """Save a movement for a second member of staff to approve. Nothing is posted yet.

    Paying in is recorded once the money is on the trust account. Taking out is proposed before
    any money leaves, and refused now if the buffer or what Finvest paid in could not cover it.
    """
    _staff_only(staff)
    proposal = Proposal(direction, amount, reason, bank_reference, value_date)
    refusal = check_proposal(proposal, business_date(clock.now()))
    if refusal is not None:
        raise _refused(refusal)
    if value_date is not None:
        closed = await ledger_closing.closed_through(session)
        if closed is not None and value_date <= closed:
            raise _invalid("value_date", "value_date_in_a_closed_day")
    if direction is Direction.OUT:
        await _check_taking_out(session, amount, clock)
    movement_id = push_id()
    try:
        async with session.begin_nested():
            await session.execute(
                _INSERT,
                {
                    "id": movement_id,
                    "direction": direction.value,
                    "currency": amount.currency.value,
                    "amount": amount.minor,
                    "reference": (bank_reference or "").strip() or None,
                    "value_date": value_date,
                    "reason": reason.strip(),
                    "by": staff.id,
                    "now": clock.now(),
                },
            )
    except IntegrityError as error:
        # This bank reference is already recorded as paid in.
        raise _invalid("bank_reference", "already_recorded") from error
    action = f"dealing.buffer_capital.{direction}_proposed"
    await _audit(session, staff, "change", action, movement_id, clock, reason=reason.strip())
    return await get_movement(session, movement_id)


async def approve(
    session: AsyncSession, staff: Actor, movement_id: str, clock: Clock
) -> MovementView:
    """The second person. Posts the movement to the ledger, once.

    For money taken out, approval dates it today, and the transfer is then made quoting the
    movement's ID. The buffer and what Finvest paid in are checked again first."""
    view = await _for_decision(session, staff, movement_id)
    if view.direction is Direction.OUT:
        await _check_taking_out(session, view.amount, clock)
    value_date = view.value_date or business_date(clock.now())
    currency = view.amount.currency
    opened = {
        code: (await ledger.open_account(session, spec, clock)).id
        for code, spec in accounts(currency).items()
    }
    draft = posting(
        view.id,
        view.direction,
        view.amount,
        value_date,
        bank_account_id=opened["bank.trust"],
        capital_account_id=opened[CAPITAL],
    )
    async with session.begin_nested():
        try:
            posted = await ledger.post_entry(session, draft, clock)
        except InsufficientFunds:
            raise _refused(Refusal.MORE_THAN_PAID_IN) from None
        except DayClosed:
            # The day the money arrived closed after it was recorded. It cannot be posted on
            # that day any more: decline this one and record it again on an open day.
            raise _invalid("value_date", "day_closed_decline_and_record_again") from None
        await session.execute(
            _DECIDE,
            {
                "id": view.id,
                "status": MovementStatus.APPROVED.value,
                "by": staff.id,
                "now": clock.now(),
                "reason": None,
                "value_date": value_date,
                "entry": posted.id,
            },
        )
    action = f"dealing.buffer_capital.{view.direction}_approved"
    second = "Second signature on Finvest's money in the buffer"
    await _audit(session, staff, "approval", action, view.id, clock, reason=second)
    return await get_movement(session, view.id)


async def decline(
    session: AsyncSession, staff: Actor, movement_id: str, reason: str, clock: Clock
) -> MovementView:
    """The second person refuses it, with a reason. Nothing is posted."""
    if not reason.strip():
        raise _refused(Refusal.REASON_REQUIRED)
    view = await _for_decision(session, staff, movement_id)
    await session.execute(
        _DECIDE,
        {
            "id": view.id,
            "status": MovementStatus.DECLINED.value,
            "by": staff.id,
            "now": clock.now(),
            "reason": reason.strip(),
            "value_date": view.value_date,
            "entry": None,
        },
    )
    action = f"dealing.buffer_capital.{view.direction}_declined"
    await _audit(session, staff, "approval", action, view.id, clock, reason=reason.strip())
    return await get_movement(session, view.id)


# Internals ---------------------------------------------------------------------------------------


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff" or actor.id is None:
        raise ApplicationError("forbidden")


async def _for_decision(session: AsyncSession, staff: Actor, movement_id: str) -> MovementView:
    _staff_only(staff)
    assert staff.id is not None
    row = (await session.execute(_LOCK, {"id": movement_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    view = _view(row)
    refusal = check_decision(view.status, view.proposed_by, staff.id)
    if refusal is not None:
        raise _refused(refusal)
    return view


async def _check_taking_out(session: AsyncSession, amount: Money, clock: Clock) -> None:
    await session.execute(_LOCK_BUFFER, {"currency": amount.currency.value})
    state = await withdrawals.buffer_now(session, amount.currency, clock)
    refusal = check_taking_out(amount, await paid_in(session, amount.currency), state.buffer)
    if refusal is not None:
        raise _refused(refusal)


def _view(row: Row[Any]) -> MovementView:
    return MovementView(
        id=row.id,
        direction=Direction(row.direction),
        amount=Money(row.amount_minor, Currency(row.currency)),
        bank_reference=row.bank_reference,
        value_date=row.value_date,
        reason=row.reason,
        status=MovementStatus(row.status),
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
        ledger_entry_id=row.ledger_entry_id,
    )


_FIELDS = {
    Refusal.REASON_REQUIRED: "reason",
    Refusal.AMOUNT_NOT_POSITIVE: "amount",
    Refusal.REFERENCE_REQUIRED: "bank_reference",
    Refusal.VALUE_DATE_REQUIRED: "value_date",
    Refusal.VALUE_DATE_IN_FUTURE: "value_date",
    Refusal.NOT_FOR_TAKING_OUT: "direction",
    Refusal.MORE_THAN_PAID_IN: "amount",
    Refusal.MORE_THAN_THE_BUFFER: "amount",
}


def _refused(refusal: Refusal) -> ApplicationError:
    if refusal in _FIELDS:
        return _invalid(_FIELDS[refusal], refusal.value)
    if refusal is Refusal.ALREADY_DECIDED:
        return ApplicationError("conflict")
    return ApplicationError("forbidden")


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


async def _audit(
    session: AsyncSession,
    actor: Actor,
    category: AuditCategory,
    action: str,
    movement_id: str,
    clock: Clock,
    *,
    reason: str | None = None,
) -> None:
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=action,
            resource_type=RESOURCE,
            resource_id=movement_id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
        ),
        clock,
    )
