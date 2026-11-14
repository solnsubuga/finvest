"""Sign-in routes, and the dependencies every other module uses to know who is calling.

A browser holds its session in an HttpOnly cookie and must call from an allowed origin. The
phone app holds a token and sends it as `Authorization: Bearer`. Staff use the console in a
browser only, so staff sessions are always cookies.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field, model_validator

from app.infrastructure.settings import Settings
from app.modules.identity.domain.permissions import Permission, Role
from app.modules.identity.domain.sessions import SecondStep
from app.modules.identity.service import (
    CONFIRMATION_WINDOW,
    AccountKind,
    ClientSession,
    IdentityService,
    IssuedSession,
    StaffSession,
)
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope

COOKIES: dict[AccountKind, str] = {
    "client": "finvest_client_session",
    "staff": "finvest_staff_session",
}
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_BEARER = "bearer "

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    429: {"model": ErrorEnvelope},
}

client_router = APIRouter(prefix="/client", tags=["client sign-in"], responses=_REFUSALS)
staff_router = APIRouter(prefix="/staff", tags=["staff sign-in"], responses=_REFUSALS)


def _service(request: Request) -> IdentityService:
    service: IdentityService = request.app.state.identity
    return service


Identity = Annotated[IdentityService, Depends(_service)]


def caller_address(request: Request) -> str | None:
    """The caller's network address, as the trusted proxy reported it. Used only for limits."""
    return request.client.host if request.client else None


def _allowed_origins(request: Request, kind: AccountKind) -> list[str]:
    settings: Settings = request.app.state.settings
    return settings.client_allowed_origins if kind == "client" else settings.staff_allowed_origins


def check_origin(request: Request, kind: AccountKind, *, required: bool) -> None:
    """Refuse a browser calling from anywhere but this audience's own site.

    A browser always sends `Origin` on a cross-site or unsafe request, and a page cannot forge
    it. The phone app sends none, and holds no cookie for a hostile page to ride on.
    """
    origin = request.headers.get("origin")
    if origin is None:
        if required:
            raise ApplicationError("forbidden")
        return
    if origin not in _allowed_origins(request, kind):
        raise ApplicationError("forbidden")


def _credentials(request: Request, kind: AccountKind) -> str | None:
    """The session token on this request, after the origin rules for how it arrived."""
    authorization = request.headers.get("authorization", "")
    if kind == "client" and authorization[: len(_BEARER)].lower() == _BEARER:
        check_origin(request, kind, required=False)
        return authorization[len(_BEARER) :].strip() or None
    token = request.cookies.get(COOKIES[kind])
    if token is None:
        return None
    # The cookie is sent by the browser on its own, so an unsafe request must prove its origin.
    check_origin(request, kind, required=request.method not in SAFE_METHODS)
    return token


async def current_client_even_locked(request: Request, identity: Identity) -> ClientSession:
    """The client's session, even one the app PIN has locked. Only for the PIN and the session."""
    token = _credentials(request, "client")
    session = await identity.client_session(token) if token else None
    if session is None:
        raise ApplicationError("unauthorized")
    return session


async def current_client(request: Request, identity: Identity) -> ClientSession:
    session = await current_client_even_locked(request, identity)
    if session.locked:
        # The phone app has been unused for a while: only its PIN opens it again.
        raise ApplicationError("app_locked")
    return session


async def current_staff(request: Request, identity: Identity) -> StaffSession:
    token = _credentials(request, "staff")
    session = await identity.staff_session(token, caller_address(request)) if token else None
    if session is None:
        raise ApplicationError("unauthorized")
    return session


CurrentClient = Annotated[ClientSession, Depends(current_client)]
CurrentClientEvenLocked = Annotated[ClientSession, Depends(current_client_even_locked)]
CurrentStaff = Annotated[StaffSession, Depends(current_staff)]


def require_permission(permission: Permission) -> Callable[..., Awaitable[StaffSession]]:
    """A dependency for a staff route: signed in, and the role holds `permission`.

    Declare it on every staff route. What the console shows or hides is not a check.
    """

    async def check(staff: CurrentStaff) -> StaffSession:
        if permission not in staff.permissions:
            raise ApplicationError("forbidden")
        return staff

    return check


def require_confirmed(permission: Permission) -> Callable[..., Awaitable[StaffSession]]:
    """`require_permission`, and the person used their second step in the last ten minutes.

    Declare it on a route that approves or decides another person's change. A refusal is
    `confirm_it_is_you`: the console asks for the passkey or app code, then sends it again.
    """

    async def check(request: Request, staff: CurrentStaff) -> StaffSession:
        if permission not in staff.permissions:
            raise ApplicationError("forbidden")
        now = request.app.state.clock.now()
        if now - staff.confirmed_at > CONFIRMATION_WINDOW:
            raise ApplicationError("confirm_it_is_you")
        return staff

    return check


