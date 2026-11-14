"""Routes for the phone app's PIN."""

from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import Field

from app.modules.identity.api import _REFUSALS, CurrentClient, CurrentClientEvenLocked
from app.modules.identity.pins import PinService
from app.shared.contracts import Contract
from app.shared.errors import ErrorEnvelope

router = APIRouter(
    prefix="/client/pin",
    tags=["client sign-in"],
    responses={**_REFUSALS, 409: {"model": ErrorEnvelope}, 423: {"model": ErrorEnvelope}},
)

# Checked as five digits by the service, so a wrong shape gets the same field problem.
Pin = Annotated[str, Field(min_length=1, max_length=16)]


def _service(request: Request) -> PinService:
    service: PinService = request.app.state.pins
    return service


class PinBody(Contract):
    pin: Pin


class PinChecked(Contract):
    """`unlocked` when the PIN was right. Otherwise the tries left before this device signs out."""

    unlocked: bool
    tries_left: int


@router.put("", status_code=204)
async def set_pin(body: PinBody, request: Request, client: CurrentClient) -> None:
    """Set or replace the PIN for this phone. Needs a log in from the last ten minutes."""
    await _service(request).set_pin(client, body.pin)


@router.post("/unlock")
async def unlock(body: PinBody, request: Request, client: CurrentClientEvenLocked) -> PinChecked:
    """Open a locked app. The fifth wrong PIN in a row signs this device out (401)."""
    checked = await _service(request).unlock(client, body.pin)
    return PinChecked(unlocked=checked.unlocked, tries_left=checked.tries_left)


routers: list[APIRouter] = [router]
