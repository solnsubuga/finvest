"""Routes for claim checks: the selfie and ID photo a client carried over from Finvest 1.0 takes
when they claim their account, and the staff member who compares them. See
docs/identity-checks.md. The claim itself, and the step it is at, are the migration module's."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile

from app.infrastructure.database import Database
from app.infrastructure.storage import FileStorage, InvalidFile, NewFile, StorageError
from app.modules.identity.api import CurrentClient, require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.kyc import claims
from app.modules.kyc.api import (
    _REFUSALS,
    VIEW_LINK_LIFETIME,
    AskForPhotosBody,
    ClientPhoto,
    PhotoLink,
    PhotoLinks,
    ReasonBody,
    StaffPhoto,
    _discard,
)
from app.modules.kyc.domain.checks import PhotoKind, photo_matches_type
from app.modules.kyc.domain.claims import ClaimCheckStatus
from app.modules.kyc.service import MAX_PHOTO_BYTES
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id


class ClientClaimCheck(Contract):
    """The client's own claim check. Nothing about who decided."""

    id: PushId
    status: ClaimCheckStatus
    photos: list[ClientPhoto]
    # The photos still needed before it can be handed in.
    missing: list[str]
    retake_photos: list[PhotoKind]
    # Of the photos asked for again, those already taken again.
    retaken: list[PhotoKind]
    client_message: str | None
    submitted_at: datetime | None


class ClientClaimCheckEnvelope(Contract):
    check: ClientClaimCheck | None


class StaffClaimCheck(Contract):
    id: PushId
    client_id: PushId
    # The carried-over identity check to compare with: open it at /staff/identity-checks/{id}.
    check_id: PushId
    status: ClaimCheckStatus
    submission: int
    submitted_at: datetime | None
    photos: list[StaffPhoto]
    retake_photos: list[PhotoKind]
    client_message: str | None
    decided_at: datetime | None


class ClaimQueue(Contract):
    items: list[StaffClaimCheck]


def _client(view: claims.ClaimCheckView) -> ClientClaimCheck:
    return ClientClaimCheck(
        id=view.id,
        status=view.status,
        photos=[
            ClientPhoto(kind=p.kind, content_type=p.content_type, byte_size=p.byte_size)
            for p in view.photos
        ],
        missing=view.missing,
        retake_photos=sorted(view.retake_photos),
        retaken=sorted(view.retaken),
        client_message=view.client_message,
        submitted_at=view.submitted_at,
    )


def _staff(view: claims.ClaimCheckView) -> StaffClaimCheck:
    return StaffClaimCheck(
        id=view.id,
        client_id=view.client_id,
        check_id=view.check_id,
        status=view.status,
        submission=view.submission,
        submitted_at=view.submitted_at,
        photos=[StaffPhoto(id=p.id, kind=p.kind) for p in view.photos],
        retake_photos=sorted(view.retake_photos),
        client_message=view.client_message,
        decided_at=view.decided_at,
    )


