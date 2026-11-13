"""The allocation report over HTTP, for staff with `dealing_desk.view`. Read only.

`GET /staff/dealing/allocations` is the report for one currency: for each product, the bills
held, the cost standing behind client investments, and what is left over on either side.
`GET /staff/dealing/allocations/investments/{id}` lists the bills behind one investment. Neither
names a client: investments appear by ID and as counts.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from app.infrastructure.database import Database
from app.modules.dealing import allocation_report, allocations
from app.modules.dealing.domain.allocation_report import LotLine, ProductReport
from app.modules.identity.api import require_permission
from app.modules.identity.service import StaffSession
from app.shared.clock import Clock
from app.shared.contracts import Contract
from app.shared.errors import ErrorEnvelope
from app.shared.money import Currency, Money

_REFUSALS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorEnvelope} for code in (401, 403, 422)
}


# Contracts --------------------------------------------------------------------------------------


class AmountOut(Contract):
    minor: int
    currency: Currency


class LotOut(Contract):
    lot_id: str
    settled_on: date
    matures_on: date
    custodian_reference: str
    # What is still held of the purchase.
    face: AmountOut
    cost: AmountOut
    # The part of the cost standing behind investments, and the part with none behind it yet.
    allocated: AmountOut
    unallocated: AmountOut
    investments: int


class ProductAllocationOut(Contract):
    product_code: str
    # Oldest purchase first.
    lots: list[LotOut]
    cost_held: AmountOut
    allocated: AmountOut
    unallocated: AmountOut
    # Running investments, what they are owed, and the part with no bill behind it yet.
    investments: int
    owed: AmountOut
    covered: AmountOut
    investments_waiting: int
    waiting: AmountOut


class AllocationReportOut(Contract):
    currency: Currency
    # The business day the report is for. Lots maturing on or before it are left out.
    on: date
    products: list[ProductAllocationOut]


class BillsOut(Contract):
    lot_id: str
    product_code: str
    matures_on: date
    custodian_reference: str
    cost: AmountOut


class InvestmentBillsOut(Contract):
    investment_id: str
    bills: list[BillsOut]
    total: AmountOut | None


def _amount(money: Money) -> AmountOut:
    return AmountOut(minor=money.minor, currency=money.currency)


def _lot(line: LotLine) -> LotOut:
    return LotOut(
        lot_id=line.lot_id,
        settled_on=line.settled_on,
        matures_on=line.matures_on,
        custodian_reference=line.custodian_reference,
        face=_amount(line.face),
        cost=_amount(line.cost),
        allocated=_amount(line.allocated),
        unallocated=_amount(line.unallocated),
        investments=line.investments,
    )


def _product(product: ProductReport) -> ProductAllocationOut:
    return ProductAllocationOut(
        product_code=product.product_code,
        lots=[_lot(line) for line in product.lots],
        cost_held=_amount(product.cost_held),
        allocated=_amount(product.allocated),
        unallocated=_amount(product.unallocated),
        investments=product.investments,
        owed=_amount(product.owed),
        covered=_amount(product.covered),
        investments_waiting=product.investments_waiting,
        waiting=_amount(product.waiting),
    )


# Wiring -----------------------------------------------------------------------------------------


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


AppClock = Annotated[Clock, Depends(_clock)]
DeskStaff = Annotated[StaffSession, Depends(require_permission("dealing_desk.view"))]
InvestmentId = Annotated[str, Path(min_length=1, max_length=64)]


# Routes -----------------------------------------------------------------------------------------

router = APIRouter(prefix="/staff/dealing/allocations", tags=["dealing desk"], responses=_REFUSALS)


@router.get("")
async def get_allocation_report(
    request: Request,
    staff: DeskStaff,
    clock: AppClock,
    currency: Annotated[Currency, Query()] = Currency.UGX,
) -> AllocationReportOut:
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        found = await allocation_report.report(session, staff.actor, currency, clock)
    return AllocationReportOut(
        currency=found.currency, on=found.on, products=[_product(p) for p in found.products]
    )


@router.get("/investments/{investment_id}")
async def get_bills_behind(
    request: Request, staff: DeskStaff, investment_id: InvestmentId
) -> InvestmentBillsOut:
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        bills = await allocations.bills_behind(session, investment_id)
    total: Money | None = None
    for bill in bills:
        total = bill.cost if total is None else total + bill.cost
    return InvestmentBillsOut(
        investment_id=investment_id,
        bills=[
            BillsOut(
                lot_id=bill.lot_id,
                product_code=bill.product_code,
                matures_on=bill.matures_on,
                custodian_reference=bill.custodian_reference,
                cost=_amount(bill.cost),
            )
            for bill in bills
        ],
        total=None if total is None else _amount(total),
    )
