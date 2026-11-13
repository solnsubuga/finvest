"""Routes for proving a client's email address with a one-time link."""

from fastapi import APIRouter, Request

from app.modules.identity.api import _REFUSALS, CurrentClient, check_origin
from app.modules.identity.email_checks import EmailCheckService
from app.modules.identity.passkeys_api import Token
from app.shared.contracts import Contract
from app.shared.errors import ErrorEnvelope

router = APIRouter(
    prefix="/client/email",
    tags=["client email"],
    responses={**_REFUSALS, 410: {"model": ErrorEnvelope}},
)


def _service(request: Request) -> EmailCheckService:
    service: EmailCheckService = request.app.state.email_checks
    return service


class EmailLinkSent(Contract):
    """`sent` is false when the address was already proved, so nothing was sent."""

    sent: bool


class EmailConfirm(Contract):
    token: Token


@router.post("/verification")
async def send_email_link(request: Request, client: CurrentClient) -> EmailLinkSent:
    """Email a one-time link to the client's address, to prove it is theirs."""
    return EmailLinkSent(sent=await _service(request).send_link(client.client_id))


@router.post("/verification/confirm", status_code=204)
async def confirm_email(body: EmailConfirm, request: Request) -> None:
    """Spend the link from the email. It works on any device, without signing in."""
    check_origin(request, "client", required=False)
    await _service(request).confirm(body.token)


routers: list[APIRouter] = [router]
