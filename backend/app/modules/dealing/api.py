"""The dealing desk over HTTP, for staff with `dealing_desk.view`.

`GET /staff/dealing/desk` is the whole screen. Every command takes an `Idempotency-Key`: a
repeat of the same request is answered from its saved outcome, so a retry from a weak
connection never prepares, approves or settles twice. Every command answers with the desk as
it now stands. Each request is one transaction for the member of staff.
"""

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from app.api.idempotency import IdempotencyKey
from app.infrastructure.database import Database
from app.infrastructure.idempotency import Receipt, run_once
from app.modules.dealing import desk as desk_view
from app.modules.dealing import orders
from app.modules.dealing.domain.liquidity import BufferLevel
from app.modules.dealing.domain.orders import Action, Fill, InvalidOrder, OrderKind, OrderStatus
from app.modules.dealing.domain.position import Cover
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.shared.clock import Clock
from app.shared.contracts import Contract
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.money import Currency, Money

MAX_AMOUNT_MINOR = 10**15
_REFUSALS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 422)
}

AmountMinor = Annotated[int, Field(ge=1, le=MAX_AMOUNT_MINOR)]
CurrencyIn = Annotated[Currency, Field(strict=False)]
KindIn = Annotated[OrderKind, Field(strict=False)]
DateIn = Annotated[date, Field(strict=False)]
ProductCode = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9-]+$")]
Note = Annotated[str, Field(min_length=1, max_length=500)]
Reference = Annotated[str, Field(min_length=1, max_length=64)]


# Contracts --------------------------------------------------------------------------------------


class AmountOut(Contract):
    minor: int
    currency: Currency


class PositionOut(Contract):
    product_code: str
    product_name: str
    # True for Treasury bills, which never trade above their face value.
    discount_only: bool
    lot_size: AmountOut
    clients_owed: AmountOut
    bills_held: AmountOut
    to_buy: AmountOut
    on_order: AmountOut
    face_held: AmountOut
    cover: Cover


class FillOut(Contract):
    face: AmountOut
    amount: AmountOut
    settled_on: date
    matures_on: date
    custodian_reference: str


class OrderOut(Contract):
    id: str
    kind: OrderKind
    product_code: str
    product_name: str
    face: AmountOut
    status: OrderStatus
    prepared_by: str
    prepared_at: datetime
    # Why it was sent back, or why its result was rejected.
    note: str | None
    result: FillOut | None
    result_recorded_by: str | None
    allowed: list[Action]


class BufferOut(Contract):
    # The buffer before paying the early withdrawals still waiting.
    balance: AmountOut
    # The buffer once they are paid. The cash for them is already counted as owed.
    after_promised: AmountOut
    target: AmountOut
    floor: AmountOut
    level: BufferLevel
    from_buffer_up_to: AmountOut
    pay_within_working_days: int
    short: AmountOut
    to_raise: AmountOut


class WithdrawalsOut(Contract):
    asked: AmountOut
    clients: int


class DeskOut(Contract):
    money_to_place: AmountOut
    bills_held: AmountOut
    # None until the holdings are compared with a custodian statement.
    matches_custodian: bool | None
    buffer: BufferOut
    early_withdrawals: WithdrawalsOut
    positions: list[PositionOut]
    orders: list[OrderOut]


class NewOrderIn(Contract):
    kind: KindIn
    product_code: ProductCode
    face_minor: AmountMinor
    currency: CurrencyIn


class NoteIn(Contract):
    note: Note


class FillIn(Contract):
    face_minor: AmountMinor
    amount_minor: AmountMinor
    settled_on: DateIn
    matures_on: DateIn
    custodian_reference: Reference


class ResultIn(Contract):
    # None means nothing was dealt.
    fill: FillIn | None


# Wiring -----------------------------------------------------------------------------------------


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


AppClock = Annotated[Clock, Depends(_clock)]
DeskStaff = Annotated[StaffSession, Depends(require_permission("dealing_desk.view"))]
# Every order command. One can settle an order into the ledger, so seeing the desk is not enough.
OrderChanger = Annotated[StaffSession, Depends(require_permission("dealing_orders.change"))]
# Approving as the second person needs a passkey or app code from the last few minutes.
OrderApprover = Annotated[StaffSession, Depends(require_confirmed("dealing_orders.change"))]


