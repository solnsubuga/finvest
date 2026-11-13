"""The liquidity buffer over HTTP: its rules and Finvest's own money in it, for staff with
`dealing_desk.view`.

`GET /staff/dealing/buffer` is the whole screen for one currency. Changing the rules and moving
Finvest's money take `dealing_orders.change`, and each is proposed by one member of staff and
approved by a different one, who must have confirmed it is them in the last few minutes. Every
command takes an `Idempotency-Key`, so a retry never proposes or posts twice, and answers with
the screen as it now stands. Reasons and notes are staff free text: they go to the database and
the audit trail, never to the logs.
"""

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.infrastructure.idempotency import Receipt, run_once
from app.modules.dealing import buffer, capital, withdrawals
from app.modules.dealing.domain.buffer_changes import ProposalStatus
from app.modules.dealing.domain.capital import Direction, MovementStatus
from app.modules.dealing.domain.liquidity import BufferLevel, BufferRules
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.shared.clock import Clock
from app.shared.contracts import Contract
from app.shared.errors import ErrorEnvelope
from app.shared.money import Currency, Money

MAX_AMOUNT_MINOR = 10**15
_REFUSALS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 422)
}

AmountMinor = Annotated[int, Field(ge=1, le=MAX_AMOUNT_MINOR)]
CurrencyIn = Annotated[Currency, Field(strict=False)]
DirectionIn = Annotated[Direction, Field(strict=False)]
DateIn = Annotated[date, Field(strict=False)]
Reason = Annotated[str, Field(min_length=1, max_length=500)]
BankReference = Annotated[str, Field(min_length=1, max_length=100)]
WorkingDays = Annotated[int, Field(ge=1, le=30)]


class Step(StrEnum):
    APPROVE = "approve"
    SEND_BACK = "send_back"
    DECLINE = "decline"
    WITHDRAW = "withdraw"


# Contracts --------------------------------------------------------------------------------------


class AmountOut(Contract):
    minor: int
    currency: Currency


class RulesOut(Contract):
    effective_from: date
    target: AmountOut
    floor: AmountOut
    from_buffer_up_to: AmountOut
    pay_within_working_days: int


class RulesProposalOut(Contract):
    id: str
    rules: RulesOut
    reason: str
    status: ProposalStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    # What the member of staff reading may do with it now.
    allowed: list[Step]


class MovementOut(Contract):
    id: str
    direction: Direction
    amount: AmountOut
    # Paying in: the bank's reference. Taking out: quote `id` on the transfer.
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
    allowed: list[Step]


class BufferScreenOut(Contract):
    currency: Currency
    # The buffer today, after the withdrawals already promised.
    buffer: AmountOut
    level: BufferLevel
    in_force: RulesOut
    # Every version, oldest first, including any that start later.
    versions: list[RulesOut]
    proposals: list[RulesProposalOut]
    # What Finvest has in the buffer: paid in, less taken out.
    finvest_money: AmountOut
    movements: list[MovementOut]


class RulesIn(Contract):
    currency: CurrencyIn
    effective_from: DateIn
    target_minor: AmountMinor
    floor_minor: AmountMinor
    from_buffer_up_to_minor: AmountMinor
    pay_within_working_days: WorkingDays
    reason: Reason


class NoteIn(Contract):
    note: Reason


class MovementIn(Contract):
    direction: DirectionIn
    currency: CurrencyIn
    amount_minor: AmountMinor
    reason: Reason
    # Paying in only: the bank's reference and the day the money arrived.
    bank_reference: BankReference | None = None
    value_date: DateIn | None = None


class DeclineIn(Contract):
    reason: Reason


# Wiring -----------------------------------------------------------------------------------------


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


AppClock = Annotated[Clock, Depends(_clock)]
DeskStaff = Annotated[StaffSession, Depends(require_permission("dealing_desk.view"))]
Changer = Annotated[StaffSession, Depends(require_permission("dealing_orders.change"))]
# Approving as the second person needs a passkey or app code from the last few minutes.
Approver = Annotated[StaffSession, Depends(require_confirmed("dealing_orders.change"))]


def _amount(money: Money) -> AmountOut:
    return AmountOut(minor=money.minor, currency=money.currency)


