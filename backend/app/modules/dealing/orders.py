"""Dealing order commands and queries. Every function works inside the caller's transaction.

The caller opens `Database.actor_transaction(actor)` for the same actor it passes here. The
trigger and constraints of migration 0080 then hold whatever this code does: nobody approves
their own order or rejects a result they recorded, and what an order is for never changes.

Which staff may prepare, approve or record is a permission, checked by the route before it
calls in.

Confirming a result settles the order: it posts the ledger entry and records the bills bought
or the lots a sale took, and migration 0081 checks all three agree with the recorded result.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.dealing import holdings
from app.modules.dealing.domain.holdings import InvalidLot, draw
from app.modules.dealing.domain.orders import (
    LOT_SIZES,
    Fill,
    InvalidOrder,
    Order,
    OrderKind,
    OrderStatus,
    Refusal,
    check_approval,
    check_confirmation,
    check_new_order,
    check_result,
    check_result_rejection,
    check_send_back,
    check_withdrawal,
)
from app.modules.dealing.domain.settlement import (
    Settlement,
    accounts,
    settlement_entry,
)
from app.modules.dealing.events import FUND_FROM_SALE_KIND
from app.modules.investments import service as investments
from app.modules.investments.domain.products import Product, ProductKind
from app.modules.ledger import service as ledger
from app.modules.ledger.domain.errors import DayClosed, InsufficientFunds
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id

ORDER_RESOURCE = "dealing_order"
MAX_ORDERS = 200


@dataclass(frozen=True, slots=True)
class OrderView:
    id: str
    kind: OrderKind
    product_code: str
    face: Money
    status: OrderStatus
    prepared_by: str
    prepared_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    result: Fill | None
    result_recorded_by: str | None
    result_recorded_at: datetime | None
    result_rejection_note: str | None
    settled_by: str | None
    settled_at: datetime | None
    settlement_entry_id: str | None

    @property
    def order(self) -> Order:
        return Order(
            kind=self.kind,
            product_code=self.product_code,
            face=self.face,
            prepared_by=self.prepared_by,
            status=self.status,
            result_recorded_by=(
                self.result_recorded_by if self.status is OrderStatus.RESULT_RECORDED else None
            ),
        )


_COLUMNS = (
    "id, kind, product_code, currency, face_minor, status, prepared_by, prepared_at, decided_by, "
    "decided_at, decision_note, result_face_minor, result_amount_minor, result_settled_on, "
    "result_matures_on, custodian_reference, result_recorded_by, result_recorded_at, "
    "result_rejection_note, settled_by, settled_at, settlement_entry_id"
)
_BY_ID = text(f"SELECT {_COLUMNS} FROM dealing_orders WHERE id = :id")  # noqa: S608
_LOCKED = text(f"SELECT {_COLUMNS} FROM dealing_orders WHERE id = :id FOR UPDATE")  # noqa: S608
_OPEN = text(
    f"SELECT {_COLUMNS} FROM dealing_orders "  # noqa: S608
    "WHERE status IN ('prepared', 'approved', 'result_recorded') "
    "ORDER BY prepared_at, id LIMIT :limit"
)
_INSERT = text(
    "INSERT INTO dealing_orders (id, kind, product_code, currency, face_minor, prepared_by, "
    "prepared_at) VALUES (:id, :kind, :product_code, :currency, :face_minor, :prepared_by, :now)"
)
_DECIDE = text(
    "UPDATE dealing_orders SET status = :status, decided_by = :by, decided_at = :now, "
    "decision_note = :note WHERE id = :id"
)
_RECORD = text(
    "UPDATE dealing_orders SET status = :status, result_face_minor = :face, "
    "result_amount_minor = :amount, result_settled_on = :settled_on, "
    "result_matures_on = :matures_on, custodian_reference = :reference, "
    "result_recorded_by = :by, result_recorded_at = :now, result_rejection_note = NULL "
    "WHERE id = :id"
)
_REJECT_RESULT = text(
    "UPDATE dealing_orders SET status = 'approved', result_face_minor = NULL, "
    "result_amount_minor = NULL, result_settled_on = NULL, result_matures_on = NULL, "
    "custodian_reference = NULL, result_recorded_by = NULL, result_recorded_at = NULL, "
    "result_rejection_note = :note WHERE id = :id"
)
_SETTLE = text(
    "UPDATE dealing_orders SET status = 'settled', settled_by = :by, settled_at = :now, "
    "settlement_entry_id = :entry_id WHERE id = :id"
)
_INSERT_LOT = text(
    "INSERT INTO dealing_lots (id, order_id, product_code, currency, face_minor, cost_minor, "
    "settled_on, matures_on, custodian_reference) VALUES (:id, :order_id, :product_code, "
    ":currency, :face, :cost, :settled_on, :matures_on, :reference)"
)
_INSERT_SALE_PART = text(
    "INSERT INTO dealing_lot_sales (lot_id, order_id, face_minor, cost_minor) "
    "VALUES (:lot_id, :order_id, :face, :cost)"
)
# One instrument at a time: two sales of the same bills are worked out one after the other.
_LOCK_INSTRUMENT = text(
    "SELECT pg_advisory_xact_lock(hashtext('dealing_instrument'), hashtext(:instrument))"
)


# Queries -----------------------------------------------------------------------------------------


async def get_order(session: AsyncSession, order_id: str) -> OrderView:
    row = (await session.execute(_BY_ID, {"id": order_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def open_orders(session: AsyncSession, *, limit: int = MAX_ORDERS) -> list[OrderView]:
    """Orders that are not finished, oldest first."""
    rows = await session.execute(_OPEN, {"limit": max(1, min(limit, MAX_ORDERS))})
    return [_view(row) for row in rows.all()]


async def on_order(session: AsyncSession) -> dict[str, Money]:
    """Face value of the open orders to buy, by product code, for the position.

    Counted at face value because the cost is not known until the result is. For a Treasury
    bill that is never less than the cost.
    """
    totals: dict[str, Money] = {}
    for view in await open_orders(session):
        if view.kind.buys:
            totals[view.product_code] = (
                totals.get(view.product_code, Money.zero(view.face.currency)) + view.face
            )
    return totals


# Commands ----------------------------------------------------------------------------------------


async def prepare_order(
    session: AsyncSession,
    staff: Actor,
    *,
    kind: OrderKind,
    product_code: str,
    face: Money,
    clock: Clock,
) -> OrderView:
    """Save an order for a second member of staff to approve. Nothing is bought or sold."""
    _staff_only(staff)
    product = await _product(session, product_code, clock)
    try:
        order = Order(kind, product.code, face, _id(staff))
    except InvalidOrder as error:
        raise _invalid("face", "not_positive") from error
    if face.currency is not product.currency:
        raise _invalid("face", Refusal.CURRENCY_MISMATCH.value)
    held = await _face_held(session, product, clock)
    refusal = check_new_order(order, LOT_SIZES[product.currency], held)
    if refusal is not None:
        raise _refused(refusal)
    order_id = push_id()
    await session.execute(
        _INSERT,
        {
            "id": order_id,
            "kind": kind.value,
            "product_code": product.code,
            "currency": face.currency.value,
            "face_minor": face.minor,
            "prepared_by": staff.id,
            "now": clock.now(),
        },
    )
    await _audit(session, staff, "change", "dealing.order.prepared", order_id, clock, order=order)
    return await get_order(session, order_id)


async def approve_order(
    session: AsyncSession, staff: Actor, order_id: str, clock: Clock
) -> OrderView:
    """Approve an order as the second person. It may now be placed with the dealer."""
    view = await _locked(session, staff, order_id)
    _allow(check_approval(view.order, _id(staff)))
    await _decide(session, staff, view, OrderStatus.APPROVED, None, clock)
    await _audit(
        session,
        staff,
        "approval",
        "dealing.order.approved",
        view.id,
        clock,
        reason="Approved as the second person",
    )
    return await get_order(session, view.id)


async def send_back_order(
    session: AsyncSession, staff: Actor, order_id: str, note: str, clock: Clock
) -> OrderView:
    """Decline an order as the second person, with a note for whoever prepared it."""
    view = await _locked(session, staff, order_id)
    _allow(check_send_back(view.order, _id(staff), note))
    await _decide(session, staff, view, OrderStatus.SENT_BACK, note.strip(), clock)
    await _audit(session, staff, "approval", "dealing.order.sent_back", view.id, clock, reason=note)
    return await get_order(session, view.id)


async def withdraw_order(
    session: AsyncSession, staff: Actor, order_id: str, clock: Clock
) -> OrderView:
    """Take back an order nobody has decided. Only the person who prepared it can."""
    view = await _locked(session, staff, order_id)
    _allow(check_withdrawal(view.order, _id(staff)))
    await _decide(session, staff, view, OrderStatus.WITHDRAWN, None, clock)
    await _audit(session, staff, "change", "dealing.order.withdrawn", view.id, clock)
    return await get_order(session, view.id)


async def record_result(
    session: AsyncSession, staff: Actor, order_id: str, fill: Fill | None, clock: Clock
) -> OrderView:
    """Record what the custodian's note says was dealt. `None` means nothing was.

    Nothing is posted to the ledger. A second person confirms the result first.
    """
    view = await _locked(session, staff, order_id)
    product = await _product(session, view.product_code, clock)
    _allow(
        check_result(
            view.order,
            fill,
            business_date(clock.now()),
            discount_only=product.kind is ProductKind.TREASURY_BILL,
            term_days=product.term_days,
            face_held=await _face_held(
                session, product, clock, matures_on=fill.matures_on if fill else None
            ),
        )
    )
    status = OrderStatus.UNSUCCESSFUL if fill is None else OrderStatus.RESULT_RECORDED
    await session.execute(
        _RECORD,
        {
            "id": view.id,
            "status": status.value,
            "face": fill.face.minor if fill else None,
            "amount": fill.amount.minor if fill else None,
            "settled_on": fill.settled_on if fill else None,
            "matures_on": fill.matures_on if fill else None,
            "reference": fill.custodian_reference if fill else None,
            "by": staff.id,
            "now": clock.now(),
        },
    )
    action = "dealing.order.unsuccessful" if fill is None else "dealing.order.result_recorded"
    await _audit(session, staff, "change", action, view.id, clock, fill=fill)
    return await get_order(session, view.id)


async def reject_result(
    session: AsyncSession, staff: Actor, order_id: str, note: str, clock: Clock
) -> OrderView:
    """Reject a recorded result that does not match the custodian's note, as a second person.

    The order goes back to approved, and the result is recorded again.
    """
    view = await _locked(session, staff, order_id)
    _allow(check_result_rejection(view.order, _id(staff), note))
    await session.execute(_REJECT_RESULT, {"id": view.id, "note": note.strip()})
    await _audit(
        session, staff, "approval", "dealing.order.result_rejected", view.id, clock, reason=note
    )
    return await get_order(session, view.id)


async def confirm_result(
    session: AsyncSession, staff: Actor, order_id: str, clock: Clock
) -> OrderView:
    """Confirm a recorded result against the custodian's note, as a second person.

    This settles the order in the caller's transaction: the journal entry on the trust bank
    account, dated on the settlement date, and the lot bought or the parts of lots sold. A sale
    takes bills of the maturity it recorded, oldest purchase first. Refused when the bank
    account holds too little for a purchase or the settlement date is in a closed day.
    """
    view = await _locked(session, staff, order_id)
    _allow(check_confirmation(view.order, _id(staff)))
    fill = view.result
    assert fill is not None
    currency = fill.face.currency
    account_ids = {
        code: (await ledger.open_account(session, spec, clock)).id
        for code, spec in accounts(currency).items()
    }
    parts: list[tuple[str, Money, Money]] = []
    if view.kind.buys:
        cost = fill.amount
    else:
        await session.execute(
            _LOCK_INSTRUMENT, {"instrument": f"{view.product_code}:{fill.matures_on.isoformat()}"}
        )
        lots = await holdings.lots_of(
            session, view.product_code, fill.matures_on, on=fill.settled_on
        )
        try:
            draws = draw([held.lot for held in lots], fill.face)
        except InvalidLot:
            raise _refused(Refusal.MORE_THAN_HELD) from None
        parts = [(lots[part.lot].id, part.face, part.cost_released) for part in draws]
        cost = sum((released for _, _, released in parts), Money.zero(currency))
    draft = settlement_entry(Settlement(view.id, view.kind, fill, cost), account_ids)
    try:
        entry = await ledger.post_entry(session, draft, clock)
    except InsufficientFunds:
        raise _invalid("amount", "not_enough_cash") from None
    except DayClosed:
        raise _invalid("settled_on", "day_closed") from None
    if view.kind.buys:
        await session.execute(
            _INSERT_LOT,
            {
                "id": push_id(),
                "order_id": view.id,
                "product_code": view.product_code,
                "currency": currency.value,
                "face": fill.face.minor,
                "cost": cost.minor,
                "settled_on": fill.settled_on,
                "matures_on": fill.matures_on,
                "reference": fill.custodian_reference,
            },
        )
    for lot_id, face, released in parts:
        await session.execute(
            _INSERT_SALE_PART,
            {"lot_id": lot_id, "order_id": view.id, "face": face.minor, "cost": released.minor},
        )
    await session.execute(
        _SETTLE, {"id": view.id, "by": staff.id, "now": clock.now(), "entry_id": entry.id}
    )
    await _audit(
        session,
        staff,
        "approval",
        "dealing.order.settled",
        view.id,
        clock,
        reason="Confirmed against the custodian's note as the second person",
    )
    if not view.kind.buys:
        # Withdrawals waiting for a sale are paid from what it raised, by the worker, at once.
        await enqueue(
            session,
            JobRequest(
                kind=FUND_FROM_SALE_KIND,
                idempotency_key=f"sale:{view.id}",
                payload={"sale": view.id},
            ),
            now=clock.now(),
        )
    return await get_order(session, view.id)


# Internals ---------------------------------------------------------------------------------------


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff":
        raise ApplicationError("forbidden")


def _id(actor: Actor) -> str:
    assert actor.id is not None
    return actor.id


async def _face_held(
    session: AsyncSession, product: Product, clock: Clock, *, matures_on: date | None = None
) -> Money:
    """Face value held today in the product, or in one maturity of it, that a sale could take."""
    return await holdings.face_held(
        session,
        product.code,
        product.currency,
        on=business_date(clock.now()),
        matures_on=matures_on,
    )


async def _product(session: AsyncSession, code: str, clock: Clock) -> Product:
    return (await investments.get_product(session, code, clock)).product


async def _locked(session: AsyncSession, staff: Actor, order_id: str) -> OrderView:
    """The order, locked, so two people acting at once are handled one after the other."""
    _staff_only(staff)
    row = (await session.execute(_LOCKED, {"id": order_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return _view(row)


async def _decide(
    session: AsyncSession,
    staff: Actor,
    view: OrderView,
    status: OrderStatus,
    note: str | None,
    clock: Clock,
) -> None:
    await session.execute(
        _DECIDE,
        {"id": view.id, "status": status.value, "by": staff.id, "now": clock.now(), "note": note},
    )


def _view(row: Row[Any]) -> OrderView:
    currency = Currency(row.currency)
    result = (
        None
        if row.result_face_minor is None
        else Fill(
            face=Money(row.result_face_minor, currency),
            amount=Money(row.result_amount_minor, currency),
            settled_on=row.result_settled_on,
            matures_on=row.result_matures_on,
            custodian_reference=row.custodian_reference,
        )
    )
    return OrderView(
        id=row.id,
        kind=OrderKind(row.kind),
        product_code=row.product_code,
        face=Money(row.face_minor, currency),
        status=OrderStatus(row.status),
        prepared_by=row.prepared_by,
        prepared_at=row.prepared_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        result=result,
        result_recorded_by=row.result_recorded_by,
        result_recorded_at=row.result_recorded_at,
        result_rejection_note=row.result_rejection_note,
        settled_by=row.settled_by,
        settled_at=row.settled_at,
        settlement_entry_id=row.settlement_entry_id,
    )


# A problem with what was sent names the field. The rest say who may not do this, or when.
_FIELDS = {
    Refusal.NOT_A_WHOLE_LOT: "face",
    Refusal.MORE_THAN_HELD: "face",
    Refusal.CURRENCY_MISMATCH: "face",
    Refusal.OVER_THE_ORDER: "face",
    Refusal.ABOVE_FACE: "amount",
    Refusal.SETTLES_IN_THE_FUTURE: "settled_on",
    Refusal.LONGER_THAN_TERM: "matures_on",
    Refusal.NOTE_REQUIRED: "note",
}


def _allow(refusal: Refusal | None) -> None:
    if refusal is not None:
        raise _refused(refusal)


def _refused(refusal: Refusal) -> ApplicationError:
    if refusal in _FIELDS:
        return _invalid(_FIELDS[refusal], refusal.value)
    if refusal is Refusal.WRONG_STATUS:
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
    order_id: str,
    clock: Clock,
    *,
    reason: str | None = None,
    order: Order | None = None,
    fill: Fill | None = None,
) -> None:
    # "product", not "product_code": the audit trail redacts any key with the word "code".
    details: dict[str, object] = (
        {} if order is None else {"kind": order.kind.value, "product": order.product_code}
    )
    if fill is not None:
        # The result as recorded, so a result later rejected can still be read.
        details |= {
            "face_minor": fill.face.minor,
            "amount_minor": fill.amount.minor,
            "currency": fill.face.currency.value,
            "settled_on": fill.settled_on.isoformat(),
            "matures_on": fill.matures_on.isoformat(),
            "custodian_reference": fill.custodian_reference,
        }
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=action,
            resource_type=ORDER_RESOURCE,
            resource_id=order_id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
            details=details,
        ),
        clock,
    )