def _may_change(staff: StaffSession) -> bool:
    return "dealing_orders.change" in staff.permissions


def _amount(money: Money) -> AmountOut:
    return AmountOut(minor=money.minor, currency=money.currency)


def _desk_out(view: desk_view.DeskView) -> DeskOut:
    buffer = view.buffer
    return DeskOut(
        money_to_place=_amount(view.money_to_place),
        bills_held=_amount(view.bills_held),
        matches_custodian=None,
        buffer=BufferOut(
            balance=_amount(buffer.buffer + view.waiting_amount),
            after_promised=_amount(buffer.buffer),
            target=_amount(buffer.rules.target),
            floor=_amount(buffer.rules.floor),
            level=buffer.level,
            from_buffer_up_to=_amount(buffer.rules.from_buffer_up_to),
            pay_within_working_days=buffer.rules.pay_within_working_days,
            short=_amount(buffer.short),
            to_raise=_amount(buffer.to_raise),
        ),
        early_withdrawals=WithdrawalsOut(
            asked=_amount(view.waiting_amount), clients=view.waiting_withdrawals
        ),
        positions=[
            PositionOut(
                product_code=p.product_code,
                product_name=p.product_name,
                discount_only=p.discount_only,
                lot_size=_amount(p.lot_size),
                clients_owed=_amount(p.clients_owed),
                bills_held=_amount(p.bills_held),
                to_buy=_amount(p.to_buy),
                on_order=_amount(p.on_order),
                face_held=_amount(p.face_held),
                cover=p.cover,
            )
            for p in view.positions
        ],
        orders=[_order_out(line) for line in view.orders],
    )


def _order_out(line: desk_view.OrderLine) -> OrderOut:
    order = line.order
    result = order.result
    return OrderOut(
        id=order.id,
        kind=order.kind,
        product_code=order.product_code,
        product_name=line.product_name,
        face=_amount(order.face),
        status=order.status,
        prepared_by=order.prepared_by,
        prepared_at=order.prepared_at,
        note=order.result_rejection_note or order.decision_note,
        result=None
        if result is None
        else FillOut(
            face=_amount(result.face),
            amount=_amount(result.amount),
            settled_on=result.settled_on,
            matures_on=result.matures_on,
            custodian_reference=result.custodian_reference,
        ),
        result_recorded_by=order.result_recorded_by,
        allowed=list(line.allowed),
    )


async def _command(
    request: Request,
    staff: StaffSession,
    clock: Clock,
    *,
    operation: str,
    key: str,
    body: dict[str, object],
    currency: Currency,
    command: Callable[..., Awaitable[orders.OrderView]],
) -> DeskOut:
    """Run one order command once per key, then answer with the desk in the order's currency."""
    database: Database = request.app.state.database
    actor = staff.actor
    async with database.actor_transaction(actor) as session:

        async def run() -> Receipt:
            view = await command(session)
            return Receipt(200, {"order_id": view.id, "currency": view.face.currency.value})

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
        shown = Currency(saved) if isinstance(saved, str) else currency
        return _desk_out(
            await desk_view.desk(session, actor, shown, clock, may_change=_may_change(staff))
        )


async def _order_currency(request: Request, staff: StaffSession, order_id: str) -> Currency:
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        return (await orders.get_order(session, order_id)).face.currency


# Routes -----------------------------------------------------------------------------------------

router = APIRouter(prefix="/staff/dealing", tags=["dealing desk"], responses=_REFUSALS)


@router.get("/desk")
async def get_desk(
    request: Request,
    staff: DeskStaff,
    clock: AppClock,
    currency: Annotated[Currency, Query()] = Currency.UGX,
) -> DeskOut:
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        return _desk_out(
            await desk_view.desk(
                session, staff.actor, currency, clock, may_change=_may_change(staff)
            )
        )


@router.post("/orders")
async def prepare_order(
    request: Request, staff: OrderChanger, clock: AppClock, key: IdempotencyKey, body: NewOrderIn
) -> DeskOut:
    """Prepare an order for a second member of staff to approve."""
    face = Money(body.face_minor, body.currency)
    return await _command(
        request,
        staff,
        clock,
        operation="dealing.prepare_order",
        key=key,
        body={
            "kind": body.kind.value,
            "product_code": body.product_code,
            "face_minor": body.face_minor,
            "currency": body.currency.value,
        },
        currency=body.currency,
        command=lambda session: orders.prepare_order(
            session,
            staff.actor,
            kind=body.kind,
            product_code=body.product_code,
            face=face,
            clock=clock,
        ),
    )


