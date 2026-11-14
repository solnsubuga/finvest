"""Identity check routes: the client's own check, and the review queue for staff.

A client sends each photo to the API, which stores it privately before saving the record, so
a refused photo leaves no file behind. Staff are given short-lived links to look at photos.
Staff see a document number masked, and looking at the whole number, or at the photos, is
written to the audit trail.
"""

from datetime import date, datetime, timedelta
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import Field

from app.infrastructure.database import Database
from app.infrastructure.sealing import Sealer
from app.infrastructure.storage import FileStorage, InvalidFile, NewFile, StorageError
from app.modules.identity.api import CurrentClient, require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.kyc import liveness, service
from app.modules.kyc.domain.checks import (
    CheckStatus,
    DocumentType,
    IdentityDetails,
    PhotoKind,
    mask_document_number,
    photo_matches_type,
    staff_moves,
)
from app.modules.kyc.domain.liveness import LivenessProvider, LivenessStatus
from app.modules.kyc.domain.provider import AutomaticCheck, IdentityProvider, Verdict
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.push_ids import push_id

logger = structlog.get_logger()
VIEW_LINK_LIFETIME = timedelta(minutes=5)

Decision = Literal["approve", "reject", "ask_for_photos", "escalate"]
_DECISIONS: dict[CheckStatus, Decision] = {
    CheckStatus.APPROVED: "approve",
    CheckStatus.REJECTED: "reject",
    CheckStatus.AWAITING_CLIENT: "ask_for_photos",
    CheckStatus.WITH_COMPLIANCE: "escalate",
}
_REFUSALS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 503)
}


# Bodies are strict, and JSON has no dates or enumerations, so these read them from strings.
IsoDate = Annotated[date, Field(strict=False)]
Document = Annotated[DocumentType, Field(strict=False)]
Photo = Annotated[PhotoKind, Field(strict=False)]


class DetailsBody(Contract):
    surname: str = Field(max_length=100)
    given_names: str = Field(max_length=100)
    document_number: str = Field(max_length=40)
    date_of_birth: IsoDate
    document_expires_on: IsoDate
    tin: str | None = Field(default=None, max_length=20)


class ClientDetails(Contract):
    """The details as the client confirmed them, or as Finvest 1.0 held them."""

    surname: str
    given_names: str
    document_number: str
    date_of_birth: IsoDate
    # Unknown only on a check carried over from Finvest 1.0.
    document_expires_on: IsoDate | None
    tin: str | None


class DocumentBody(Contract):
    document_type: Document
    document_country: str = Field(min_length=2, max_length=2)


class ClientPhoto(Contract):
    kind: PhotoKind
    content_type: str
    byte_size: int
    # True for a selfie captured by a liveness check that saw a live person.
    live: bool = False


class ClientCheck(Contract):
    """A client's own check. Nothing about providers, flags or who decided."""

    id: PushId
    status: CheckStatus
    document_type: DocumentType
    document_country: str
    details: ClientDetails | None
    details_confirmed: bool
    photos: list[ClientPhoto]
    # What is still needed before it can be handed in: photo kinds and "details".
    missing: list[str]
    retake_photos: list[PhotoKind]
    # Of the photos asked for again, those the client has already taken again.
    retaken: list[PhotoKind]
    client_message: str | None
    submitted_at: datetime | None
    account_number: str | None


class ClientCheckEnvelope(Contract):
    check: ClientCheck | None


class QueueEntry(Contract):
    id: PushId
    name: str | None
    document_type: DocumentType
    status: CheckStatus
    submitted_at: datetime | None
    review_flags: list[str]


class Queue(Contract):
    items: list[QueueEntry]


class Outcome(Contract):
    check: AutomaticCheck
    verdict: Verdict
    score: int | None


class StaffPhoto(Contract):
    id: PushId
    kind: PhotoKind
    # True for a selfie captured by a liveness check that saw a live person.
    live: bool = False


class LivenessAttempt(Contract):
    """One liveness attempt. Open the `url` page while `status` is `open`."""

    id: PushId
    status: LivenessStatus
    # The provider's page for this attempt. Only while it is open; it is the client's alone.
    url: str | None
    # Why it failed or did not start, as a stable code.
    failure_code: str | None
    # True when the face it captured became the check's selfie.
    photo_used: bool
    expires_at: datetime


class LivenessState(Contract):
    """Whether the selfie step can use a liveness check, and the latest attempt."""

    # False when liveness is not switched on: a camera selfie is the only way then.
    available: bool
    # Why a new attempt cannot start now, such as `too_many_attempts`, or null.
    problem: str | None
    attempts_left: int
    latest: LivenessAttempt | None


