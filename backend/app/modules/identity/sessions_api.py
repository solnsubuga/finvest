"""Routes for a person's own signed-in devices: list them and sign out the unwanted ones."""

from datetime import datetime

from fastapi import APIRouter

from app.modules.identity.api import _REFUSALS, CurrentClient, CurrentStaff, Identity
from app.modules.identity.domain.sessions import SecondStep, Transport
from app.modules.identity.service import DeviceSession
from app.shared.actors import Actor
from app.shared.contracts import Contract, PushId

client_router = APIRouter(prefix="/client/sessions", tags=["client sign-in"], responses=_REFUSALS)
staff_router = APIRouter(prefix="/staff/sessions", tags=["staff sign-in"], responses=_REFUSALS)


class DeviceSessionView(Contract):
    id: PushId
    # A fixed label such as "Chrome on Android".
    device: str
    transport: Transport
    second_step: SecondStep
    signed_in_at: datetime
    last_seen_at: datetime
    # The session making this request.
    current: bool


class DeviceSessionList(Contract):
    items: list[DeviceSessionView]


class SignedOut(Contract):
    ended: int


def _view(sessions: list[DeviceSession], current: str) -> DeviceSessionList:
    return DeviceSessionList(
        items=[
            DeviceSessionView(
                id=s.id,
                device=s.device,
                transport=s.transport,
                second_step=s.second_step,
                signed_in_at=s.signed_in_at,
                last_seen_at=s.last_seen_at,
                current=s.id == current,
            )
            for s in sessions
        ]
    )


async def _list(identity: Identity, actor: Actor, current: str) -> DeviceSessionList:
    return _view(await identity.live_sessions(actor), current)


@client_router.get("")
async def client_sessions(client: CurrentClient, identity: Identity) -> DeviceSessionList:
    """The devices signed in to this account, most recently used first."""
    return await _list(identity, client.actor, client.session_id)


@client_router.delete("/{session_id}", status_code=204)
async def client_sign_out_device(
    session_id: PushId, client: CurrentClient, identity: Identity
) -> None:
    """Sign out one device."""
    await identity.sign_out_device(client.actor, session_id)


@client_router.post("/sign-out-others")
async def client_sign_out_others(client: CurrentClient, identity: Identity) -> SignedOut:
    """Sign out every device except this one."""
    return SignedOut(ended=await identity.sign_out_other_devices(client.actor, client.session_id))


@staff_router.get("")
async def staff_sessions(staff: CurrentStaff, identity: Identity) -> DeviceSessionList:
    """The devices signed in as this member of staff, most recently used first."""
    return await _list(identity, staff.actor, staff.session_id)


@staff_router.delete("/{session_id}", status_code=204)
async def staff_sign_out_device(
    session_id: PushId, staff: CurrentStaff, identity: Identity
) -> None:
    """Sign out one device."""
    await identity.sign_out_device(staff.actor, session_id)


@staff_router.post("/sign-out-others")
async def staff_sign_out_others(staff: CurrentStaff, identity: Identity) -> SignedOut:
    """Sign out every device except this one."""
    return SignedOut(ended=await identity.sign_out_other_devices(staff.actor, staff.session_id))


routers: list[APIRouter] = [client_router, staff_router]
