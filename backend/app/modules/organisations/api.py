"""Routes for company and group accounts: the client's application, and the staff check.

* An approved personal client applies for a company or group, adds the people who sign for it
  and its documents, and hands it in. The people they add join or decline for themselves.
* Staff with `organisations.review` check a handed-in application, and a different member of
  staff approves it, which gives the organisation its own account number. Either may send it
  back to the applicant to change, or reject it.

See docs/organisations.md.
"""

from datetime import datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.storage import FileStorage, InvalidFile, NewFile, StorageError
from app.infrastructure.throttling import reserve_attempts, throttle_key
from app.modules.identity.api import CurrentClient, require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.kyc.service import identities
from app.modules.organisations import service
from app.modules.organisations.domain.applications import (
    DOCUMENTS,
    MAX_DOCUMENT_BYTES,
    MAX_NAME,
    MAX_NOTE,
    MAX_ROLE,
    MAX_SIGNATORIES,
    DocumentKind,
    Kind,
    Problem,
    SignatoryStatus,
    Status,
    document_matches_type,
)
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ApplicationError, ErrorEnvelope, FieldProblem
from app.shared.push_ids import push_id

logger = structlog.get_logger()

VIEW_LINK_LIFETIME = timedelta(minutes=5)
LOOKUPS_PER_WINDOW = 20
_REFUSALS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 422, 429)
}

Reviewer = Annotated[StaffSession, Depends(require_permission("organisations.review"))]
# Approving opens an account, so it needs a passkey or app code from just now.
Approver = Annotated[StaffSession, Depends(require_confirmed("organisations.review"))]
Note = Annotated[str, Field(min_length=1, max_length=MAX_NOTE)]


class SignatoryOut(Contract):
    client_id: PushId
    role: str
    # As other clients see them, such as "Brian K.".
    name: str
    account_number: str
    is_applicant: bool
    status: SignatoryStatus
    invited_at: datetime
    answered_at: datetime | None


class DocumentOut(Contract):
    kind: DocumentKind
    content_type: str
    byte_size: int
    uploaded_at: datetime


class OrganisationOut(Contract):
    id: PushId
    kind: Kind
    name: str
    registration_number: str | None
    approvals_needed: int
    status: Status
    created_at: datetime
    submitted_at: datetime | None
    # What staff asked the applicant to change, when they sent it back.
    return_note: str | None
    returned_at: datetime | None
    decided_at: datetime | None
    # The organisation's own account number, once it is approved.
    account_number: str | None
    # The documents this kind needs, in order, and those handed in.
    documents_needed: list[DocumentKind]
    documents: list[DocumentOut]
    # Every reason it cannot be handed in yet. Empty when it can, or once it is handed in.
    problems: list[Problem]


class ClientOrganisationOut(OrganisationOut):
    signatories: list[SignatoryOut]
    # True for the person who applied: only they change it.
    is_applicant: bool
    # True when the reader is asked to sign and has not answered yet.
    invited: bool


class ClientOrganisationList(Contract):
    items: list[ClientOrganisationOut]


class ApplyBody(Contract):
    kind: Annotated[Kind, Field(strict=False)]
    name: Annotated[str, Field(min_length=1, max_length=MAX_NAME)]
    # Needed for a company, from its certificate. A group may have none.
    registration_number: Annotated[str | None, Field(max_length=40)] = None
    # How many signatories must approve money leaving the organisation, once it can invest.
    approvals_needed: Annotated[int, Field(ge=1, le=MAX_SIGNATORIES)]
    # What the applicant is in it, such as Director or Chairperson.
    role: Annotated[str, Field(min_length=1, max_length=MAX_ROLE)]


class ChangeBody(Contract):
    name: Annotated[str | None, Field(min_length=1, max_length=MAX_NAME)] = None
    registration_number: Annotated[str | None, Field(max_length=40)] = None
    approvals_needed: Annotated[int | None, Field(ge=1, le=MAX_SIGNATORIES)] = None


class InviteBody(Contract):
    # A Finvest account number or a mobile number.
    to: Annotated[str, Field(min_length=6, max_length=24)]
    role: Annotated[str, Field(min_length=1, max_length=MAX_ROLE)]


class AnswerBody(Contract):
    join: bool


class StaffSignatoryOut(SignatoryOut):
    # The full name on the approved identity document, or None if it is no longer approved.
    verified_name: str | None