def set_session_cookie(
    request: Request, response: Response, kind: AccountKind, issued: IssuedSession, now: datetime
) -> None:
    """Hand a browser its session. The second sign-in step calls this."""
    settings: Settings = request.app.state.settings
    response.set_cookie(
        COOKIES[kind],
        issued.token,
        max_age=max(1, int((issued.absolute_expires_at - now).total_seconds())),
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _clear_session_cookie(request: Request, response: Response, kind: AccountKind) -> None:
    settings: Settings = request.app.state.settings
    response.delete_cookie(
        COOKIES[kind], path="/", secure=settings.cookie_secure, httponly=True, samesite="strict"
    )


# A long password is cut off here so nobody can make the server hash megabytes.
Password = Annotated[str, Field(min_length=1, max_length=1024)]


class ClientLoginStart(Contract):
    """The mobile number, or an email address the client has proved, with the password."""

    phone: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    email: Annotated[str, Field(min_length=1, max_length=254)] | None = None
    password: Password

    @model_validator(mode="after")
    def one_identifier(self) -> "ClientLoginStart":
        if (self.phone is None) == (self.email is None):
            raise ValueError("Send either phone or email")
        return self


class StaffLoginStart(Contract):
    email: Annotated[str, Field(min_length=1, max_length=254)]
    password: Password


class LoginStarted(Contract):
    """Step one passed. Nothing is open yet: finish with one of `methods`."""

    login_id: PushId
    methods: list[SecondStep]


class ClientSessionView(Contract):
    client_id: PushId
    phone: str
    # Set for a day after signing in with a code: withdrawals go to known accounts only.
    restricted_until: datetime | None
    email: str | None = None
    # Whether the client proved `email` with a link. Statements go only to a proved address.
    email_verified: bool = False
    # The phone app's PIN for this session. Always false in a browser.
    pin_set: bool = False
    # True when the PIN must be entered before anything else answers (423 `app_locked`).
    locked: bool = False


class StaffSessionView(Contract):
    staff_id: PushId
    name: str
    role: Role
    permissions: list[Permission]
    # Approving another person's change works without asking again until then.
    confirmed_until: datetime
    # What this person can confirm it is them with, passkey first.
    second_steps: list[SecondStep]


async def staff_view(identity: IdentityService, staff: StaffSession) -> StaffSessionView:
    return StaffSessionView(
        staff_id=staff.staff_id,
        name=staff.name,
        role=staff.role,
        permissions=staff.permissions,
        confirmed_until=staff.confirmed_at + CONFIRMATION_WINDOW,
        second_steps=await identity.staff_second_steps(staff),
    )


@client_router.post("/login/start")
async def client_login_start(
    body: ClientLoginStart, request: Request, identity: Identity
) -> LoginStarted:
    """Check the mobile number or proved email, and the password. Never opens a session."""
    check_origin(request, "client", required=False)
    by_email = body.email is not None
    challenge = await identity.start_login(
        "client",
        body.email if body.email is not None else (body.phone or ""),
        body.password,
        address=caller_address(request),
        by_email=by_email,
    )
    return LoginStarted(login_id=challenge.id, methods=challenge.methods)


@client_router.get("/session")
async def client_session(client: CurrentClientEvenLocked) -> ClientSessionView:
    """The signed-in client. A locked session answers too, so the app knows to ask for the PIN."""
    return ClientSessionView(
        client_id=client.client_id,
        phone=client.phone,
        restricted_until=client.restricted_until,
        email=client.email,
        email_verified=client.email_verified,
        pin_set=client.pin_set,
        locked=client.locked,
    )


async def _sign_out(
    request: Request, response: Response, identity: IdentityService, kind: AccountKind
) -> None:
    token = _credentials(request, kind)
    if token:
        await identity.sign_out(token, kind)
    _clear_session_cookie(request, response, kind)


@client_router.post("/sign-out", status_code=204)
async def client_sign_out(request: Request, response: Response, identity: Identity) -> None:
    """End this session. Succeeds whether or not one was open."""
    await _sign_out(request, response, identity, "client")


@staff_router.post("/login/start")
async def staff_login_start(
    body: StaffLoginStart, request: Request, identity: Identity
) -> LoginStarted:
    """Check the work email and password. A passkey or an authenticator app code must follow.

    Staff have no texted or emailed code option.
    """
    check_origin(request, "staff", required=True)
    challenge = await identity.start_login(
        "staff", body.email, body.password, address=caller_address(request)
    )
    return LoginStarted(login_id=challenge.id, methods=challenge.methods)


@staff_router.get("/session")
async def staff_session(staff: CurrentStaff, identity: Identity) -> StaffSessionView:
    return await staff_view(identity, staff)


@staff_router.post("/sign-out", status_code=204)
async def staff_sign_out(request: Request, response: Response, identity: Identity) -> None:
    await _sign_out(request, response, identity, "staff")


routers: list[APIRouter] = [client_router, staff_router]