class StaffCheck(QueueEntry):
    client_id: PushId
    document_country: str
    surname: str | None
    given_names: str | None
    document_number_masked: str | None
    date_of_birth: date | None
    document_expires_on: date | None
    photos: list[StaffPhoto]
    automatic_checks: list[Outcome]
    retake_photos: list[PhotoKind]
    client_message: str | None
    account_number: str | None
    decided_at: datetime | None
    # True when the check was carried over from Finvest 1.0: approved there, with no photos here.
    carried_over: bool
    # What staff may do to the check in this state. The database enforces the same table.
    allowed: list[Decision]


class DocumentNumber(Contract):
    document_number: str


class PhotoLink(Contract):
    id: PushId
    kind: PhotoKind
    url: str


class PhotoLinks(Contract):
    photos: list[PhotoLink]
    expires_at: datetime


class ReasonBody(Contract):
    reason: str = Field(max_length=500)


class RejectBody(ReasonBody):
    client_message: str | None = Field(default=None, max_length=300)


class AskForPhotosBody(Contract):
    photos: list[Photo] = Field(max_length=3)
    client_message: str = Field(max_length=300)


def _client_check(view: service.CheckView) -> ClientCheck:
    details = view.details
    return ClientCheck(
        id=view.id,
        status=view.status,
        document_type=view.document_type,
        document_country=view.document_country,
        details=None
        if details is None
        else ClientDetails(
            surname=details.surname,
            given_names=details.given_names,
            document_number=details.document_number,
            date_of_birth=details.date_of_birth,
            document_expires_on=details.document_expires_on,
            tin=details.tin,
        ),
        details_confirmed=view.details_confirmed,
        photos=[
            ClientPhoto(
                kind=p.kind, content_type=p.content_type, byte_size=p.byte_size, live=p.live
            )
            for p in view.photos
        ],
        missing=view.missing,
        retake_photos=sorted(view.retake_photos),
        retaken=sorted(view.retaken),
        client_message=view.client_message,
        submitted_at=view.submitted_at,
        account_number=view.account_number,
    )


def _name(surname: str | None, given_names: str | None) -> str | None:
    return f"{given_names} {surname.title()}" if surname and given_names else None


def _staff_check(view: service.CheckView) -> StaffCheck:
    details = view.details
    return StaffCheck(
        id=view.id,
        name=_name(details.surname, details.given_names) if details else None,
        document_type=view.document_type,
        status=view.status,
        submitted_at=view.submitted_at,
        review_flags=list(view.review_flags),
        client_id=view.client_id,
        document_country=view.document_country,
        surname=details.surname if details else None,
        given_names=details.given_names if details else None,
        document_number_masked=mask_document_number(details.document_number) if details else None,
        date_of_birth=details.date_of_birth if details else None,
        document_expires_on=details.document_expires_on if details else None,
        photos=[StaffPhoto(id=p.id, kind=p.kind, live=p.live) for p in view.photos],
        automatic_checks=[
            Outcome(check=o.check, verdict=o.verdict, score=o.score) for o in view.automatic_checks
        ],
        retake_photos=sorted(view.retake_photos),
        client_message=view.client_message,
        account_number=view.account_number,
        decided_at=view.decided_at,
        carried_over=view.source == "finvest1",
        allowed=[
            decision
            for status, decision in _DECISIONS.items()
            if status in staff_moves(view.status)
        ],
    )


def _attempt(view: liveness.LivenessView) -> LivenessAttempt:
    return LivenessAttempt(
        id=view.id,
        status=view.status,
        url=view.url,
        failure_code=view.failure_code,
        photo_used=view.photo_used,
        expires_at=view.expires_at,
    )


