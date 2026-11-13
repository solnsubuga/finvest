"""The dealing desk screen in one read: the position of each product, the buffer, the early
withdrawals waiting, and the open orders with what the reader may do to each. For staff.

Every figure comes from the module's own reads. The console shows them and works out nothing.
"""

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dealing import holdings, orders, withdrawals
from app.modules.dealing.domain.orders import LOT_SIZES, Action, OrderKind, allowed_actions
from app.modules.dealing.domain.position import Cover, ProductPosition
from app.modules.dealing.domain.withdrawals import Source
from app.modules.investments import service as investments
from app.modules.investments.domain.products import ProductKind
from app.shared.actors import Actor
from app.shared.business_day import business_date
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Currency, Money


@dataclass(frozen=True, slots=True)
class PositionView:
    product_code: str
    product_name: str
    discount_only: bool
    lot_size: Money
    clients_owed: Money
    bills_held: Money
    to_buy: Money
    on_order: Money
    # Face value held that is not already in an open sale.
    face_held: Money
    cover: Cover


@dataclass(frozen=True, slots=True)
class OrderLine:
    order: orders.OrderView
    product_name: str
    allowed: tuple[Action, ...]


@dataclass(frozen=True, slots=True)
class DeskView:
    currency: Currency
    money_to_place: Money
    bills_held: Money
    buffer: withdrawals.BufferView
    waiting_withdrawals: int
    waiting_amount: Money
    positions: tuple[PositionView, ...]
    orders: tuple[OrderLine, ...]


async def desk(session: AsyncSession, staff: Actor, currency: Currency, clock: Clock) -> DeskView:
    if staff.kind != "staff" or staff.id is None:
        raise ApplicationError("forbidden")
    on = business_date(clock.now())
    zero = Money.zero(currency)
    products = [
        view.product
        for view in await investments.list_products(session, clock)
        if view.product.currency is currency
    ]
    names = {product.code: product.name for product in products}
    owed = {
        row.product_code: row.principal
        for row in await investments.owed_by_product(session, on)
        if row.currency is currency
    }
    cost: defaultdict[str, int] = defaultdict(int)
    face: defaultdict[str, int] = defaultdict(int)
    for holding in await holdings.holdings_on(session, on):
        if holding.currency is currency:
            cost[holding.product_code] += holding.cost_minor
            face[holding.product_code] += holding.face_minor
    on_order = await orders.on_order(session)
    open_orders = await orders.open_orders(session)
    selling: defaultdict[str, int] = defaultdict(int)
    for order in open_orders:
        if order.kind is OrderKind.SECONDARY_SALE and order.face.currency is currency:
            selling[order.product_code] += order.face.minor
    positions = []
    for product in products:
        position = ProductPosition(
            product_code=product.code,
            clients_owed=owed.get(product.code, zero),
            bills_held=Money(cost[product.code], currency),
            on_order=on_order.get(product.code, zero),
        )
        positions.append(
            PositionView(
                product_code=product.code,
                product_name=product.name,
                discount_only=product.kind is ProductKind.TREASURY_BILL,
                lot_size=LOT_SIZES[currency],
                clients_owed=position.clients_owed,
                bills_held=position.bills_held,
                to_buy=position.to_buy,
                on_order=position.on_order,
                face_held=Money(max(face[product.code] - selling[product.code], 0), currency),
                cover=position.cover,
            )
        )
    buffer = await withdrawals.buffer_now(session, currency, clock)
    waiting = [
        *await withdrawals.waiting(session, currency, Source.BUFFER),
        *await withdrawals.waiting(session, currency, Source.SALE),
    ]
    staff_id = staff.id
    lines = tuple(
        OrderLine(
            order=order,
            product_name=names.get(order.product_code, order.product_code),
            allowed=allowed_actions(order.order, staff_id),
        )
        for order in open_orders
        if order.face.currency is currency
    )
    return DeskView(
        currency=currency,
        money_to_place=buffer.to_place,
        bills_held=Money(sum(cost.values()), currency),
        buffer=buffer,
        waiting_withdrawals=len(waiting),
        waiting_amount=sum((funding.amount for funding in waiting), zero),
        positions=tuple(positions),
        orders=lines,
    )
