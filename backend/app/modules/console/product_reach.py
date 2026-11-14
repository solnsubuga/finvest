"""GET /staff/products/{code}/reach: who a change to a product reaches, before anyone approves it.

A rate change, new terms or a pause apply to new investments only. This route tells staff how
many running investments keep their terms, and how many goals and automatic investing plans put
new money into the product, so they will feel the change. It only reads, through each owning
module's own read functions, and holds no personal data: counts only.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.infrastructure.database import Database
from app.modules.autoinvest import service as autoinvest
from app.modules.goals import service as goals
from app.modules.identity.api import require_permission
from app.modules.identity.service import StaffSession
from app.modules.investments import service as investments
from app.shared.contracts import Contract
from app.shared.errors import ErrorEnvelope

router = APIRouter(
    prefix="/staff/products",
    tags=["staff investing"],
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
    },
)

ProductsViewer = Annotated[StaffSession, Depends(require_permission("products.view"))]


class ProductReachOut(Contract):
    product_code: str
    # Investments that keep the terms they started with, and the clients who hold them.
    running_investments: int
    awaiting_payment: int
    clients: int
    # New money that will take the change: open goals and plans that are not stopped.
    open_goals: int
    active_plans: int
    paused_plans: int


@router.get("/{code}/reach")
async def product_reach(request: Request, staff: ProductsViewer, code: str) -> ProductReachOut:
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        held = await investments.product_reach(session, staff.actor, code)
        saving = await goals.open_goals_in_product(session, staff.actor, code)
        plans = await autoinvest.plan_counts(session, staff.actor, product_code=code)
    return ProductReachOut(
        product_code=code,
        running_investments=held.running,
        awaiting_payment=held.awaiting_payment,
        clients=held.clients,
        open_goals=saving,
        active_plans=plans.active,
        paused_plans=plans.paused,
    )