def create_routers(
    sealer: Sealer | None,
    provider: IdentityProvider,
    storage: FileStorage,
    liveness_provider: LivenessProvider | None = None,
) -> list[APIRouter]:
    """The routes for this process. Without the data key they answer `unavailable`."""
    client_router = APIRouter(
        prefix="/client/identity-check", tags=["identity checks"], responses=_REFUSALS
    )
    staff_router = APIRouter(
        prefix="/staff/identity-checks", tags=["identity checks"], responses=_REFUSALS
    )
    Viewer = Annotated[StaffSession, Depends(require_permission("identity_checks.view"))]
    Reviewer = Annotated[StaffSession, Depends(require_permission("identity_checks.review"))]
    # Deciding needs a passkey or app code from the last few minutes.
    Decider = Annotated[StaffSession, Depends(require_confirmed("identity_checks.review"))]

    def seal() -> Sealer:
        if sealer is None:
            raise ApplicationError("unavailable")
        return sealer

    def database(request: Request) -> Database:
        held: Database = request.app.state.database
        return held

    def clock(request: Request) -> Clock:
        held: Clock = request.app.state.clock
        return held

    # The client's own check ------------------------------------------------------------------

    @client_router.get("")
    async def my_check(client: CurrentClient, request: Request) -> ClientCheckEnvelope:
        """The signed-in client's check, or `null` before they have chosen a document."""
        async with database(request).actor_transaction(client.actor) as session:
            view = await service.check_for_client(session, seal(), client.actor)
        return ClientCheckEnvelope(check=None if view is None else _client_check(view))

    @client_router.put("/document")
    async def choose_document(
        body: DocumentBody, client: CurrentClient, request: Request
    ) -> ClientCheck:
        """Start the check, or change the document while it is still a draft."""
        async with database(request).actor_transaction(client.actor) as session:
            view = await service.choose_document(
                session,
                seal(),
                client.actor,
                body.document_type,
                body.document_country,
                clock(request),
            )
        return _client_check(view)

    @client_router.put("/photos/{kind}")
    async def add_photo(
        kind: PhotoKind,
        photo: Annotated[UploadFile, File(description="JPEG, PNG or WebP, at most 10 MiB")],
        client: CurrentClient,
        request: Request,
    ) -> ClientCheck:
        """Send one photo as `multipart/form-data`. It replaces the one of the same kind."""
        sealer_now = seal()
        data = await photo.read(service.MAX_PHOTO_BYTES + 1)
        content_type = photo.content_type or ""
        # Everything that can refuse the photo runs before the file is stored.
        async with database(request).actor_transaction(client.actor) as session:
            check_id = await service.photo_slot(
                session, client.actor, kind, content_type=content_type, byte_size=len(data)
            )
        if not photo_matches_type(content_type, data):
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["photo"], code="not_an_image_of_that_type")],
            )
        photo_id = push_id()
        key = service.photo_key(check_id, photo_id)
        try:
            stored = await storage.put(NewFile(key, data, content_type))
        except InvalidFile:
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["photo"], code="unsupported_content_type")],
            ) from None
        except StorageError:
            # Nothing was saved, so the client can send the same photo again.
            raise ApplicationError("unavailable") from None
        try:
            async with database(request).actor_transaction(client.actor) as session:
                await service.add_photo(
                    session,
                    client.actor,
                    kind,
                    content_type=stored.content_type,
                    byte_size=stored.byte_size,
                    sha256=stored.sha256,
                    clock=clock(request),
                    photo_id=photo_id,
                )
                view = await service.check_for_client(session, sealer_now, client.actor)
        except Exception:
            # The check changed in between, for example it was handed in. Keep no orphan file.
            await _discard(storage, key, photo_id)
            raise
        if view is None:
            raise ApplicationError("not_found")
        return _client_check(view)

    @client_router.get("/liveness")
    async def my_liveness(client: CurrentClient, request: Request) -> LivenessState:
        """Whether the selfie can be taken with a liveness check, and how the latest one stands.

        Read it again every few seconds while the latest attempt is `starting` or `open`.
        """
        async with database(request).actor_transaction(client.actor) as session:
            state = await liveness.availability(
                session,
                seal(),
                client.actor,
                clock(request),
                provider_on=liveness_provider is not None,
            )
        return LivenessState(
            available=state.available,
            problem=state.problem,
            attempts_left=state.attempts_left,
            latest=None if state.latest is None else _attempt(state.latest),
        )

    @client_router.post("/liveness")
    async def start_liveness(client: CurrentClient, request: Request) -> LivenessAttempt:
        """Start a liveness attempt for the selfie. Any attempt still waiting is retired.

        The attempt starts as `starting`. Read `GET /liveness` until it is `open`, then open its
        page. When it passes, the face it captured is the check's selfie.
        """
        if liveness_provider is None:
            raise ApplicationError("unavailable")
        async with database(request).actor_transaction(client.actor) as session:
            seal()  # refuse before saving anything when the data key is missing
            view = await liveness.start(
                session, client.actor, liveness_provider.name, clock(request)
            )
        return _attempt(view)

    @client_router.put("/details")
    async def confirm_details(
        body: DetailsBody, client: CurrentClient, request: Request
    ) -> ClientCheck:
        """Save the details the client read from their document and confirmed."""
        details = IdentityDetails(
            surname=body.surname,
            given_names=body.given_names,
            document_number=body.document_number,
            date_of_birth=body.date_of_birth,
            document_expires_on=body.document_expires_on,
            tin=body.tin,
        )
        async with database(request).actor_transaction(client.actor) as session:
            view = await service.confirm_details(
                session, seal(), client.actor, details, clock(request)
            )
        return _client_check(view)

    @client_router.post("/submit")
    async def submit(client: CurrentClient, request: Request) -> ClientCheck:
        """Hand the check in. Answers which photos or details are missing if it is not ready."""
        async with database(request).actor_transaction(client.actor) as session:
            view = await service.submit(session, seal(), client.actor, provider, clock(request))
        return _client_check(view)

    # Staff -----------------------------------------------------------------------------------

    @staff_router.get("")
    async def queue(staff: Viewer, request: Request) -> Queue:
        """Checks that are not decided, the ones needing a person first."""
        async with database(request).actor_transaction(staff.actor) as session:
            items = await service.review_queue(session)
        return Queue(
            items=[
                QueueEntry(
                    id=item.id,
                    name=_name(item.surname, item.given_names),
                    document_type=item.document_type,
                    status=item.status,
                    submitted_at=item.submitted_at,
                    review_flags=list(item.review_flags),
                )
                for item in items
            ]
        )

    @staff_router.get("/{check_id}")
    async def one_check(check_id: PushId, staff: Viewer, request: Request) -> StaffCheck:
        async with database(request).actor_transaction(staff.actor) as session:
            return _staff_check(await service.check_for_staff(session, seal(), check_id))

    @staff_router.post("/{check_id}/document-number")
    async def show_document_number(
        check_id: PushId, staff: Viewer, request: Request
    ) -> DocumentNumber:
        """The whole document number. Each call is written to the audit trail."""
        async with database(request).actor_transaction(staff.actor) as session:
            number = await service.show_document_number(
                session, seal(), staff.actor, check_id, clock(request)
            )
        return DocumentNumber(document_number=number)

    @staff_router.post("/{check_id}/photo-links")
    async def photo_links(check_id: PushId, staff: Viewer, request: Request) -> PhotoLinks:
        """Short-lived links to the check's photos. Each call is written to the audit trail."""
        now = clock(request).now()
        async with database(request).actor_transaction(staff.actor) as session:
            photos = await service.photos_for_viewing(
                session, seal(), staff.actor, check_id, clock(request)
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

    @staff_router.post("/{check_id}/approve")
    async def approve(
        check_id: PushId, body: ReasonBody, staff: Decider, request: Request
    ) -> StaffCheck:
        """Approve the client and issue their account number. It cannot be undone."""
        async with database(request).actor_transaction(staff.actor) as session:
            view = await service.approve(
                session, seal(), staff.actor, check_id, body.reason, clock(request)
            )
        return _staff_check(view)

    @staff_router.post("/{check_id}/reject")
    async def reject(
        check_id: PushId, body: RejectBody, staff: Decider, request: Request
    ) -> StaffCheck:
        """Reject the client. It cannot be undone."""
        async with database(request).actor_transaction(staff.actor) as session:
            view = await service.reject(
                session,
                seal(),
                staff.actor,
                check_id,
                body.reason,
                clock(request),
                client_message=body.client_message,
            )
        return _staff_check(view)

    @staff_router.post("/{check_id}/ask-for-photos")
    async def ask_for_photos(
        check_id: PushId, body: AskForPhotosBody, staff: Reviewer, request: Request
    ) -> StaffCheck:
        """Ask the client to take the named photos again, with a message."""
        async with database(request).actor_transaction(staff.actor) as session:
            view = await service.ask_for_photos(
                session,
                seal(),
                staff.actor,
                check_id,
                frozenset(body.photos),
                body.client_message,
                clock(request),
            )
        return _staff_check(view)

    @staff_router.post("/{check_id}/escalate")
    async def escalate(
        check_id: PushId, body: ReasonBody, staff: Reviewer, request: Request
    ) -> StaffCheck:
        """Pass the check to compliance."""
        async with database(request).actor_transaction(staff.actor) as session:
            view = await service.escalate(
                session, seal(), staff.actor, check_id, body.reason, clock(request)
            )
        return _staff_check(view)

    return [client_router, staff_router]


async def _discard(storage: FileStorage, key: str, photo_id: str) -> None:
    """Delete a stored photo that no record points to. Best effort: if the store cannot be
    reached, the file stays under a key nothing points to, and the log names the photo id."""
    try:
        await storage.delete(key)
    except StorageError:
        logger.warning("identity_photo_orphaned", photo_id=photo_id)
