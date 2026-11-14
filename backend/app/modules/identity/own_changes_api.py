"""Routes for a signed-in client changing their own password, mobile number and email.

Every route needs a passkey or authenticator app used on this session in the last ten minutes.
Without one the answer is 403 `confirm_it_is_you`: the app confirms with
`POST /client/session/confirmation/passkey` or `/authenticator`, then sends the change again.
A password or number change also needs a live selfie on this session in the last ten minutes,
when the client's approved identity check has a selfie to compare it with. Without one the
answer is 403 `confirm_with_selfie`: the app takes it with
`POST /client/session/confirmation/selfie` (see app/modules/kyc/face_confirmations_api.py).
"""

from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import Field

from app.modules.identity.api import _REFUSALS, CurrentConfirmedClient
from app.modules.identity.own_changes import OwnChangeService
from app.modules.kyc.face_confirmations_api import CurrentFaceConfirmedClient
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope

router = APIRouter(
    prefix="/client",
    tags=["client sign-in"],
    responses={**_REFUSALS, 409: {"model": ErrorEnvelope}, 410: {"model": ErrorEnvelope}},
)


def _service(request: Request) -> OwnChangeService:
    service: OwnChangeService = request.app.state.own_changes
    return service


class PasswordChange(Contract):
    current_password: Annotated[str, Field(min_length=1, max_length=1024)]
    # Checked by the service, so a short or common one gets a field problem with its reason.
    new_password: Annotated[str, Field(min_length=1, max_length=1024)]


class PhoneChangeStart(Contract):
    phone: Annotated[str, Field(min_length=1, max_length=32)]


class PhoneChangeStarted(Contract):
    """Send the code texted to the new number with this ID, within ten minutes."""

    challenge_id: PushId


class PhoneChangeConfirm(Contract):
    code: Annotated[str, Field(min_length=1, max_length=16)]


class PhoneChanged(Contract):
    phone: str


class EmailChange(Contract):
    email: Annotated[str, Field(min_length=1, max_length=254)]


class EmailChanged(Contract):
    """The new address, not proved until the link sent to it is opened."""

    email: str


@router.put("/password", status_code=204)
async def change_password(
    body: PasswordChange, request: Request, client: CurrentFaceConfirmedClient
) -> None:
    """Change the password. A wrong current one is 401 `invalid_credentials`. Every other
    session ends."""
    await _service(request).change_password(client, body.current_password, body.new_password)


@router.post("/phone-change", status_code=201)
async def start_phone_change(
    body: PhoneChangeStart, request: Request, client: CurrentFaceConfirmedClient
) -> PhoneChangeStarted:
    """Text a code to the new number."""
    return PhoneChangeStarted(
        challenge_id=await _service(request).start_phone_change(client, body.phone)
    )


@router.post("/phone-change/{challenge_id}/confirmation")
async def confirm_phone_change(
    challenge_id: PushId,
    body: PhoneChangeConfirm,
    request: Request,
    client: CurrentFaceConfirmedClient,
) -> PhoneChanged:
    """Move the account to the new number with its code. Every other session ends, and for a day
    withdrawals go only to accounts the client already uses. 409 `phone_taken` when the number
    has an account."""
    phone = await _service(request).confirm_phone_change(client, challenge_id, body.code)
    return PhoneChanged(phone=phone)


@router.put("/email")
async def change_email(
    body: EmailChange, request: Request, client: CurrentConfirmedClient
) -> EmailChanged:
    """Set a new email address and send it a link to prove it."""
    return EmailChanged(email=await _service(request).change_email(client, body.email))
