"""Coupons paid in on the dealing desk's bonds, over HTTP, for staff with `dealing_desk.view`.

`GET /staff/dealing/bond-coupons` is the whole screen for one currency: the coupons still to
record, and those recorded, newest coupon first, with the steps the reader may take on each.
Recording and deciding take `dealing_orders.change`, and approving asks the second person to
confirm it is them. Every command takes an `Idempotency-Key`, so a retry never records or posts
twice, and answers with the screen as it now stands. Notes and reasons are staff free text:
they go to the database and the audit trail, never to the logs.
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
from app.modules.dealing import bond_coupons
from app.modules.dealing.domain.bond_coupons import CouponStatus, check_decision
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
DateIn = Annotated[date, Field(strict=False)]
IssueCode = Annotated[str, Field(min_length=1, max_length=24, pattern=r"^[A-Z0-9-]+$")]
Note = Annotated[str, Field(min_length=1, max_length=500)]
BankReference = Annotated[str, Field(min_length=1, max_length=100)]


class Step(StrEnum):
    APPROVE = "approve"
    DECLINE = "decline"


# Contracts --------------------------------------------------------------------------------------


class AmountOut(Contract):
    minor: int
    currency: Currency


class CouponDueOut(Contract):
    issue_code: str
    name: str
    coupon_date: date
    # The face value held the day before, and the coupon it earns before any tax.
    face: AmountOut
    expected: AmountOut


class CouponOut(Contract):
    id: str
    issue_code: str
    coupon_date: date
    face: AmountOut
    expected: AmountOut
    # What reached the trust account, on `value_date`, and why it differs if it does.
    amount: AmountOut
    value_date: date
    bank_reference: str
    note: str | None
    status: CouponStatus
    recorded_by: str
    recorded_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    allowed: list[Step]


class CouponsOut(Contract):
    currency: Currency
    due: list[CouponDueOut]
    coupons: list[CouponOut]
    # Whether the reader may record a coupon.
    may_change: bool


class CouponIn(Contract):
    issue_code: IssueCode
    coupon_date: DateIn
    currency: CurrencyIn
    amount_minor: AmountMinor
    value_date: DateIn
    bank_reference: BankReference
    # Required when what arrived differs from what the face value earns.
    note: Note | None = None


class DeclineIn(Contract):
    reason: Note


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


def _may_change(staff: StaffSession) -> bool:
    return "dealing_orders.change" in staff.permissions


async def _screen(
    session: AsyncSession, staff: StaffSession, currency: Currency, clock: Clock
) -> CouponsOut:
    may_change = _may_change(staff)
    reader = staff.actor.id or ""
    return CouponsOut(
        currency=currency,
        due=[
            CouponDueOut(
                issue_code=d.issue_code,
                name=d.name,
                coupon_date=d.coupon_date,
                face=_amount(d.face),
                expected=_amount(d.expected),
            )
            for d in await bond_coupons.due(session, currency, clock)
        ],
        coupons=[
            CouponOut(
                id=c.id,
                issue_code=c.issue_code,
                coupon_date=c.coupon_date,
                face=_amount(c.face),
                expected=_amount(c.expected),
                amount=_amount(c.amount),
                value_date=c.value_date,
                bank_reference=c.bank_reference,
                note=c.note,
                status=c.status,
                recorded_by=c.recorded_by,
                recorded_at=c.recorded_at,
                decided_by=c.decided_by,
                decided_at=c.decided_at,
                decision_reason=c.decision_reason,
                allowed=[Step.APPROVE, Step.DECLINE]
                if may_change and check_decision(c.status, c.recorded_by, reader) is None
                else [],
            )
            for c in await bond_coupons.coupons(session, currency)
        ],
        may_change=may_change,
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
) -> CouponsOut:
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

router = APIRouter(prefix="/staff/dealing/bond-coupons", tags=["dealing desk"], responses=_REFUSALS)


@router.get("")
async def get_bond_coupons(
    request: Request,
    staff: DeskStaff,
    clock: AppClock,
    currency: Annotated[Currency, Query()] = Currency.UGX,
) -> CouponsOut:
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        return await _screen(session, staff, currency, clock)


@router.post("")
async def record_coupon(
    request: Request, staff: Changer, clock: AppClock, key: IdempotencyKey, body: CouponIn
) -> CouponsOut:
    """Record a coupon from the custodian's note and the bank statement, for a second person
    to approve. Nothing is posted yet."""

    async def command(session: AsyncSession) -> Currency:
        view = await bond_coupons.record(
            session,
            staff.actor,
            issue_code=body.issue_code,
            coupon_date=body.coupon_date,
            amount=Money(body.amount_minor, body.currency),
            value_date=body.value_date,
            bank_reference=body.bank_reference,
            note=body.note,
            clock=clock,
        )
        return view.amount.currency

    return await _command(
        request,
        staff,
        clock,
        operation="dealing.record_bond_coupon",
        key=key,
        body={
            "issue_code": body.issue_code,
            "coupon_date": body.coupon_date.isoformat(),
            "currency": body.currency.value,
            "amount_minor": body.amount_minor,
            "value_date": body.value_date.isoformat(),
            "bank_reference": body.bank_reference,
            "note": body.note,
        },
        command=command,
    )


@router.post("/{coupon_id}/approve", summary="Approve a coupon as the second person, posting it")
async def approve_coupon(
    request: Request, staff: Approver, clock: AppClock, key: IdempotencyKey, coupon_id: str
) -> CouponsOut:
    async def command(session: AsyncSession) -> Currency:
        view = await bond_coupons.approve(session, staff.actor, coupon_id, clock)
        return view.amount.currency

    return await _command(
        request,
        staff,
        clock,
        operation="dealing.approve_bond_coupon",
        key=key,
        body={"coupon_id": coupon_id},
        command=command,
    )


@router.post("/{coupon_id}/decline", summary="Decline a coupon with a reason")
async def decline_coupon(
    request: Request,
    staff: Changer,
    clock: AppClock,
    key: IdempotencyKey,
    coupon_id: str,
    body: DeclineIn,
) -> CouponsOut:
    async def command(session: AsyncSession) -> Currency:
        view = await bond_coupons.decline(session, staff.actor, coupon_id, body.reason, clock)
        return view.amount.currency

    return await _command(
        request,
        staff,
        clock,
        operation="dealing.decline_bond_coupon",
        key=key,
        body={"coupon_id": coupon_id, "reason": body.reason},
        command=command,
    )