class StaffOrganisationOut(OrganisationOut):
    applicant_id: PushId
    signatories: list[StaffSignatoryOut]
    checked_by: PushId | None
    checked_by_name: str | None
    checked_at: datetime | None
    check_note: str | None
    decided_by: PushId | None
    decided_by_name: str | None
    decision_note: str | None
    # What this member of staff may do with it now.
    can_check: bool
    can_approve: bool
    can_send_back: bool
    can_reject: bool


class StaffOrganisationList(Contract):
    items: list[StaffOrganisationOut]


class NoteBody(Contract):
    note: Note


class DocumentLink(Contract):
    kind: DocumentKind
    content_type: str
    url: str


class DocumentLinks(Contract):
    documents: list[DocumentLink]
    expires_at: datetime


def _base(found: service.Organisation) -> dict[str, object]:
    return {
        "id": found.id,
        "kind": found.kind,
        "name": found.name,
        "registration_number": found.registration_number,
        "approvals_needed": found.approvals_needed,
        "status": found.status,
        "created_at": found.created_at,
        "submitted_at": found.submitted_at,
        "return_note": found.return_note if found.status is Status.DRAFT else None,
        "returned_at": found.returned_at if found.status is Status.DRAFT else None,
        "decided_at": found.decided_at,
        "account_number": found.account_number,
        "documents_needed": list(DOCUMENTS[found.kind]),
        "documents": [
            DocumentOut(
                kind=d.kind,
                content_type=d.content_type,
                byte_size=d.byte_size,
                uploaded_at=d.uploaded_at,
            )
            for d in found.documents
        ],
        "problems": found.problems if found.status is Status.DRAFT else [],
    }


def _signatory(s: service.Signatory) -> dict[str, object]:
    return {
        "client_id": s.client_id,
        "role": s.role,
        "name": s.shown_name,
        "account_number": s.account_number,
        "is_applicant": s.is_applicant,
        "status": s.status,
        "invited_at": s.invited_at,
        "answered_at": s.answered_at,
    }


def _for_client(found: service.Organisation, client_id: str) -> ClientOrganisationOut:
    mine = next((s for s in found.signatories if s.client_id == client_id), None)
    # Someone who declined or was removed is no longer shown to the others.
    shown = (
        found.current
        if found.status is not Status.DRAFT or found.applicant_id != client_id
        else found.signatories
    )
    return ClientOrganisationOut(
        **_base(found),  # type: ignore[arg-type]
        signatories=[SignatoryOut(**_signatory(s)) for s in shown],  # type: ignore[arg-type]
        is_applicant=found.applicant_id == client_id,
        invited=mine is not None and mine.status is SignatoryStatus.INVITED,
    )


