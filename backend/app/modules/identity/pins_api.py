"""Routes for the phone app's PIN."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.modules.identity.api import _REFUSALS, CurrentClient, CurrentClientEvenLocked
from app.modules.identity.domain.pins import PinAction
from app.modules.identity.pins import PinService
from app.shared.contracts import Contract
from app.shared.errors import ErrorEnvelope

router = APIRouter(
    prefix="/client/pin",
    tags=["client sign-in"],
    responses={
        **_REFUSALS,
        409: {"model": ErrorEnvelope},
        423: {"model": ErrorEnvelope},
        428: {"model": ErrorEnvelope},
    },
)

# Checked as five digits by the service, so a wrong shape gets the same field problem.
Pin = Annotated[str, Field(min_length=1, max_length=16)]


def _service(request: Request) -> PinService:
    service: PinService = request.app.state.pins
    return service


# For the routes of other modules whose action a PIN confirms.
Pins = Annotated[PinService, Depends(_service)]


class PinBody(Contract):
    pin: Pin


# A key the API made, from `secrets.token_urlsafe(32)`. Bounded so nothing large is hashed.
Key = Annotated[str, Field(min_length=1, max_length=128)]


class UnlockKeyBody(Contract):
    key: Key


class UnlockKeyTurnedOn(Contract):
    """A right PIN gives `key`, once: keep it where only a fingerprint or face can read it.
    Otherwise `tries_left` before this device signs out."""

    enabled: bool
    tries_left: int
    key: str | None


class PinChecked(Contract):
    """`unlocked` when the PIN was right. Otherwise the tries left before this device signs out."""

    unlocked: bool
    tries_left: int


class ConfirmBody(Contract):
    pin: Pin
    action: PinAction


class PinConfirmed(Contract):
    """A right PIN gives `confirmation`: send it with the action, once, within three minutes.
    Otherwise `tries_left` before this device signs out."""

    confirmed: bool
    tries_left: int
    confirmation: str | None


@router.put("", status_code=204)
async def set_pin(body: PinBody, request: Request, client: CurrentClient) -> None:
    """Set or replace the PIN for this phone. Needs a log in, or a passkey or app confirmation,
    from the last ten minutes."""
    await _service(request).set_pin(client, body.pin)


@router.post("/unlock")
async def unlock(body: PinBody, request: Request, client: CurrentClientEvenLocked) -> PinChecked:
    """Open a locked app. The fifth wrong PIN in a row signs this device out (401)."""
    checked = await _service(request).unlock(client, body.pin)
    return PinChecked(unlocked=checked.unlocked, tries_left=checked.tries_left)


@router.put("/unlock-key")
async def turn_on_unlock_key(
    body: PinBody, request: Request, client: CurrentClient
) -> UnlockKeyTurnedOn:
    """Turn on fingerprint or face unlock for this phone, with the PIN. A wrong PIN counts."""
    made = await _service(request).turn_on_unlock_key(client, body.pin)
    return UnlockKeyTurnedOn(enabled=made.enabled, tries_left=made.tries_left, key=made.key)


@router.delete("/unlock-key", status_code=204)
async def turn_off_unlock_key(request: Request, client: CurrentClient) -> None:
    """Turn off fingerprint or face unlock for this phone."""
    await _service(request).turn_off_unlock_key(client)


@router.post("/unlock-with-key")
async def unlock_with_key(
    body: UnlockKeyBody, request: Request, client: CurrentClientEvenLocked
) -> PinChecked:
    """Open a locked app with this phone's unlock key. A wrong key turns fingerprint unlock off."""
    checked = await _service(request).unlock_with_key(client, body.key)
    return PinChecked(unlocked=checked.unlocked, tries_left=checked.tries_left)


@router.post("/confirm")
async def confirm(body: ConfirmBody, request: Request, client: CurrentClient) -> PinConfirmed:
    """Confirm one action with the PIN. The fifth wrong PIN in a row signs this device out."""
    checked = await _service(request).confirm(client, body.pin, body.action)
    return PinConfirmed(
        confirmed=checked.confirmed,
        tries_left=checked.tries_left,
        confirmation=checked.confirmation,
    )


routers: list[APIRouter] = [router]
