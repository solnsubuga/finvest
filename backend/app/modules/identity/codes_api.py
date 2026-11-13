"""Routes for client signup, the code fallback at sign-in, and password reset."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field

from app.infrastructure.settings import Settings
from app.modules.identity.api import _REFUSALS, Identity, Password, caller_address, check_origin
from app.modules.identity.codes import CodeService
from app.modules.identity.passkeys_api import (
    ClientSignedIn,
    Code,
    Token,
    client_signed_in,
    client_transport,
)
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope

router = APIRouter(
    prefix="/client",
    tags=["client sign-in"],
    responses={**_REFUSALS, 409: {"model": ErrorEnvelope}, 410: {"model": ErrorEnvelope}},
)


def _service(request: Request) -> CodeService:
    service: CodeService = request.app.state.codes
    return service


Codes = Annotated[CodeService, Depends(_service)]
Phone = Annotated[str, Field(min_length=1, max_length=32)]


class SignupStart(Contract):
    phone: Phone
    email: Annotated[str, Field(max_length=254)] | None = None


class SignupStarted(Contract):
    """A code was texted to `phone`. The answer is the same if the number has an account."""

    challenge_id: PushId
    phone: str


class SignupConfirm(Contract):
    challenge_id: PushId
    code: Code


class SignupTicketView(Contract):
    ticket: str
    phone: str


class SignupComplete(Contract):
    ticket: Token
    password: Password


class SendLoginCode(Contract):
    login_id: PushId
    channel: Literal["sms", "email"] = "sms"


class LoginCode(Contract):
    login_id: PushId
    code: Code


class ResetStart(Contract):
    phone: Phone


class ResetStarted(Contract):
    challenge_id: PushId


class ResetComplete(Contract):
    challenge_id: PushId
    code: Code
    password: Password


def require_signup_open(request: Request) -> None:
    """Refuse sign-up before any other work while it is closed (`client_signup_open`)."""
    settings: Settings = request.app.state.settings
    if not settings.signup_open():
        raise ApplicationError("unavailable")


@router.post("/signup/start")
async def signup_start(body: SignupStart, request: Request, codes: Codes) -> SignupStarted:
    """Sign up, step 1: text a code to prove the number."""
    require_signup_open(request)
    check_origin(request, "client", required=False)
    challenge_id, phone = await codes.signup_start(
        body.phone, body.email, address=caller_address(request)
    )
    return SignupStarted(challenge_id=challenge_id, phone=phone)


@router.post("/signup/confirm")
async def signup_confirm(body: SignupConfirm, request: Request, codes: Codes) -> SignupTicketView:
    """Sign up, step 2: the texted code. `phone_taken` is said only after a right code."""
    require_signup_open(request)
    check_origin(request, "client", required=False)
    ticket = await codes.signup_confirm(body.challenge_id, body.code)
    return SignupTicketView(ticket=ticket.ticket, phone=ticket.phone)


@router.post("/signup/complete")
async def signup_complete(
    body: SignupComplete, request: Request, response: Response, codes: Codes, identity: Identity
) -> ClientSignedIn:
    """Sign up, step 3: set the password. Creates the account and opens its first session."""
    require_signup_open(request)
    issued = await codes.signup_complete(body.ticket, body.password, client_transport(request))
    return await client_signed_in(request, response, identity, issued)


@router.post("/login/code/send", status_code=204)
async def login_code_send(body: SendLoginCode, request: Request, codes: Codes) -> None:
    """Send the backup code for a sign-in. Email is used only if the client has an address."""
    check_origin(request, "client", required=False)
    await codes.send_login_code(body.login_id, body.channel)


@router.post("/login/code/verify")
async def login_code_verify(
    body: LoginCode, request: Request, response: Response, codes: Codes, identity: Identity
) -> ClientSignedIn:
    """Finish sign-in with the code. Withdrawals go to known accounts only for 24 hours."""
    issued = await codes.verify_login_code(body.login_id, body.code, client_transport(request))
    return await client_signed_in(request, response, identity, issued)


@router.post("/password/reset/start")
async def password_reset_start(body: ResetStart, request: Request, codes: Codes) -> ResetStarted:
    """Text a reset code if the number has an account. The answer never says whether it does."""
    check_origin(request, "client", required=False)
    challenge_id = await codes.reset_start(body.phone, address=caller_address(request))
    return ResetStarted(challenge_id=challenge_id)


@router.post("/password/reset/complete", status_code=204)
async def password_reset_complete(body: ResetComplete, request: Request, codes: Codes) -> None:
    """Set a new password. Every session ends, and the account is restricted for 24 hours."""
    check_origin(request, "client", required=False)
    await codes.reset_complete(body.challenge_id, body.code, body.password)


routers: list[APIRouter] = [router]