def create_routers(storage: FileStorage) -> list[APIRouter]:
    client_router = APIRouter(
        prefix="/client/organisations", tags=["organisations"], responses=_REFUSALS
    )
    staff_router = APIRouter(
        prefix="/staff/organisations", tags=["organisations"], responses=_REFUSALS
    )

    def database(request: Request) -> Database:
        found: Database = request.app.state.database
        return found

    def clock(request: Request) -> Clock:
        found: Clock = request.app.state.clock
        return found

    # The client --------------------------------------------------------------------------

    @client_router.get("")
    async def my_organisations(client: CurrentClient, request: Request) -> ClientOrganisationList:
        """Every company or group the client applied for or is asked to sign for."""
        async with database(request).actor_transaction(client.actor) as session:
            found = await service.mine(session, client.actor)
        return ClientOrganisationList(items=[_for_client(o, client.client_id) for o in found])

    @client_router.post("", status_code=201)
    async def apply(
        body: ApplyBody, client: CurrentClient, request: Request
    ) -> ClientOrganisationOut:
        """Start an application as a draft, with the client as its first signatory.

        409 `not_verified` until the client's own identity check is approved. 422 on
        `registration_number` with `not_a_registration_number` or `already_applied`.
        """
        async with database(request).actor_transaction(client.actor) as session:
            found = await service.apply(
                session,
                client.actor,
                kind=body.kind,
                name=body.name,
                registration_number=body.registration_number,
                approvals_needed=body.approvals_needed,
                role=body.role,
                clock=clock(request),
            )
        return _for_client(found, client.client_id)

    @client_router.get("/{organisation_id}")
    async def my_organisation(
        organisation_id: PushId, client: CurrentClient, request: Request
    ) -> ClientOrganisationOut:
        async with database(request).actor_transaction(client.actor) as session:
            found = await service.for_client(session, client.actor, organisation_id)
        return _for_client(found, client.client_id)

    @client_router.patch("/{organisation_id}")
    async def change(
        organisation_id: PushId, body: ChangeBody, client: CurrentClient, request: Request
    ) -> ClientOrganisationOut:
        """Change a draft's details. Only the applicant, only while it is a draft."""
        async with database(request).actor_transaction(client.actor) as session:
            found = await service.change(
                session,
                client.actor,
                organisation_id,
                name=body.name,
                registration_number=body.registration_number,
                approvals_needed=body.approvals_needed,
                clock=clock(request),
            )
        return _for_client(found, client.client_id)

    @client_router.post("/{organisation_id}/signatories", status_code=201)
    async def invite(
        organisation_id: PushId, body: InviteBody, client: CurrentClient, request: Request
    ) -> ClientOrganisationOut:
        """Ask another approved client to sign for it, by account number or mobile number.

        422 on `to` with `no_such_client`, `already_a_signatory` or `too_many_signatories`.
        429 after 20 look-ups in 15 minutes, counted with look-ups to send an investment.
        """
        await reserve_attempts(
            database(request),
            clock(request),
            [(throttle_key("transfer_recipient", client.client_id), LOOKUPS_PER_WINDOW)],
        )
        async with database(request).actor_transaction(client.actor) as session:
            found = await service.invite(
                session, client.actor, organisation_id, body.to, body.role, clock(request)
            )
        return _for_client(found, client.client_id)

    @client_router.delete("/{organisation_id}/signatories/{client_id}")
    async def remove(
        organisation_id: PushId, client_id: PushId, client: CurrentClient, request: Request
    ) -> ClientOrganisationOut:
        """Take someone off a draft."""
        async with database(request).actor_transaction(client.actor) as session:
            found = await service.remove(
                session, client.actor, organisation_id, client_id, clock(request)
            )
        return _for_client(found, client.client_id)

    @client_router.post("/{organisation_id}/answer")
    async def answer(
        organisation_id: PushId, body: AnswerBody, client: CurrentClient, request: Request
    ) -> ClientOrganisationOut:
        """Join or decline, when asked to sign for it."""
        async with database(request).actor_transaction(client.actor) as session:
            found = await service.answer(
                session, client.actor, organisation_id, body.join, clock(request)
            )
        return _for_client(found, client.client_id)

    @client_router.put("/{organisation_id}/documents/{kind}")
    async def add_document(
        organisation_id: PushId,
        kind: DocumentKind,
        document: Annotated[UploadFile, File(description="PDF, JPEG or PNG, at most 10 MiB")],
        client: CurrentClient,
        request: Request,
    ) -> ClientOrganisationOut:
        """Send one document as `multipart/form-data`. It replaces the one of the same kind."""
        data = await document.read(MAX_DOCUMENT_BYTES + 1)
        content_type = document.content_type or ""
        async with database(request).actor_transaction(client.actor) as session:
            await service.document_slot(
                session,
                client.actor,
                organisation_id,
                kind,
                content_type=content_type,
                byte_size=len(data),
            )
        if not document_matches_type(content_type, data):
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["document"], code="not_a_file_of_that_type")],
            )
        file_id = push_id()
        key = service.document_key(organisation_id, file_id)
        try:
            stored = await storage.put(NewFile(key, data, content_type))
        except InvalidFile:
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["document"], code="unsupported_content_type")],
            ) from None
        except StorageError:
            raise ApplicationError("unavailable") from None
        try:
            async with database(request).actor_transaction(client.actor) as session:
                replaced = await service.add_document(
                    session,
                    client.actor,
                    organisation_id,
                    kind,
                    file_id=file_id,
                    content_type=stored.content_type,
                    byte_size=stored.byte_size,
                    sha256=stored.sha256,
                    clock=clock(request),
                )
                found = await service.get(session, organisation_id)
        except Exception:
            await _discard(storage, key, file_id)
            raise
        if replaced:
            await _discard(storage, replaced, file_id)
        return _for_client(found, client.client_id)

    @client_router.post("/{organisation_id}/submission")
    async def submit(
        organisation_id: PushId, client: CurrentClient, request: Request
    ) -> ClientOrganisationOut:
        """Hand it in for staff to check. 409 lists each reason it is not ready as `problems`."""
        async with database(request).actor_transaction(client.actor) as session:
            found = await service.submit(session, client.actor, organisation_id, clock(request))
        return _for_client(found, client.client_id)

    # Staff -------------------------------------------------------------------------------

    async def _staff_view(
        session: AsyncSession, staff: StaffSession, found: service.Organisation
    ) -> StaffOrganisationOut:
        names = await identities(session, [s.client_id for s in found.signatories])
        may = "organisations.review" in staff.permissions
        return StaffOrganisationOut(
            **_base(found),  # type: ignore[arg-type]
            applicant_id=found.applicant_id,
            signatories=[
                StaffSignatoryOut(
                    **_signatory(s),  # type: ignore[arg-type]
                    verified_name=names[s.client_id].verified_name
                    if s.client_id in names
                    else None,
                )
                for s in found.current
            ],
            checked_by=found.checked_by,
            checked_by_name=found.checked_by_name,
            checked_at=found.checked_at,
            check_note=found.check_note,
            decided_by=found.decided_by,
            decided_by_name=found.decided_by_name,
            decision_note=found.decision_note,
            can_check=may and found.status is Status.SUBMITTED,
            can_approve=may
            and found.status is Status.CHECKED
            and found.checked_by != staff.staff_id,
            can_send_back=may and found.is_waiting,
            can_reject=may and found.is_waiting,
        )

    @staff_router.get("")
    async def waiting(
        staff: Reviewer,
        request: Request,
        status: Annotated[Status | None, Query()] = None,
    ) -> StaffOrganisationList:
        """Every application handed in and not yet decided, oldest first."""
        async with database(request).actor_transaction(staff.actor) as session:
            found = await service.waiting(session)
            return StaffOrganisationList(
                items=[
                    await _staff_view(session, staff, o)
                    for o in found
                    if status is None or o.status is status
                ]
            )

    @staff_router.get("/{organisation_id}")
    async def one(
        organisation_id: PushId, staff: Reviewer, request: Request
    ) -> StaffOrganisationOut:
        async with database(request).actor_transaction(staff.actor) as session:
            return await _staff_view(session, staff, await service.get(session, organisation_id))

    @staff_router.post("/{organisation_id}/document-links")
    async def document_links(
        organisation_id: PushId, staff: Reviewer, request: Request
    ) -> DocumentLinks:
        """Short-lived links to the documents. Each call is written to the audit trail."""
        now = clock(request).now()
        async with database(request).actor_transaction(staff.actor) as session:
            documents = await service.documents_for_viewing(
                session, staff.actor, organisation_id, clock(request)
            )
        return DocumentLinks(
            documents=[
                DocumentLink(
                    kind=d.kind,
                    content_type=d.content_type,
                    url=storage.read_link(
                        d.storage_key,
                        content_type=d.content_type,
                        lifetime=VIEW_LINK_LIFETIME,
                        now=now,
                    ).url,
                )
                for d in documents
            ],
            expires_at=now + VIEW_LINK_LIFETIME,
        )

    @staff_router.post("/{organisation_id}/check")
    async def check(
        organisation_id: PushId, body: NoteBody, staff: Reviewer, request: Request
    ) -> StaffOrganisationOut:
        """Check the documents and the people, and pass it to a second member of staff."""
        async with database(request).actor_transaction(staff.actor) as session:
            found = await service.check(
                session, staff.actor, organisation_id, body.note, clock(request)
            )
            return await _staff_view(session, staff, found)

    @staff_router.post("/{organisation_id}/approval")
    async def approve(
        organisation_id: PushId, body: NoteBody, staff: Approver, request: Request
    ) -> StaffOrganisationOut:
        """Open the account of someone else's checked application. It cannot be undone."""
        async with database(request).actor_transaction(staff.actor) as session:
            found = await service.approve(
                session, staff.actor, organisation_id, body.note, clock(request)
            )
            return await _staff_view(session, staff, found)

    @staff_router.post("/{organisation_id}/return")
    async def send_back(
        organisation_id: PushId, body: NoteBody, staff: Reviewer, request: Request
    ) -> StaffOrganisationOut:
        """Send it back to the applicant to change. They see the note."""
        async with database(request).actor_transaction(staff.actor) as session:
            found = await service.send_back(
                session, staff.actor, organisation_id, body.note, clock(request)
            )
            return await _staff_view(session, staff, found)

    @staff_router.post("/{organisation_id}/rejection")
    async def reject(
        organisation_id: PushId, body: NoteBody, staff: Approver, request: Request
    ) -> StaffOrganisationOut:
        """Turn it down for good. The applicant is told and may apply again."""
        async with database(request).actor_transaction(staff.actor) as session:
            found = await service.reject(
                session, staff.actor, organisation_id, body.note, clock(request)
            )
            return await _staff_view(session, staff, found)

    return [client_router, staff_router]


async def _discard(storage: FileStorage, key: str, file_id: str) -> None:
    """Delete a stored document that no record points to. Best effort."""
    try:
        await storage.delete(key)
    except StorageError:
        logger.warning("organisation_document_orphaned", file_id=file_id)
