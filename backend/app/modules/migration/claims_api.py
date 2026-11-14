"""Routes for a client carried over from Finvest 1.0 to claim their account.

The one-time link from the invitation opens the claim. The visitor then proves the phone on
file with a texted code and sets a password, and is signed in. A bad link, a spent one and an
account already claimed all get the same answer, `not_found`, so a link says nothing about
anyone it does not belong to.

Signed in, the client takes their identity step with the identity checks module's own routes:
the selfie and ID photo of a claim check, or the full ID check. They hand it in here, so the
check and the claim's step move in one transaction. The decision comes back to the worker as an
event. Last, the client confirms the investments shown, or says something is wrong.
"""

from typing import Annotated

import structlog
from fastapi import APIRouter, Request, Response
from pydantic import Field

from app.infrastructure.database import Database
from app.infrastructure.sealing import Sealer
from app.modules.identity.api import (
    _REFUSALS,
    CurrentClient,
    Identity,
    Password,
    caller_address,
    check_origin,
)
from app.modules.identity.codes import CodeService
from app.modules.identity.passkeys_api import (
    ClientSignedIn,
    Code,
    client_signed_in,
    client_transport,
)
from app.modules.kyc import claims as claim_checks
from app.modules.kyc import service as identity_checks
from app.modules.kyc.domain.provider import IdentityProvider
from app.modules.migration import claims
from app.modules.migration.domain.claim import ClaimStatus, IdentityStep
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/client/claim",
    tags=["account claim"],
    responses={**_REFUSALS, 410: {"model": ErrorEnvelope}, 429: {"model": ErrorEnvelope}},
)

# The link's token: 43 URL-safe characters. Anything else is simply not found.
LinkToken = Annotated[str, Field(min_length=1, max_length=128)]


class ClaimStart(Contract):
    token: LinkToken


class ClaimStarted(Contract):
    """A code was texted to the phone on file for the account the link belongs to."""

    challenge_id: PushId


class ClaimComplete(Contract):
    token: LinkToken
    challenge_id: PushId
    code: Code
    password: Password


class ClaimStateView(Contract):
    """Where the signed-in client is in claiming their account. Nothing from Finvest 1.0."""

    status: ClaimStatus
    identity_step: IdentityStep
    problem_reported: bool


class ProblemReport(Contract):
    description: str = Field(min_length=1, max_length=1000)


def _state(request: Request) -> tuple[Database, Clock, CodeService]:
    return request.app.state.database, request.app.state.clock, request.app.state.codes


async def _open(database: Database, clock: Clock, token: str) -> claims.OpenedLink:
    async with database.transaction() as session:
        return await claims.open_link(session, clock, token)


def _view(view: claims.ClaimView) -> ClaimStateView:
    return ClaimStateView(
        status=view.status,
        identity_step=view.identity_step,
        problem_reported=view.status is ClaimStatus.PROBLEM_REPORTED,
    )


@router.post("/start")
async def claim_start(body: ClaimStart, request: Request) -> ClaimStarted:
    """Open the link and text a code to the phone on file. Nothing is changed yet."""
    check_origin(request, "client", required=False)
    database, clock, codes = _state(request)
    link = await _open(database, clock, body.token)
    try:
        challenge_id = await codes.claim_start(link.client_id, address=caller_address(request))
    except ApplicationError as error:
        if error.code == "not_found":
            # Claimed or closed since the link opened: the same answer as a bad link.
            raise ApplicationError("not_found") from None
        raise
    return ClaimStarted(challenge_id=challenge_id)


@router.post("/complete")
async def claim_complete(
    body: ClaimComplete, request: Request, response: Response, identity: Identity
) -> ClientSignedIn:
    """The texted code and a new password. The account becomes the client's and is signed in.

    The link is checked again first, so one that expired or was replaced since `start` is
    refused before anything changes.
    """
    database, clock, codes = _state(request)
    transport = client_transport(request)
    link = await _open(database, clock, body.token)
    # A challenge for another account is refused as not found, and nothing changes.
    claimed = await codes.claim_complete(
        body.challenge_id, body.code, body.password, transport, client_id=link.client_id
    )
    client = Actor("client", claimed.client_id)
    # Signing in has committed in identity's own transaction, so the account is the client's
    # whatever happens next. If recording it here fails, the client is still signed in and the
    # claim stays at invited. The reminder run skips an account that is no longer unclaimed.
    try:
        async with database.actor_transaction(client) as session:
            await claims.record_signed_in(session, client, clock, link)
    except ApplicationError as error:
        logger.warning("claim_sign_in_not_recorded", error_code=error.code)
    return await client_signed_in(request, response, identity, claimed.session)


@router.get("")
async def my_claim(request: Request, client: CurrentClient) -> ClaimStateView:
    """The signed-in client's claim. `not_found` for a client who signed up in Finvest 2.0."""
    database, _, _ = _state(request)
    async with database.actor_transaction(client.actor) as session:
        view = await claims.claim_for(session, client.client_id)
    if view is None:
        raise ApplicationError("not_found")
    return _view(view)


@router.post("/problem")
async def report_problem(
    body: ProblemReport, request: Request, client: CurrentClient
) -> ClaimStateView:
    """The client says something carried over is wrong. Staff look into it."""
    check_origin(request, "client", required=False)
    database, clock, _ = _state(request)
    async with database.actor_transaction(client.actor) as session:
        view = await claims.report_problem(
            session, client.actor, clock, description=body.description
        )
    return _view(view)


def _identity(request: Request) -> tuple[Sealer, IdentityProvider]:
    sealer: Sealer | None = request.app.state.migration_sealer
    provider: IdentityProvider | None = getattr(request.app.state, "identity_provider", None)
    if sealer is None or provider is None:
        # Without the identity data key no check can be read or handed in.
        raise ApplicationError("unavailable")
    return sealer, provider


@router.post("/identity")
async def hand_in_identity(request: Request, client: CurrentClient) -> ClaimStateView:
    """Hand in the identity step: the claim check's photos, or the full ID check.

    `conflict` unless the claim is waiting for it. Answers which photos or details are missing,
    as the identity check routes do, and then nothing changes.
    """
    check_origin(request, "client", required=False)
    database, clock, _ = _state(request)
    async with database.actor_transaction(client.actor) as session:
        claim = await claims.claim_for(session, client.client_id)
        if claim is None:
            raise ApplicationError("not_found")
        if claim.status is not ClaimStatus.SIGNED_IN:
            raise ApplicationError("conflict")
        if claim.identity_step is IdentityStep.SELFIE:
            await claim_checks.submit(session, client.actor, clock)
        else:
            sealer, provider = _identity(request)
            await identity_checks.submit(session, sealer, client.actor, provider, clock)
        view = await claims.submit_identity(session, client.actor, clock)
    return _view(view)


@router.post("/confirm")
async def confirm_investments(request: Request, client: CurrentClient) -> ClaimStateView:
    """The client says the investments shown are right. Final. `conflict` before the identity
    step is confirmed, or once a problem is reported."""
    check_origin(request, "client", required=False)
    database, clock, _ = _state(request)
    async with database.actor_transaction(client.actor) as session:
        view = await claims.confirm_investments(session, client.actor, clock)
    return _view(view)
