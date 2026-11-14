"""Passkey routes: the second step of sign-in, a client's own passkeys, and staff enrolment."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field

from app.modules.identity.api import (
    _REFUSALS,
    ClientSessionView,
    CurrentClient,
    CurrentStaff,
    Identity,
    Password,
    StaffSessionView,
    caller_address,
    check_origin,
    set_session_cookie,
    staff_view,
)
from app.modules.identity.authenticators import AuthenticatorService
from app.modules.identity.domain.permissions import Role
from app.modules.identity.domain.sessions import SecondStep, Transport
from app.modules.identity.passkeys import PasskeyService
from app.modules.identity.service import IdentityService, IssuedSession
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope

_MORE: dict[int | str, dict[str, object]] = {
    **_REFUSALS,
    410: {"model": ErrorEnvelope},
    503: {"model": ErrorEnvelope},
}
client_router = APIRouter(prefix="/client", tags=["client sign-in"], responses=_MORE)
staff_router = APIRouter(prefix="/staff", tags=["staff sign-in"], responses=_MORE)


def _service(request: Request) -> PasskeyService:
    service: PasskeyService = request.app.state.passkeys
    return service


Passkeys = Annotated[PasskeyService, Depends(_service)]


def _authenticators(request: Request) -> AuthenticatorService:
    service: AuthenticatorService = request.app.state.authenticators
    return service


Authenticators = Annotated[AuthenticatorService, Depends(_authenticators)]
# Six digits. Apps show them as "123 456", so a space is allowed.
Code = Annotated[str, Field(min_length=6, max_length=7)]

# What `navigator.credentials` returned, as JSON. The WebAuthn library checks its shape.
Credential = Annotated[dict[str, Any], Field(max_length=16)]
Token = Annotated[str, Field(min_length=20, max_length=128)]
PasskeyName = Annotated[str, Field(max_length=60)]


class PasskeyOptions(Contract):
    """Pass `public_key` to `navigator.credentials.create` or `.get` (after decoding base64url)."""

    public_key: dict[str, Any]


class LoginPasskeyOptions(Contract):
    login_id: PushId


class LoginPasskey(Contract):
    login_id: PushId
    credential: Credential


class ClientSignedIn(Contract):
    """A browser has its session in a cookie and `token` is null. The phone app gets `token`."""

    token: str | None
    idle_expires_at: datetime
    absolute_expires_at: datetime
    session: ClientSessionView


class PasskeyItem(Contract):
    id: PushId
    name: str
    created_at: datetime
    last_used_at: datetime | None


class NewPasskey(Contract):
    credential: Credential
    name: PasskeyName = ""


class EnrolmentToken(Contract):
    token: Token


class EnrolmentView(Contract):
    name: str
    email: str
    role: Role
    # The second steps this person may enrol with: `passkey`, `authenticator`, or both.
    methods: list[SecondStep]


class Enrol(Contract):
    token: Token
    password: Password
    credential: Credential
    passkey_name: PasskeyName = ""


# Clients


@client_router.post("/login/passkey/options")
async def client_login_passkey_options(
    body: LoginPasskeyOptions, request: Request, passkeys: Passkeys
) -> PasskeyOptions:
    check_origin(request, "client", required=False)
    return PasskeyOptions(public_key=await passkeys.sign_in_options(body.login_id, "client"))


@client_router.post("/login/passkey/verify")
async def client_login_passkey_verify(
    body: LoginPasskey, request: Request, response: Response, passkeys: Passkeys, identity: Identity
) -> ClientSignedIn:
    """Finish sign-in. A browser is given a cookie, never the token itself."""
    issued = await passkeys.sign_in(
        body.login_id, "client", body.credential, client_transport(request)
    )
    return await client_signed_in(request, response, identity, issued)


async def client_signed_in(
    request: Request, response: Response, identity: IdentityService, issued: IssuedSession
) -> ClientSignedIn:
    """Hand a new client session to a browser as a cookie, or to the phone app as a token."""
    browser = issued.transport == "cookie"
    session = await identity.client_session(issued.token)
    if session is None:
        raise ApplicationError("unauthorized")
    if browser:
        set_session_cookie(request, response, "client", issued, request.app.state.clock.now())
    return ClientSignedIn(
        token=None if browser else issued.token,
        idle_expires_at=issued.idle_expires_at,
        absolute_expires_at=issued.absolute_expires_at,
        session=ClientSessionView(
            client_id=session.client_id,
            phone=session.phone,
            restricted_until=session.restricted_until,
            email=session.email,
            email_verified=session.email_verified,
        ),
    )


def client_transport(request: Request) -> Transport:
    """A browser always sends Origin on these requests and gets a cookie. The app gets a token."""
    check_origin(request, "client", required=False)
    return "cookie" if request.headers.get("origin") is not None else "token"


@client_router.get("/passkeys")
async def client_passkeys(client: CurrentClient, passkeys: Passkeys) -> list[PasskeyItem]:
    return [
        PasskeyItem(id=p.id, name=p.name, created_at=p.created_at, last_used_at=p.last_used_at)
        for p in await passkeys.list(client)
    ]


@client_router.post("/passkeys/options")
async def client_passkey_options(client: CurrentClient, passkeys: Passkeys) -> PasskeyOptions:
    """Start adding a passkey. Needs a sign-in in the last 10 minutes, or answers 403."""
    return PasskeyOptions(public_key=await passkeys.client_registration_options(client))


@client_router.post("/passkeys", status_code=201, responses={409: {"model": ErrorEnvelope}})
async def client_passkey_add(
    body: NewPasskey, client: CurrentClient, passkeys: Passkeys
) -> PasskeyItem:
    added = await passkeys.add(client, body.credential, body.name)
    return PasskeyItem(id=added.id, name=added.name, created_at=added.created_at, last_used_at=None)


@client_router.delete("/passkeys/{passkey_id}", status_code=204)
async def client_passkey_remove(
    passkey_id: PushId, client: CurrentClient, passkeys: Passkeys
) -> None:
    await passkeys.remove(client, passkey_id)


class LoginCode(Contract):
    login_id: PushId
    code: Code


class AuthenticatorSetupView(Contract):
    """Show `uri` as a QR code, and `secret` for typing in by hand. Neither is shown again."""

    secret: str
    uri: str


class AuthenticatorState(Contract):
    enabled: bool


class ConfirmCode(Contract):
    code: Code


@client_router.post("/login/authenticator/verify")
async def client_login_authenticator_verify(
    body: LoginCode,
    request: Request,
    response: Response,
    authenticators: Authenticators,
    identity: Identity,
) -> ClientSignedIn:
    """Finish sign-in with a code from the authenticator app. The session is not restricted."""
    issued = await authenticators.sign_in(
        body.login_id, body.code, "client", client_transport(request)
    )
    return await client_signed_in(request, response, identity, issued)


@client_router.get("/authenticator")
async def client_authenticator(
    client: CurrentClient, authenticators: Authenticators
) -> AuthenticatorState:
    return AuthenticatorState(enabled=await authenticators.client_has(client))


class SecondSteps(Contract):
    """What the client can finish a sign-in with, strongest first."""

    methods: list[SecondStep]


@client_router.get("/second-steps")
async def client_second_steps(
    client: CurrentClient, passkeys: Passkeys, authenticators: Authenticators
) -> SecondSteps:
    methods: list[SecondStep] = ["passkey"] if await passkeys.list(client) else []
    if await authenticators.client_has(client):
        methods.append("authenticator")
    # A texted or emailed code is always there as the backup.
    methods.append("code")
    return SecondSteps(methods=methods)


@client_router.post("/authenticator/options")
async def client_authenticator_options(
    client: CurrentClient, authenticators: Authenticators
) -> AuthenticatorSetupView:
    """Start adding an authenticator app. Needs a sign-in in the last 10 minutes."""
    setup = await authenticators.client_setup(client)
    return AuthenticatorSetupView(secret=setup.secret, uri=setup.uri)


@client_router.post("/authenticator", status_code=204)
async def client_authenticator_confirm(
    body: ConfirmCode, client: CurrentClient, authenticators: Authenticators
) -> None:
    """Turn the app on with a first code from it."""
    await authenticators.client_confirm(client, body.code)


@client_router.delete("/authenticator", status_code=204)
async def client_authenticator_remove(
    client: CurrentClient, authenticators: Authenticators
) -> None:
    await authenticators.client_remove(client)


# Staff


async def _staff_signed_in(
    request: Request, response: Response, identity: IdentityService, issued: IssuedSession
) -> StaffSessionView:
    session = await identity.staff_session(issued.token)
    if session is None:
        raise ApplicationError("unauthorized")
    set_session_cookie(request, response, "staff", issued, request.app.state.clock.now())
    return await staff_view(identity, session)


@staff_router.post("/login/passkey/options")
async def staff_login_passkey_options(
    body: LoginPasskeyOptions, request: Request, passkeys: Passkeys
) -> PasskeyOptions:
    check_origin(request, "staff", required=True)
    return PasskeyOptions(public_key=await passkeys.sign_in_options(body.login_id, "staff"))


@staff_router.post("/login/passkey/verify")
async def staff_login_passkey_verify(
    body: LoginPasskey, request: Request, response: Response, passkeys: Passkeys, identity: Identity
) -> StaffSessionView:
    """Finish staff sign-in and set the session cookie."""
    check_origin(request, "staff", required=True)
    issued = await passkeys.sign_in(body.login_id, "staff", body.credential, "cookie")
    return await _staff_signed_in(request, response, identity, issued)


@staff_router.post("/enrolment")
async def staff_enrolment(
    body: EnrolmentToken, request: Request, passkeys: Passkeys, authenticators: Authenticators
) -> EnrolmentView:
    """Who an enrolment link is for. The token travels in the body, never in a URL path."""
    check_origin(request, "staff", required=True)
    found = await passkeys.enrolment(body.token, address=caller_address(request))
    methods: list[SecondStep] = []
    if passkeys.enabled("staff"):
        methods.append("passkey")
    if authenticators.enabled:
        methods.append("authenticator")
    return EnrolmentView(name=found.name, email=found.email, role=found.role, methods=methods)


@staff_router.post("/enrolment/passkey/options")
async def staff_enrolment_passkey_options(
    body: EnrolmentToken, request: Request, passkeys: Passkeys
) -> PasskeyOptions:
    check_origin(request, "staff", required=True)
    options = await passkeys.enrolment_options(body.token, address=caller_address(request))
    return PasskeyOptions(public_key=options)


@staff_router.post("/enrolment/passkey/verify")
async def staff_enrolment_passkey_verify(
    body: Enrol, request: Request, response: Response, passkeys: Passkeys, identity: Identity
) -> StaffSessionView:
    """Set the password, add the passkey and sign in. The link then stops working."""
    check_origin(request, "staff", required=True)
    issued = await passkeys.enrol(
        body.token,
        body.password,
        body.credential,
        body.passkey_name,
        address=caller_address(request),
    )
    return await _staff_signed_in(request, response, identity, issued)


class EnrolWithApp(Contract):
    token: Token
    password: Password
    code: Code


@staff_router.post("/login/authenticator/verify")
async def staff_login_authenticator_verify(
    body: LoginCode,
    request: Request,
    response: Response,
    authenticators: Authenticators,
    identity: Identity,
) -> StaffSessionView:
    """Finish staff sign-in with a code from the authenticator app."""
    check_origin(request, "staff", required=True)
    issued = await authenticators.sign_in(body.login_id, body.code)
    return await _staff_signed_in(request, response, identity, issued)


@staff_router.post("/enrolment/authenticator/options")
async def staff_enrolment_authenticator_options(
    body: EnrolmentToken, request: Request, authenticators: Authenticators
) -> AuthenticatorSetupView:
    """Start enrolling with an authenticator app in place of a passkey."""
    check_origin(request, "staff", required=True)
    setup = await authenticators.enrolment_setup(body.token, address=caller_address(request))
    return AuthenticatorSetupView(secret=setup.secret, uri=setup.uri)


@staff_router.post("/enrolment/authenticator/verify")
async def staff_enrolment_authenticator_verify(
    body: EnrolWithApp,
    request: Request,
    response: Response,
    authenticators: Authenticators,
    identity: Identity,
) -> StaffSessionView:
    """Set the password, confirm the app with a code and sign in. The link then stops working."""
    check_origin(request, "staff", required=True)
    issued = await authenticators.enrol(
        body.token, body.password, body.code, address=caller_address(request)
    )
    return await _staff_signed_in(request, response, identity, issued)


routers: list[APIRouter] = [client_router, staff_router]


# A member of staff confirming it is them again before an approval


class Confirmed(Contract):
    # Approving another person's change, or for a client changing how they sign in, works
    # without asking again until then.
    confirmed_until: datetime


class ConfirmWithPasskey(Contract):
    credential: Credential


@client_router.post("/session/confirmation/passkey/options")
async def client_confirmation_passkey_options(
    request: Request, client: CurrentClient, passkeys: Passkeys
) -> PasskeyOptions:
    """Ask the device to sign again, for this session only."""
    check_origin(request, "client", required=False)
    return PasskeyOptions(public_key=await passkeys.client_reconfirm_options(client))


@client_router.post("/session/confirmation/passkey")
async def client_confirmation_passkey(
    body: ConfirmWithPasskey, request: Request, client: CurrentClient, passkeys: Passkeys
) -> Confirmed:
    """Confirm it is you with a passkey, so sign-in changes work for the next ten minutes."""
    check_origin(request, "client", required=False)
    return Confirmed(confirmed_until=await passkeys.client_reconfirm(client, body.credential))


@client_router.post("/session/confirmation/authenticator")
async def client_confirmation_authenticator(
    body: ConfirmCode, client: CurrentClient, authenticators: Authenticators
) -> Confirmed:
    """Confirm it is you with a code from the authenticator app."""
    return Confirmed(confirmed_until=await authenticators.client_reconfirm(client, body.code))


@staff_router.post("/session/confirmation/passkey/options")
async def staff_confirmation_passkey_options(
    request: Request, staff: CurrentStaff, passkeys: Passkeys
) -> PasskeyOptions:
    """Ask the device to sign again, for this session only."""
    check_origin(request, "staff", required=True)
    return PasskeyOptions(public_key=await passkeys.reconfirm_options(staff))


@staff_router.post("/session/confirmation/passkey")
async def staff_confirmation_passkey(
    body: ConfirmWithPasskey, request: Request, staff: CurrentStaff, passkeys: Passkeys
) -> Confirmed:
    """Confirm it is you with a passkey, so approvals work for the next ten minutes."""
    check_origin(request, "staff", required=True)
    return Confirmed(confirmed_until=await passkeys.reconfirm(staff, body.credential))


@staff_router.post("/session/confirmation/authenticator")
async def staff_confirmation_authenticator(
    body: ConfirmCode, request: Request, staff: CurrentStaff, authenticators: Authenticators
) -> Confirmed:
    """Confirm it is you with a code from the authenticator app."""
    check_origin(request, "staff", required=True)
    return Confirmed(confirmed_until=await authenticators.reconfirm(staff, body.code))
