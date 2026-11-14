"""GET /staff/review-queue: everything waiting for a person, for the console's Review queue.

The work itself is still done on each owning module's screen and routes, which check their own
permissions.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request

from app.infrastructure.database import Database
from app.modules.identity.api import require_permission
from app.modules.identity.service import StaffSession
from app.modules.review_queue import service
from app.modules.review_queue.domain.queue import GROUPS, Group, Kind
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope

Reader = Annotated[StaffSession, Depends(require_permission("review_queue.view"))]


class QueueAmount(Contract):
    minor: int
    currency: Literal["UGX", "USD"]


class QueueItemOut(Contract):
    kind: Kind
    group: Group
    id: PushId
    # The client it is about, or for a company application its applicant. The console names
    # them through the client directory.
    client_id: PushId | None
    amount: QueueAmount | None
    reason: str
    waiting_since: datetime
    # Someone already looked and proposed what to do; it waits for a different person.
    proposed_by: PushId | None
    yours: bool


class ReviewQueueOut(Contract):
    items: list[QueueItemOut]
    # Every kind this reader may see, even with nothing waiting, so a filter can show zero.
    kinds: list[Kind]
    waiting: int
    cleared_automatically_today: int
    oldest_waiting_since: datetime | None


router = APIRouter(
    prefix="/staff",
    tags=["staff review queue"],
    responses={401: {"model": ErrorEnvelope}, 403: {"model": ErrorEnvelope}},
)
routers = [router]


@router.get("/review-queue")
async def review_queue(request: Request, staff: Reader) -> ReviewQueueOut:
    """Everything waiting for a person that this member of staff may see, longest first."""
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        found = await service.waiting_for(session, staff, request.app.state.clock.now())
    return ReviewQueueOut(
        items=[
            QueueItemOut(
                kind=item.kind,
                group=GROUPS[item.kind],
                id=item.id,
                client_id=item.client_id,
                amount=None
                if item.amount is None
                else QueueAmount(minor=item.amount.minor, currency=item.amount.currency.value),
                reason=item.reason,
                waiting_since=item.waiting_since,
                proposed_by=item.proposed_by,
                yours=item.proposed_by == staff.staff_id,
            )
            for item in found.items
        ],
        kinds=found.kinds,
        waiting=len(found.items),
        cleared_automatically_today=found.cleared_automatically_today,
        oldest_waiting_since=found.oldest,
    )
