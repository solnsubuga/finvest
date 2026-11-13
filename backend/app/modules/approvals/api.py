"""GET /staff/approvals: what is waiting for a second person, for the console's inbox.

Deciding still happens on each owning module's route, which checks its own permission and
refuses the person who proposed the item.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.infrastructure.database import Database
from app.modules.approvals import service
from app.modules.approvals.domain.inbox import Kind
from app.modules.identity.api import require_permission
from app.modules.identity.service import StaffSession, staff_names
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope

# Every role opens the overview, so every member of staff has an inbox. What is in it depends
# on the rest of their permissions.
Reader = Annotated[StaffSession, Depends(require_permission("overview.view"))]


class ApprovalItemOut(Contract):
    kind: Kind
    id: str
    subject: str
    proposed_by: PushId
    # None only if the account was removed, which never happens to staff.
    proposed_by_name: str | None
    proposed_at: datetime
    yours: bool
    can_decide: bool


class ApprovalsOut(Contract):
    items: list[ApprovalItemOut]


router = APIRouter(
    prefix="/staff",
    tags=["staff approvals"],
    responses={401: {"model": ErrorEnvelope}, 403: {"model": ErrorEnvelope}},
)
routers = [router]


@router.get("/approvals")
async def approvals(request: Request, staff: Reader) -> ApprovalsOut:
    """Everything waiting for a second person that this member of staff may see, oldest first."""
    database: Database = request.app.state.database
    async with database.actor_transaction(staff.actor) as session:
        items = await service.waiting_for(session, staff, request.app.state.clock.now())
        names = await staff_names(session, {item.proposed_by for item in items})
    return ApprovalsOut(
        items=[
            ApprovalItemOut(
                kind=item.kind,
                id=item.id,
                subject=item.subject,
                proposed_by=item.proposed_by,
                proposed_by_name=names.get(item.proposed_by),
                proposed_at=item.proposed_at,
                yours=item.yours,
                can_decide=item.can_decide,
            )
            for item in items
        ]
    )
