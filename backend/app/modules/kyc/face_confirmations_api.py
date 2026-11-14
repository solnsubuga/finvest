"""Routes for a live selfie that confirms a sign-in change, and the check the change routes use.

Before a signed-in client changes their password or mobile number, they confirm with a passkey
or authenticator app and, when their approved identity check has a selfie, with a live selfie
too. The app starts an attempt, opens the provider's page from `url`, and reads the attempt
until it is `passed` or has ended. A passed attempt confirms a change on the same session for
ten minutes. See docs/sign-in.md#changing-how-you-sign-in.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.infrastructure.database import Database
from app.infrastructure.sealing import Sealer
from app.modules.identity.api import CurrentClient, CurrentConfirmedClient
from app.modules.identity.service import ClientSession
from app.modules.kyc import face_confirmations as confirmations
from app.modules.kyc.domain.face_confirmations import ConfirmationStatus, FaceMatcher
from app.modules.kyc.domain.liveness import LivenessProvider
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 423, 503)
}


class SelfieConfirmation(Contract):
    """One attempt. Open the `url` page while `status` is `open`."""

    id: PushId
    status: ConfirmationStatus
    # The provider's page. Only while the attempt is open; it is this client's alone.
    url: str | None
    # Why it failed or did not start, as a stable code, such as `face_not_matched`.
    failure_code: str | None
    expires_at: datetime
    # Until when a passed attempt confirms a password or number change on this session.
    counts_until: datetime | None


class SelfieConfirmationState(Contract):
    """Whether a password or number change on this session needs a live selfie, and has one."""

    # False when the client has no selfie on file or liveness is off: a passkey or app is enough.
    needed: bool
    # True while a passed attempt on this session confirms a change.
    confirmed: bool
    # Why a new attempt cannot start now, such as `too_many_attempts`, or null.
    problem: str | None
    attempts_left: int
    latest: SelfieConfirmation | None


def _out(view: confirmations.ConfirmationView) -> SelfieConfirmation:
    return SelfieConfirmation(
        id=view.id,
        status=view.status,
        url=view.url,
        failure_code=view.failure_code,
        expires_at=view.expires_at,
        counts_until=view.counts_until,
    )


def _database(request: Request) -> Database:
    held: Database = request.app.state.database
    return held


def _clock(request: Request) -> Clock:
    held: Clock = request.app.state.clock
    return held


def _on(request: Request) -> bool:
    return bool(getattr(request.app.state, "face_confirmations_on", False))


async def current_face_confirmed_client(
    request: Request, client: CurrentConfirmedClient
) -> ClientSession:
    """A client who used a passkey or app, and a live selfie when one is needed, just now.

    Declare it on a route that changes the password or mobile number. The passkey or app comes
    first (`confirm_it_is_you`), then the selfie (403 `confirm_with_selfie`): the app takes it
    with `POST /client/session/confirmation/selfie`, then sends the change again.
    """
    async with _database(request).actor_transaction(client.actor) as session:
        if await confirmations.needed(
            session, client.client_id, on=_on(request)
        ) and not await confirmations.confirmed(
            session, client.client_id, client.session_id, _clock(request)
        ):
            raise ApplicationError("confirm_with_selfie")
    return client


CurrentFaceConfirmedClient = Annotated[ClientSession, Depends(current_face_confirmed_client)]


def create_router(
    sealer: Sealer | None,
    liveness: LivenessProvider | None,
    matcher: FaceMatcher | None,
) -> APIRouter:
    """The selfie confirmation routes. With liveness or the face match off, `needed` is false."""
    router = APIRouter(
        prefix="/client/session/confirmation/selfie",
        tags=["client sign-in"],
        responses=_REFUSALS,
    )

    def seal() -> Sealer:
        if sealer is None:
            raise ApplicationError("unavailable")
        return sealer

    @router.get("")
    async def selfie_confirmation(
        client: CurrentClient, request: Request
    ) -> SelfieConfirmationState:
        """Whether a password or number change here needs a live selfie, and the latest attempt."""
        async with _database(request).actor_transaction(client.actor) as session:
            state = await confirmations.state(
                session,
                sealer,
                client.actor,
                client.session_id,
                _clock(request),
                on=_on(request),
            )
        return SelfieConfirmationState(
            needed=state.needed,
            confirmed=state.confirmed,
            problem=state.problem,
            attempts_left=state.attempts_left,
            latest=None if state.latest is None else _out(state.latest),
        )

    @router.post("", status_code=201)
    async def start_selfie_confirmation(
        client: CurrentClient, request: Request
    ) -> SelfieConfirmation:
        """Start a live selfie. Any attempt still waiting is retired.

        It starts as `starting`. Read it with `GET .../{id}` every few seconds until it is
        `open`, open its `url`, and read it again until it ends. A client with no approved check
        selfie is refused with `no_selfie_on_file`, and a sixth attempt in a day with
        `too_many_attempts`.
        """
        if liveness is None or matcher is None or not _on(request):
            raise ApplicationError("unavailable")
        async with _database(request).actor_transaction(client.actor) as session:
            seal()  # refuse before saving anything when the data key is missing
            view = await confirmations.start(
                session,
                client.actor,
                client.session_id,
                liveness.name,
                matcher.name,
                _clock(request),
            )
        return _out(view)

    @router.get("/{attempt_id}")
    async def selfie_confirmation_attempt(
        attempt_id: PushId, client: CurrentClient, request: Request
    ) -> SelfieConfirmation:
        """One of the client's own attempts."""
        async with _database(request).actor_transaction(client.actor) as session:
            view = await confirmations.attempt(
                session, seal(), client.actor, attempt_id, _clock(request)
            )
        return _out(view)

    return router