def _step(
    operation: str,
    run: Callable[..., Awaitable[orders.OrderView]],
) -> Callable[..., Awaitable[DeskOut]]:
    """A command on one order that takes nothing but the order."""

    async def route(
        request: Request, staff: OrderChanger, clock: AppClock, key: IdempotencyKey, order_id: str
    ) -> DeskOut:
        return await _command(
            request,
            staff,
            clock,
            operation=operation,
            key=key,
            body={"order_id": order_id},
            currency=await _order_currency(request, staff, order_id),
            command=lambda session: run(session, staff.actor, order_id, clock),
        )

    return route


def _approval(
    operation: str,
    run: Callable[..., Awaitable[orders.OrderView]],
) -> Callable[..., Awaitable[DeskOut]]:
    """`_step` for the second person's approval, which asks them to confirm it is them."""

    async def route(
        request: Request, staff: OrderApprover, clock: AppClock, key: IdempotencyKey, order_id: str
    ) -> DeskOut:
        return await _command(
            request,
            staff,
            clock,
            operation=operation,
            key=key,
            body={"order_id": order_id},
            currency=await _order_currency(request, staff, order_id),
            command=lambda session: run(session, staff.actor, order_id, clock),
        )

    return route


def _noted(
    operation: str,
    run: Callable[..., Awaitable[orders.OrderView]],
) -> Callable[..., Awaitable[DeskOut]]:
    """A command on one order that takes a note."""

    async def route(
        request: Request,
        staff: OrderChanger,
        clock: AppClock,
        key: IdempotencyKey,
        order_id: str,
        body: NoteIn,
    ) -> DeskOut:
        return await _command(
            request,
            staff,
            clock,
            operation=operation,
            key=key,
            body={"order_id": order_id, "note": body.note},
            currency=await _order_currency(request, staff, order_id),
            command=lambda session: run(session, staff.actor, order_id, body.note, clock),
        )

    return route


router.post("/orders/{order_id}/approve", summary="Approve an order as the second person")(
    _approval("dealing.approve_order", orders.approve_order)
)
router.post("/orders/{order_id}/withdraw", summary="Withdraw an order you prepared")(
    _step("dealing.withdraw_order", orders.withdraw_order)
)
router.post(
    "/orders/{order_id}/confirm-result",
    summary="Confirm a recorded result as the second person, which settles the order",
)(_approval("dealing.confirm_result", orders.confirm_result))
router.post("/orders/{order_id}/send-back", summary="Send an order back with a note")(
    _noted("dealing.send_back_order", orders.send_back_order)
)
router.post("/orders/{order_id}/reject-result", summary="Reject a recorded result with a note")(
    _noted("dealing.reject_result", orders.reject_result)
)


@router.post("/orders/{order_id}/result")
async def record_result(
    request: Request,
    staff: OrderChanger,
    clock: AppClock,
    key: IdempotencyKey,
    order_id: str,
    body: ResultIn,
) -> DeskOut:
    """Record what the custodian's note says was dealt. A fill of null means nothing was."""
    currency = await _order_currency(request, staff, order_id)
    given = body.fill
    try:
        fill = (
            None
            if given is None
            else Fill(
                face=Money(given.face_minor, currency),
                amount=Money(given.amount_minor, currency),
                settled_on=given.settled_on,
                matures_on=given.matures_on,
                custodian_reference=given.custodian_reference,
            )
        )
    except InvalidOrder:
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["fill"], code="invalid")]
        ) from None
    return await _command(
        request,
        staff,
        clock,
        operation="dealing.record_result",
        key=key,
        body={
            "order_id": order_id,
            "fill": None
            if given is None
            else {
                "face_minor": given.face_minor,
                "amount_minor": given.amount_minor,
                "settled_on": given.settled_on.isoformat(),
                "matures_on": given.matures_on.isoformat(),
                "custodian_reference": given.custodian_reference,
            },
        },
        currency=currency,
        command=lambda session: orders.record_result(session, staff.actor, order_id, fill, clock),
    )
