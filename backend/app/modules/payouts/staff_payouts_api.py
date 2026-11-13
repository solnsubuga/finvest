"""Staff routes for payouts that need a person to look at them.

`GET /staff/payouts/held-up` lists queued mobile money payouts the provider has taken nothing of
since their first try `HELD_UP_AFTER` or more ago. The sweep keeps sending them and never gives
up: a client's money is in flight. This is where a person sees that it is not moving, so they can
find out why from the provider. Seeing it needs `money_movement.view`.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request

from app.infrastructure.database import Database
from app.modules.identity.api import require_permission
from app.modules.identity.service import StaffSession
from app.modules.payouts import payouts
from app.modules.payouts.api import MoneyView
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
}

staff_router = APIRouter(prefix="/staff", tags=["staff payouts"], responses=_REFUSALS)
routers = [staff_router]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]
Viewer = Annotated[StaffSession, Depends(require_permission("money_movement.view"))]


class HeldUpPayoutOut(Contract):
    """A queued payout first tried two hours or more ago, and taken by nothing since."""

    id: PushId
    client_id: PushId
    kind: Literal["mobile_money"]
    amount: MoneyView
    created_at: datetime
    first_tried_at: datetime
    last_tried_at: datetime
    # Sends tried so far. Each one ended with the provider saying it took nothing.
    tries: int


@staff_router.get("/payouts/held-up")
async def held_up(staff: Viewer, database: Db, clock: Time) -> list[HeldUpPayoutOut]:
    """Payouts held up at the provider, longest first, at most 100."""
    async with database.actor_transaction(staff.actor) as session:
        found = await payouts.held_up(session, clock.now(), limit=100)
    return [
        HeldUpPayoutOut(
            id=item.payout.id,
            client_id=item.payout.client_id,
            kind="mobile_money",
            amount=MoneyView(
                minor=item.payout.amount.minor, currency=item.payout.amount.currency.value
            ),
            created_at=item.payout.created_at,
            first_tried_at=item.first_tried_at,
            last_tried_at=item.last_tried_at,
            tries=item.tries,
        )
        for item in found
    ]
