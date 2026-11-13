"""Staff routes to change a client's mobile number, with a second person approving."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.modules.identity.api import _REFUSALS, require_permission
from app.modules.identity.phone_changes import PhoneChangeService
from app.modules.identity.service import StaffSession
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope

router = APIRouter(
    prefix="/staff",
    tags=["client numbers"],
    responses={**_REFUSALS, 409: {"model": ErrorEnvelope}, 410: {"model": ErrorEnvelope}},
)
Changer = Annotated[StaffSession, Depends(require_permission("client_phones.change"))]
Text = Annotated[str, Field(min_length=1, max_length=500)]


def _service(request: Request) -> PhoneChangeService:
    service: PhoneChangeService = request.app.state.phone_changes
    return service


Changes = Annotated[PhoneChangeService, Depends(_service)]


class ProposePhone(Contract):
    phone: Annotated[str, Field(min_length=1, max_length=32)]
    reason: Text


class Proposed(Contract):
    id: PushId


class PhoneChangeView(Contract):
    id: PushId
    client_id: PushId
    new_phone: str
    reason: str
    proposed_by: PushId
    proposed_at: datetime
    expires_at: datetime


class PhoneChangeList(Contract):
    items: list[PhoneChangeView]


class Rejection(Contract):
    note: Text


@router.post("/clients/{client_id}/phone-changes", status_code=201)
async def propose_phone_change(
    client_id: PushId, body: ProposePhone, staff: Changer, changes: Changes
) -> Proposed:
    """Propose a new number for a client. A different member of staff must approve it."""
    change_id = await changes.propose(staff.actor, client_id, body.phone, body.reason)
    return Proposed(id=change_id)


@router.get("/phone-changes")
async def list_phone_changes(staff: Changer, changes: Changes) -> PhoneChangeList:
    """Number changes waiting for a second person, newest first."""
    return PhoneChangeList(
        items=[
            PhoneChangeView(
                id=change.id,
                client_id=change.client_id,
                new_phone=change.new_phone,
                reason=change.reason,
                proposed_by=change.proposed_by,
                proposed_at=change.proposed_at,
                expires_at=change.expires_at,
            )
            for change in await changes.pending()
        ]
    )


@router.post("/phone-changes/{change_id}/approval", status_code=204)
async def approve_phone_change(change_id: PushId, staff: Changer, changes: Changes) -> None:
    """Approve someone else's proposal. The number changes and the client is signed out."""
    await changes.approve(staff.actor, change_id)


@router.post("/phone-changes/{change_id}/rejection", status_code=204)
async def reject_phone_change(
    change_id: PushId, body: Rejection, staff: Changer, changes: Changes
) -> None:
    """Turn down someone else's proposal, saying why."""
    await changes.reject(staff.actor, change_id, body.note)


routers: list[APIRouter] = [router]
