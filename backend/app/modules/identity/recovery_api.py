"""Routes for someone locked out of their account to ask for help, and for staff to follow up."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.modules.identity.api import _REFUSALS, caller_address, check_origin, require_permission
from app.modules.identity.recovery import Outcome, Reason, RecoveryService
from app.modules.identity.service import StaffSession
from app.shared.contracts import Contract, PushId

client_router = APIRouter(prefix="/client", tags=["client sign-in"], responses=_REFUSALS)
staff_router = APIRouter(prefix="/staff", tags=["client numbers"], responses=_REFUSALS)
# The people who change a client's number are the ones who follow these up.
Follower = Annotated[StaffSession, Depends(require_permission("client_phones.change"))]
Phone = Annotated[str, Field(min_length=1, max_length=32)]


def _service(request: Request) -> RecoveryService:
    service: RecoveryService = request.app.state.recovery
    return service


Recovery = Annotated[RecoveryService, Depends(_service)]


class RecoveryAsk(Contract):
    """The number on the account, a number to call now, and what happened. No free text."""

    phone: Phone
    contact_phone: Phone
    reason: Reason


class RecoveryView(Contract):
    id: PushId
    client_id: PushId
    account_phone: str
    contact_phone: str
    reason: Reason
    created_at: datetime


class RecoveryList(Contract):
    items: list[RecoveryView]


class RecoveryClose(Contract):
    outcome: Outcome


@client_router.post("/recovery-requests", status_code=202)
async def ask_for_recovery(body: RecoveryAsk, request: Request, recovery: Recovery) -> None:
    """Ask Finvest to call back. The same answer whether or not the number has an account."""
    check_origin(request, "client", required=False)
    await recovery.request(
        phone=body.phone,
        contact_phone=body.contact_phone,
        reason=body.reason,
        address=caller_address(request),
    )


@staff_router.get("/recovery-requests")
async def open_recovery_requests(staff: Follower, recovery: Recovery) -> RecoveryList:
    """Requests for a real account that nobody has closed yet, oldest first."""
    items = await recovery.open_requests(staff.actor)
    return RecoveryList(
        items=[
            RecoveryView(
                id=item.id,
                client_id=item.client_id,
                account_phone=item.account_phone,
                contact_phone=item.contact_phone,
                reason=item.reason,
                created_at=item.created_at,
            )
            for item in items
        ]
    )


@staff_router.post("/recovery-requests/{request_id}/close", status_code=204)
async def close_recovery_request(
    request_id: PushId, body: RecoveryClose, staff: Follower, recovery: Recovery
) -> None:
    """Close a request once the person was called back. The number changes separately."""
    await recovery.close(staff.actor, request_id, body.outcome)


routers: list[APIRouter] = [client_router, staff_router]
