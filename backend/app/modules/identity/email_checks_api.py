"""Routes for proving a client's email address with a one-time link."""

from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import Field

from app.modules.identity.api import (
    _REFUSALS,
    CurrentClient,
    Identity,
    Password,
    caller_address,
    check_origin,
)
from app.modules.identity.email_checks import EmailCheckService
from app.modules.identity.passkeys_api import Token
from app.shared.contracts import Contract
from app.shared.errors import ErrorEnvelope

router = APIRouter(
    prefix="/client/email",
    tags=["client email"],
    responses={**_REFUSALS, 409: {"model": ErrorEnvelope}, 410: {"model": ErrorEnvelope}},
)


def _service(request: Request) -> EmailCheckService:
    service: EmailCheckService = request.app.state.email_checks
    return service


class EmailLinkSent(Contract):
    """`sent` is false when the address was already proved, so nothing was sent."""

    sent: bool


class EmailLinkFromLogin(Contract):
    """The email and password the client just tried to log in with."""

    email: Annotated[str, Field(min_length=1, max_length=254)]
    password: Password


class EmailConfirm(Contract):
    token: Token


@router.post("/verification")
async def send_email_link(request: Request, client: CurrentClient) -> EmailLinkSent:
    """Email a one-time link to the client's address, to prove it is theirs."""
    return EmailLinkSent(sent=await _service(request).send_link(client.client_id))


@router.post("/verification/from-login")
async def send_email_link_from_login(
    body: EmailLinkFromLogin, request: Request, identity: Identity
) -> EmailLinkSent:
    """Email a new link from the log in screen, after `email_not_confirmed`.

    Signed out, so the password proves the address is theirs to confirm. It counts as a sign-in
    attempt, and anything but the right password with an unproved address is
    `invalid_credentials`.
    """
    check_origin(request, "client", required=False)
    client_id = await identity.unproved_email_owner(
        body.email, body.password, address=caller_address(request)
    )
    return EmailLinkSent(sent=await _service(request).send_link(client_id))


@router.post("/verification/confirm", status_code=204)
async def confirm_email(body: EmailConfirm, request: Request) -> None:
    """Spend the link from the email. It works on any device, without signing in.

    `email_taken` when another account proved the same address first.
    """
    check_origin(request, "client", required=False)
    await _service(request).confirm(body.token)


routers: list[APIRouter] = [router]