def create_routers(storage: FileStorage) -> list[APIRouter]:
    client_router = APIRouter(
        prefix="/client/claim-check", tags=["identity checks"], responses=_REFUSALS
    )
    staff_router = APIRouter(
        prefix="/staff/claim-checks", tags=["identity checks"], responses=_REFUSALS
    )
    Viewer = Annotated[StaffSession, Depends(require_permission("identity_checks.view"))]
    Reviewer = Annotated[StaffSession, Depends(require_permission("identity_checks.review"))]
    # Deciding needs a passkey or app code from the last few minutes.
    Decider = Annotated[StaffSession, Depends(require_confirmed("identity_checks.review"))]

    def database(request: Request) -> Database:
        held: Database = request.app.state.database
        return held

    def clock(request: Request) -> Clock:
        held: Clock = request.app.state.clock
        return held

    # The client ------------------------------------------------------------------------------

    @client_router.get("")
    async def my_claim_check(client: CurrentClient, request: Request) -> ClientClaimCheckEnvelope:
        """The signed-in client's claim check, or `null` before they have started it."""
        async with database(request).actor_transaction(client.actor) as session:
            view = await claims.for_client(session, client.actor)
        return ClientClaimCheckEnvelope(check=None if view is None else _client(view))

    @client_router.post("")
    async def start(client: CurrentClient, request: Request) -> ClientClaimCheck:
        """Start the claim check. Only a client carried over from Finvest 1.0 has one."""
        async with database(request).actor_transaction(client.actor) as session:
            view = await claims.start(session, client.actor, clock(request))
        return _client(view)

    @client_router.put("/photos/{kind}")
    async def add_photo(
        kind: PhotoKind,
        photo: Annotated[UploadFile, File(description="JPEG, PNG or WebP, at most 10 MiB")],
        client: CurrentClient,
        request: Request,
    ) -> ClientClaimCheck:
        """Send the selfie or the front of the ID as `multipart/form-data`."""
        data = await photo.read(MAX_PHOTO_BYTES + 1)
        content_type = photo.content_type or ""
        # Everything that can refuse the photo runs before the file is stored.
        async with database(request).actor_transaction(client.actor) as session:
            claim_check_id = await claims.photo_slot(
                session, client.actor, kind, content_type=content_type, byte_size=len(data)
            )
        if not photo_matches_type(content_type, data):
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["photo"], code="not_an_image_of_that_type")],
            )
        photo_id = push_id()
        key = claims.photo_key(claim_check_id, photo_id)
        try:
            stored = await storage.put(NewFile(key, data, content_type))
        except InvalidFile:
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["photo"], code="unsupported_content_type")],
            ) from None
        except StorageError:
            raise ApplicationError("unavailable") from None
        try:
            async with database(request).actor_transaction(client.actor) as session:
                await claims.add_photo(
                    session,
                    client.actor,
                    kind,
                    content_type=stored.content_type,
                    byte_size=stored.byte_size,
                    sha256=stored.sha256,
                    clock=clock(request),
                    photo_id=photo_id,
                )
                view = await claims.for_client(session, client.actor)
        except Exception:
            # The claim check changed in between, for example it was handed in.
            await _discard(storage, key, photo_id)
            raise
        if view is None:
            raise ApplicationError("not_found")
        return _client(view)

    @client_router.post("/submit")
    async def submit(client: CurrentClient, request: Request) -> ClientClaimCheck:
        """Hand the photos in. Answers which photos are missing if it is not ready."""
        async with database(request).actor_transaction(client.actor) as session:
            view = await claims.submit(session, client.actor, clock(request))
        return _client(view)

    # Staff -----------------------------------------------------------------------------------

    @staff_router.get("")
    async def queue(staff: Viewer, request: Request) -> ClaimQueue:
        """Claim checks waiting for a person, the longest waiting first."""
        async with database(request).actor_transaction(staff.actor) as session:
            items = await claims.review_queue(session)
        return ClaimQueue(items=[_staff(item) for item in items])

    @staff_router.get("/{claim_check_id}")
    async def one(claim_check_id: PushId, staff: Viewer, request: Request) -> StaffClaimCheck:
        async with database(request).actor_transaction(staff.actor) as session:
            return _staff(await claims.for_staff(session, claim_check_id))

    @staff_router.post("/{claim_check_id}/photo-links")
    async def photo_links(claim_check_id: PushId, staff: Viewer, request: Request) -> PhotoLinks:
        """Short-lived links to the photos. Each call is written to the audit trail."""
        now = clock(request).now()
        async with database(request).actor_transaction(staff.actor) as session:
            photos = await claims.photos_for_viewing(
                session, staff.actor, claim_check_id, clock(request)
            )
        return PhotoLinks(
            photos=[
                PhotoLink(
                    id=photo.id,
                    kind=photo.kind,
                    url=storage.read_link(
                        photo.storage_key,
                        content_type=photo.content_type,
                        lifetime=VIEW_LINK_LIFETIME,
                        now=now,
                    ).url,
                )
                for photo in photos
            ],
            expires_at=now + VIEW_LINK_LIFETIME,
        )

    @staff_router.post("/{claim_check_id}/match")
    async def match(
        claim_check_id: PushId, body: ReasonBody, staff: Decider, request: Request
    ) -> StaffClaimCheck:
        """The photos match each other and the carried-over check. It cannot be undone."""
        async with database(request).actor_transaction(staff.actor) as session:
            view = await claims.decide(
                session,
                staff.actor,
                claim_check_id,
                matched=True,
                reason=body.reason,
                clock=clock(request),
            )
        return _staff(view)

    @staff_router.post("/{claim_check_id}/no-match")
    async def no_match(
        claim_check_id: PushId, body: ReasonBody, staff: Decider, request: Request
    ) -> StaffClaimCheck:
        """The photos do not match. It cannot be undone; staff then follow the claim up."""
        async with database(request).actor_transaction(staff.actor) as session:
            view = await claims.decide(
                session,
                staff.actor,
                claim_check_id,
                matched=False,
                reason=body.reason,
                clock=clock(request),
            )
        return _staff(view)

    @staff_router.post("/{claim_check_id}/ask-for-photos")
    async def ask_for_photos(
        claim_check_id: PushId, body: AskForPhotosBody, staff: Reviewer, request: Request
    ) -> StaffClaimCheck:
        """Ask the client to take the named photos again, with a message."""
        async with database(request).actor_transaction(staff.actor) as session:
            view = await claims.ask_for_photos(
                session,
                staff.actor,
                claim_check_id,
                frozenset(body.photos),
                body.client_message,
                clock(request),
            )
        return _staff(view)

    return [client_router, staff_router]