def _rules_out(rules: BufferRules) -> RulesOut:
    return RulesOut(
        effective_from=rules.effective_from,
        target=_amount(rules.target),
        floor=_amount(rules.floor),
        from_buffer_up_to=_amount(rules.from_buffer_up_to),
        pay_within_working_days=rules.pay_within_working_days,
    )


def _may_change(staff: StaffSession) -> bool:
    return "dealing_orders.change" in staff.permissions


def _steps(
    staff: StaffSession, proposed_by: str, waiting: bool, deciding: list[Step]
) -> list[Step]:
    """What the reader may do: the second person decides, and the first may withdraw a change."""
    if not waiting or not _may_change(staff):
        return []
    if proposed_by == staff.staff_id:
        return [Step.WITHDRAW] if Step.SEND_BACK in deciding else []
    return deciding


async def _screen(
    session: AsyncSession, staff: StaffSession, currency: Currency, clock: Clock
) -> BufferScreenOut:
    state = await withdrawals.buffer_now(session, currency, clock)
    rule_steps = [Step.APPROVE, Step.SEND_BACK]
    movement_steps = [Step.APPROVE, Step.DECLINE]
    return BufferScreenOut(
        currency=currency,
        buffer=_amount(state.buffer),
        level=state.level,
        in_force=_rules_out(state.rules),
        versions=[_rules_out(v) for v in await buffer.versions(session, currency)],
        proposals=[
            RulesProposalOut(
                id=p.id,
                rules=_rules_out(p.rules),
                reason=p.reason,
                status=p.status,
                proposed_by=p.proposed_by,
                proposed_at=p.proposed_at,
                decided_by=p.decided_by,
                decided_at=p.decided_at,
                decision_note=p.decision_note,
                allowed=_steps(
                    staff, p.proposed_by, p.status is ProposalStatus.PROPOSED, rule_steps
                ),
            )
            for p in await buffer.proposals(session, currency)
        ],
        finvest_money=_amount(await capital.paid_in(session, currency)),
        movements=[
            MovementOut(
                id=m.id,
                direction=m.direction,
                amount=_amount(m.amount),
                bank_reference=m.bank_reference,
                value_date=m.value_date,
                reason=m.reason,
                status=m.status,
                proposed_by=m.proposed_by,
                proposed_at=m.proposed_at,
                decided_by=m.decided_by,
                decided_at=m.decided_at,
                decision_reason=m.decision_reason,
                allowed=_steps(
                    staff, m.proposed_by, m.status is MovementStatus.PROPOSED, movement_steps
                ),
            )
            for m in await capital.movements(session, currency)
        ],
    )


async def _command(
    request: Request,
    staff: StaffSession,
    clock: Clock,
    *,
    operation: str,
    key: str,
    body: dict[str, object],
    command: Callable[[AsyncSession], Awaitable[Currency]],
) -> BufferScreenOut:
    """Run one command once per key, then answer with the screen in its currency."""
    database: Database = request.app.state.database
    actor = staff.actor
    async with database.actor_transaction(actor) as session:

        async def run() -> Receipt:
            currency = await command(session)
            return Receipt(200, {"currency": currency.value})

        outcome = await run_once(
            session,
            actor=actor,
            operation=operation,
            key=key,
            request=body,
            now=clock.now(),
            command=run,
        )
        saved = outcome.receipt.response.get("currency")
        currency = Currency(saved) if isinstance(saved, str) else Currency.UGX
        return await _screen(session, staff, currency, clock)


# Routes -----------------------------------------------------------------------------------------

router = APIRouter(prefix="/staff/dealing/buffer", tags=["dealing desk"], responses=_REFUSALS)


@router.get("")
async def get_buffer(
    request: Request,
    staff: DeskStaff,
    clock: AppClock,
    currency: Annotated[Currency, Query()] = Currency.UGX,
) -> BufferScreenOut:
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        return await _screen(session, staff, currency, clock)


@router.post("/rules")
async def propose_rules(
    request: Request, staff: Changer, clock: AppClock, key: IdempotencyKey, body: RulesIn
) -> BufferScreenOut:
    """Propose a new version of the rules, from a later day, for a second person to approve."""

    async def command(session: AsyncSession) -> Currency:
        view = await buffer.propose_rules(
            session,
            staff.actor,
            effective_from=body.effective_from,
            target=Money(body.target_minor, body.currency),
            floor=Money(body.floor_minor, body.currency),
            from_buffer_up_to=Money(body.from_buffer_up_to_minor, body.currency),
            pay_within_working_days=body.pay_within_working_days,
            reason=body.reason,
            clock=clock,
        )
        return view.rules.currency

    return await _command(
        request, staff, clock, operation="dealing.propose_buffer_rules", key=key,
        body=body.model_dump(mode="json"), command=command,
    )  # fmt: skip


@router.post("/rules/{proposal_id}/approve", summary="Approve a rules change as the second person")
async def approve_rules(
    request: Request, staff: Approver, clock: AppClock, key: IdempotencyKey, proposal_id: str
) -> BufferScreenOut:
    async def command(session: AsyncSession) -> Currency:
        view = await buffer.approve_rules(session, staff.actor, proposal_id, clock)
        return view.rules.currency

    return await _command(
        request, staff, clock, operation="dealing.approve_buffer_rules", key=key,
        body={"proposal_id": proposal_id}, command=command,
    )  # fmt: skip


@router.post("/rules/{proposal_id}/send-back", summary="Send a rules change back with a note")
async def send_back_rules(
    request: Request,
    staff: Changer,
    clock: AppClock,
    key: IdempotencyKey,
    proposal_id: str,
    body: NoteIn,
) -> BufferScreenOut:
    async def command(session: AsyncSession) -> Currency:
        view = await buffer.send_back_rules(session, staff.actor, proposal_id, body.note, clock)
        return view.rules.currency

    return await _command(
        request, staff, clock, operation="dealing.send_back_buffer_rules", key=key,
        body={"proposal_id": proposal_id, "note": body.note}, command=command,
    )  # fmt: skip


@router.post("/rules/{proposal_id}/withdraw", summary="Withdraw a rules change you proposed")
async def withdraw_rules(
    request: Request, staff: Changer, clock: AppClock, key: IdempotencyKey, proposal_id: str
) -> BufferScreenOut:
    async def command(session: AsyncSession) -> Currency:
        view = await buffer.withdraw_rules(session, staff.actor, proposal_id, clock)
        return view.rules.currency

    return await _command(
        request, staff, clock, operation="dealing.withdraw_buffer_rules", key=key,
        body={"proposal_id": proposal_id}, command=command,
    )  # fmt: skip


@router.post("/capital")
async def propose_movement(
    request: Request, staff: Changer, clock: AppClock, key: IdempotencyKey, body: MovementIn
) -> BufferScreenOut:
    """Record Finvest's money paid into the trust account, or propose taking some out, for a
    second person to approve. Nothing is posted yet."""

    async def command(session: AsyncSession) -> Currency:
        view = await capital.propose(
            session,
            staff.actor,
            direction=body.direction,
            amount=Money(body.amount_minor, body.currency),
            reason=body.reason,
            bank_reference=body.bank_reference,
            value_date=body.value_date,
            clock=clock,
        )
        return view.amount.currency

    return await _command(
        request, staff, clock, operation="dealing.propose_buffer_capital", key=key,
        body=body.model_dump(mode="json"), command=command,
    )  # fmt: skip


@router.post(
    "/capital/{movement_id}/approve",
    summary="Approve Finvest's money paid in or taken out, as the second person, which posts it",
)
async def approve_movement(
    request: Request, staff: Approver, clock: AppClock, key: IdempotencyKey, movement_id: str
) -> BufferScreenOut:
    async def command(session: AsyncSession) -> Currency:
        view = await capital.approve(session, staff.actor, movement_id, clock)
        return view.amount.currency

    return await _command(
        request, staff, clock, operation="dealing.approve_buffer_capital", key=key,
        body={"movement_id": movement_id}, command=command,
    )  # fmt: skip


@router.post("/capital/{movement_id}/decline", summary="Decline a movement with a reason")
async def decline_movement(
    request: Request,
    staff: Changer,
    clock: AppClock,
    key: IdempotencyKey,
    movement_id: str,
    body: DeclineIn,
) -> BufferScreenOut:
    async def command(session: AsyncSession) -> Currency:
        view = await capital.decline(session, staff.actor, movement_id, body.reason, clock)
        return view.amount.currency

    return await _command(
        request, staff, clock, operation="dealing.decline_buffer_capital", key=key,
        body={"movement_id": movement_id, "reason": body.reason}, command=command,
    )  # fmt: skip
