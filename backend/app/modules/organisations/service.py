"""Applying for a company or group account, and the two-person check that opens it.

Every function works inside the caller's transaction, and each change, its audit event and any
notification commit together or not at all. PostgreSQL holds the same rules (migration 0242),
so a bug here is refused there. See docs/organisations.md.

Opening an organisation moves no money and it cannot invest yet.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.modules.account_numbers.domain.numbers import AccountType
from app.modules.account_numbers.service import issue_account_number
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.identity.client_locks import client_id_for_phone, client_status
from app.modules.investments.domain.transfers import display_name
from app.modules.kyc import service as kyc
from app.modules.notifications import contract as notifications
from app.modules.organisations.domain.applications import (
    DOCUMENT_TYPES,
    DOCUMENTS,
    MAX_DOCUMENT_BYTES,
    MAX_NOTE,
    MAX_SIGNATORIES,
    DocumentKind,
    Kind,
    Problem,
    Readiness,
    SignatoryStatus,
    Status,
    clean_name,
    clean_registration,
    clean_role,
    problems,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

RESOURCE = "organisation"
TELL_INVITED_KIND = "organisations.tell_invited"
_ACCOUNT_NUMBER = re.compile(r"[1-9]\d{9}")
_ACCOUNT_TYPES = {Kind.COMPANY: AccountType.COMPANY, Kind.GROUP: AccountType.GROUP}


@dataclass(frozen=True, slots=True)
class Signatory:
    client_id: str
    role: str
    # As other clients see them: "Brian K.".
    shown_name: str
    account_number: str
    is_applicant: bool
    status: SignatoryStatus
    invited_at: datetime
    answered_at: datetime | None


@dataclass(frozen=True, slots=True)
class Document:
    kind: DocumentKind
    file_id: str
    storage_key: str
    content_type: str
    byte_size: int
    sha256: str
    uploaded_at: datetime


@dataclass(frozen=True, slots=True)
class Organisation:
    id: str
    kind: Kind
    name: str
    registration_number: str | None
    applicant_id: str
    approvals_needed: int
    status: Status
    created_at: datetime
    submitted_at: datetime | None
    checked_by: str | None
    checked_by_name: str | None
    checked_at: datetime | None
    check_note: str | None
    returned_at: datetime | None
    return_note: str | None
    decided_by: str | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_note: str | None
    account_number: str | None
    signatories: tuple[Signatory, ...]
    documents: tuple[Document, ...]

    @property
    def current(self) -> tuple[Signatory, ...]:
        """The people still on it: invited or joined, applicant first."""
        return tuple(
            s
            for s in self.signatories
            if s.status in (SignatoryStatus.INVITED, SignatoryStatus.JOINED)
        )

    @property
    def problems(self) -> list[Problem]:
        joined = sum(1 for s in self.signatories if s.status is SignatoryStatus.JOINED)
        invited = sum(1 for s in self.signatories if s.status is SignatoryStatus.INVITED)
        return problems(
            Readiness(
                kind=self.kind,
                registration_number=self.registration_number,
                approvals_needed=self.approvals_needed,
                joined=joined,
                invited=invited,
                documents=frozenset(d.kind for d in self.documents),
            )
        )

    @property
    def is_waiting(self) -> bool:
        return self.status in (Status.SUBMITTED, Status.CHECKED)


_SELECT = (
    "SELECT o.id, o.kind, o.name, o.registration_number, o.applicant_id, o.approvals_needed, "
    "o.status, o.created_at, o.submitted_at, o.checked_by, c.name AS checked_by_name, "
    "o.checked_at, o.check_note, o.returned_at, o.return_note, o.decided_by, "
    "d.name AS decided_by_name, o.decided_at, o.decision_note, o.account_number "
    "FROM organisations o LEFT JOIN identity_staff c ON c.id = o.checked_by "
    "LEFT JOIN identity_staff d ON d.id = o.decided_by "
)
_BY_ID = text(_SELECT + "WHERE o.id = :id")
_MINE = text(
    _SELECT + "WHERE o.applicant_id = :client OR EXISTS (SELECT FROM organisation_signatories s "
    "WHERE s.organisation_id = o.id AND s.client_id = :client "
    "AND s.status IN ('invited', 'joined')) ORDER BY o.created_at DESC, o.id DESC"
)
_WAITING = text(
    _SELECT + "WHERE o.status IN ('submitted', 'checked') ORDER BY o.submitted_at, o.id"
)
_LOCK = text("SELECT status, applicant_id, kind FROM organisations WHERE id = :id FOR UPDATE")
_PEEK = text("SELECT applicant_id FROM organisations WHERE id = :id")
_SIGNATORIES = text(
    "SELECT client_id, role, shown_name, account_number, is_applicant, status, invited_at, "
    "answered_at FROM organisation_signatories WHERE organisation_id = :id "
    "ORDER BY is_applicant DESC, invited_at, client_id"
)
_DOCUMENTS = text(
    "SELECT kind, file_id, storage_key, content_type, byte_size, sha256, uploaded_at "
    "FROM organisation_documents WHERE organisation_id = :id ORDER BY kind"
)


# Reading ------------------------------------------------------------------------------------


async def mine(session: AsyncSession, client: Actor) -> list[Organisation]:
    """Every organisation the client applied for or is asked to sign for, newest first."""
    client_id = _person(client, "client")
    rows = (await session.execute(_MINE, {"client": client_id})).all()
    return [await _assemble(session, row) for row in rows]


async def for_client(session: AsyncSession, client: Actor, organisation_id: str) -> Organisation:
    """One the client belongs to. Anyone else's is `not_found`, as the row policies make it."""
    client_id = _person(client, "client")
    found = await get(session, organisation_id)
    if found.applicant_id != client_id and not any(s.client_id == client_id for s in found.current):
        raise ApplicationError("not_found")
    return found


async def waiting(session: AsyncSession) -> list[Organisation]:
    """Every application handed in and not yet decided, oldest first. For staff."""
    return [await _assemble(session, row) for row in (await session.execute(_WAITING)).all()]


async def get(session: AsyncSession, organisation_id: str) -> Organisation:
    row = (await session.execute(_BY_ID, {"id": organisation_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return await _assemble(session, row)


# The applicant ------------------------------------------------------------------------------


async def apply(
    session: AsyncSession,
    client: Actor,
    *,
    kind: Kind,
    name: str,
    registration_number: str | None,
    approvals_needed: int,
    role: str,
    clock: Clock,
) -> Organisation:
    """Start an application as a draft, with the client as its first signatory."""
    client_id = _person(client, "client")
    me = await _approved(session, client_id)
    if me is None:
        # Only a client whose own identity check is approved may apply.
        raise ApplicationError(
            "conflict", fields=[FieldProblem(field_path=["client"], code="not_verified")]
        )
    cleaned_name = _name(name)
    cleaned_registration = _registration(registration_number)
    cleaned_role = _role(role)
    _approvals(approvals_needed)
    organisation_id = push_id(clock)
    now = clock.now()
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO organisations (id, kind, name, registration_number, "
                    "applicant_id, approvals_needed, created_at) VALUES (:id, :kind, :name, "
                    ":registration, :client, :approvals, :now)"
                ),
                {
                    "id": organisation_id,
                    "kind": kind.value,
                    "name": cleaned_name,
                    "registration": cleaned_registration,
                    "client": client_id,
                    "approvals": approvals_needed,
                    "now": now,
                },
            )
    except IntegrityError:
        raise _invalid("registration_number", "already_applied") from None
    await session.execute(
        text(
            "INSERT INTO organisation_signatories (organisation_id, client_id, role, shown_name, "
            "account_number, is_applicant, status, invited_at, answered_at) VALUES (:id, "
            ":client, :role, :shown, :number, true, 'joined', :now, :now)"
        ),
        {
            "id": organisation_id,
            "client": client_id,
            "role": cleaned_role,
            "shown": display_name(me.surname, me.given_names),
            "number": me.account_number,
            "now": now,
        },
    )
    await _audit(session, client, "organisation.applied", organisation_id, clock, kind=kind.value)
    return await get(session, organisation_id)


async def change(
    session: AsyncSession,
    client: Actor,
    organisation_id: str,
    *,
    name: str | None,
    registration_number: str | None,
    approvals_needed: int | None,
    clock: Clock,
) -> Organisation:
    """Change the details of a draft. A field left out stays as it is."""
    await _draft_of(session, client, organisation_id)
    found = await get(session, organisation_id)
    new_name = found.name if name is None else _name(name)
    new_registration = (
        found.registration_number
        if registration_number is None
        else _registration(registration_number)
    )
    new_approvals = found.approvals_needed if approvals_needed is None else approvals_needed
    _approvals(new_approvals)
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "UPDATE organisations SET name = :name, registration_number = :registration, "
                    "approvals_needed = :approvals WHERE id = :id"
                ),
                {
                    "name": new_name,
                    "registration": new_registration,
                    "approvals": new_approvals,
                    "id": organisation_id,
                },
            )
    except IntegrityError:
        raise _invalid("registration_number", "already_applied") from None
    await _audit(session, client, "organisation.changed", organisation_id, clock)
    return await get(session, organisation_id)


async def invite(
    session: AsyncSession, client: Actor, organisation_id: str, to: str, role: str, clock: Clock
) -> Organisation:
    """Ask another approved client, by account number or mobile number, to sign for it.

    `not_found` on `to` when nobody approved has it. They are told in the app, and they join or
    decline for themselves.
    """
    client_id = await _draft_of(session, client, organisation_id)
    cleaned_role = _role(role)
    person = await _find(session, to)
    if person is None:
        raise _invalid("to", "no_such_client")
    if person.client_id == client_id:
        raise _invalid("to", "already_a_signatory")
    found = await get(session, organisation_id)
    existing = next((s for s in found.signatories if s.client_id == person.client_id), None)
    if existing is not None and existing.status in (
        SignatoryStatus.INVITED,
        SignatoryStatus.JOINED,
    ):
        raise _invalid("to", "already_a_signatory")
    if len(found.current) >= MAX_SIGNATORIES:
        raise _invalid("to", "too_many_signatories")
    now = clock.now()
    shown = display_name(person.surname, person.given_names)
    if existing is None:
        await session.execute(
            text(
                "INSERT INTO organisation_signatories (organisation_id, client_id, role, "
                "shown_name, account_number, invited_at) VALUES (:id, :client, :role, :shown, "
                ":number, :now)"
            ),
            {
                "id": organisation_id,
                "client": person.client_id,
                "role": cleaned_role,
                "shown": shown,
                "number": person.account_number,
                "now": now,
            },
        )
    else:
        await session.execute(
            text(
                "UPDATE organisation_signatories SET status = 'invited', role = :role, "
                "invited_at = :now, answered_at = NULL "
                "WHERE organisation_id = :id AND client_id = :client"
            ),
            {"role": cleaned_role, "now": now, "id": organisation_id, "client": person.client_id},
        )
    applicant = next(s for s in found.signatories if s.is_applicant)
    # The person is told by the worker: a client's own transaction writes only their own alerts.
    await enqueue(
        session,
        JobRequest(
            kind=TELL_INVITED_KIND,
            idempotency_key=f"{organisation_id}:{person.client_id}:{int(now.timestamp() * 1000)}",
            payload={
                "organisation_id": organisation_id,
                "client_id": person.client_id,
                # Never the organisation's name: the applicant wrote it and staff have not seen
                # it, and this goes out in Finvest's voice. The app shows it.
                "person": applicant.shown_name,
            },
        ),
        now=now,
    )
    await _audit(
        session,
        client,
        "organisation.signatory_invited",
        organisation_id,
        clock,
        signatory=person.client_id,
    )
    return await get(session, organisation_id)


async def remove(
    session: AsyncSession, client: Actor, organisation_id: str, signatory_id: str, clock: Clock
) -> Organisation:
    """Take someone off a draft. The applicant cannot take themselves off."""
    client_id = await _draft_of(session, client, organisation_id)
    if signatory_id == client_id:
        raise _invalid("client_id", "applicant_stays")
    updated = await session.execute(
        text(
            "UPDATE organisation_signatories SET status = 'removed' "
            "WHERE organisation_id = :id AND client_id = :client AND status <> 'removed'"
        ),
        {"id": organisation_id, "client": signatory_id},
    )
    if updated.rowcount != 1:  # type: ignore[attr-defined]
        raise ApplicationError("not_found")
    await _audit(
        session,
        client,
        "organisation.signatory_removed",
        organisation_id,
        clock,
        signatory=signatory_id,
    )
    return await get(session, organisation_id)


async def answer(
    session: AsyncSession, client: Actor, organisation_id: str, join: bool, clock: Clock
) -> Organisation:
    """The invited client joins, or declines. Only while the application is a draft."""
    client_id = _person(client, "client")
    found = await for_client(session, client, organisation_id)
    if found.status is not Status.DRAFT:
        raise _conflict("status", f"already_{found.status.value}")
    mine_ = next((s for s in found.signatories if s.client_id == client_id), None)
    if mine_ is None or mine_.status is not SignatoryStatus.INVITED:
        raise _conflict("signatory", "not_invited")
    if join and await _approved(session, client_id) is None:
        raise _conflict("client", "not_verified")
    await session.execute(
        text(
            "UPDATE organisation_signatories SET status = :status, answered_at = :now "
            "WHERE organisation_id = :id AND client_id = :client"
        ),
        {
            "status": "joined" if join else "declined",
            "now": clock.now(),
            "id": organisation_id,
            "client": client_id,
        },
    )
    action = "organisation.signatory_joined" if join else "organisation.signatory_declined"
    await _audit(session, client, action, organisation_id, clock)
    if not join:
        # They no longer see it.
        return found
    return await get(session, organisation_id)


async def document_slot(
    session: AsyncSession,
    client: Actor,
    organisation_id: str,
    kind: DocumentKind,
    *,
    content_type: str,
    byte_size: int,
) -> None:
    """Refuse a document before it is stored, so a refused file never reaches the bucket."""
    await _draft_of(session, client, organisation_id)
    found = await get(session, organisation_id)
    if kind not in DOCUMENTS[found.kind]:
        raise _invalid("kind", "not_for_this_kind")
    if content_type not in DOCUMENT_TYPES:
        raise _invalid("document", "unsupported_content_type")
    if not 0 < byte_size <= MAX_DOCUMENT_BYTES:
        raise _invalid("document", "too_large" if byte_size else "empty")


def document_key(organisation_id: str, file_id: str) -> str:
    return f"organisations/{organisation_id}/{file_id}"


async def add_document(
    session: AsyncSession,
    client: Actor,
    organisation_id: str,
    kind: DocumentKind,
    *,
    file_id: str,
    content_type: str,
    byte_size: int,
    sha256: str,
    clock: Clock,
) -> str | None:
    """Record a stored document, replacing the one of the same kind. Returns the replaced
    file's key, for the caller to delete once this commits, or None."""
    client_id = await _draft_of(session, client, organisation_id)
    old = await session.scalar(
        text(
            "SELECT storage_key FROM organisation_documents "
            "WHERE organisation_id = :id AND kind = :kind"
        ),
        {"id": organisation_id, "kind": kind.value},
    )
    await session.execute(
        text(
            "INSERT INTO organisation_documents (organisation_id, kind, file_id, storage_key, "
            "content_type, byte_size, sha256, uploaded_by, uploaded_at) VALUES (:id, :kind, "
            ":file, :key, :type, :size, :sha, :client, :now) "
            "ON CONFLICT (organisation_id, kind) DO UPDATE SET file_id = EXCLUDED.file_id, "
            "storage_key = EXCLUDED.storage_key, content_type = EXCLUDED.content_type, "
            "byte_size = EXCLUDED.byte_size, sha256 = EXCLUDED.sha256, "
            "uploaded_by = EXCLUDED.uploaded_by, uploaded_at = EXCLUDED.uploaded_at"
        ),
        {
            "id": organisation_id,
            "kind": kind.value,
            "file": file_id,
            "key": document_key(organisation_id, file_id),
            "type": content_type,
            "size": byte_size,
            "sha": sha256,
            "client": client_id,
            "now": clock.now(),
        },
    )
    await _audit(
        session, client, "organisation.document_added", organisation_id, clock, document=kind.value
    )
    return str(old) if old else None


async def submit(
    session: AsyncSession, client: Actor, organisation_id: str, clock: Clock
) -> Organisation:
    """Hand the draft in for staff to check. Refused, with each problem, until it is ready."""
    await _draft_of(session, client, organisation_id)
    found = await get(session, organisation_id)
    found_problems = found.problems
    if found_problems:
        raise ApplicationError(
            "conflict",
            fields=[FieldProblem(field_path=["problems"], code=p.value) for p in found_problems],
        )
    await session.execute(
        text("UPDATE organisations SET status = 'submitted', submitted_at = :now WHERE id = :id"),
        {"now": clock.now(), "id": organisation_id},
    )
    await _audit(session, client, "organisation.submitted", organisation_id, clock)
    return await get(session, organisation_id)


# Staff --------------------------------------------------------------------------------------


async def check(
    session: AsyncSession, staff: Actor, organisation_id: str, note: str, clock: Clock
) -> Organisation:
    """Check the documents and people, and pass it for a second member of staff to approve."""
    staff_id = _person(staff, "staff")
    cleaned = _note(note)
    await _locked(session, organisation_id, Status.SUBMITTED)
    await _people_still_approved(session, await get(session, organisation_id))
    await session.execute(
        text(
            "UPDATE organisations SET status = 'checked', checked_by = :staff, "
            "checked_at = :now, check_note = :note WHERE id = :id"
        ),
        {"staff": staff_id, "now": clock.now(), "note": cleaned, "id": organisation_id},
    )
    await _audit(session, staff, "organisation.checked", organisation_id, clock, reason=cleaned)
    return await get(session, organisation_id)


async def approve(
    session: AsyncSession, staff: Actor, organisation_id: str, note: str, clock: Clock
) -> Organisation:
    """Open the account of someone else's checked application, with its own account number."""
    staff_id = _person(staff, "staff")
    cleaned = _note(note)
    kind = await _locked(session, organisation_id, Status.CHECKED)
    found = await get(session, organisation_id)
    if found.checked_by == staff_id:
        # PostgreSQL refuses this too. Answering here gives the console a clean refusal.
        raise ApplicationError("forbidden")
    await _people_still_approved(session, found)
    number = await issue_account_number(session, _ACCOUNT_TYPES[kind], clock)
    now = clock.now()
    await session.execute(
        text(
            "UPDATE organisations SET status = 'approved', decided_by = :staff, "
            "decided_at = :now, decision_note = :note, account_number = :number WHERE id = :id"
        ),
        {
            "staff": staff_id,
            "now": now,
            "note": cleaned,
            "number": number.value,
            "id": organisation_id,
        },
    )
    await _audit(session, staff, "organisation.approved", organisation_id, clock, reason=cleaned)
    for person in found.current:
        await notifications.notify(
            session,
            event_name="organisation_approved",
            client_id=person.client_id,
            idempotency_key=f"organisation-approved:{organisation_id}:{person.client_id}",
            variables={"organisation": found.name},
            link=notifications.Link("organisation", organisation_id),
            now=now,
        )
    return await get(session, organisation_id)


async def send_back(
    session: AsyncSession, staff: Actor, organisation_id: str, note: str, clock: Clock
) -> Organisation:
    """Return it to the applicant as a draft to change, saying what. Its check is undone."""
    staff_id = _person(staff, "staff")
    cleaned = _note(note)
    await _locked(session, organisation_id, Status.SUBMITTED, Status.CHECKED)
    now = clock.now()
    await session.execute(
        text(
            "UPDATE organisations SET status = 'draft', checked_by = NULL, checked_at = NULL, "
            "check_note = NULL, returned_by = :staff, returned_at = :now, return_note = :note "
            "WHERE id = :id"
        ),
        {"staff": staff_id, "now": now, "note": cleaned, "id": organisation_id},
    )
    await _audit(session, staff, "organisation.returned", organisation_id, clock, reason=cleaned)
    found = await get(session, organisation_id)
    await _tell_applicant(session, found, "organisation_returned", now)
    return found


async def reject(
    session: AsyncSession, staff: Actor, organisation_id: str, note: str, clock: Clock
) -> Organisation:
    """Turn the application down for good. The applicant is told and may apply again."""
    staff_id = _person(staff, "staff")
    cleaned = _note(note)
    await _locked(session, organisation_id, Status.SUBMITTED, Status.CHECKED)
    now = clock.now()
    await session.execute(
        text(
            "UPDATE organisations SET status = 'rejected', decided_by = :staff, "
            "decided_at = :now, decision_note = :note WHERE id = :id"
        ),
        {"staff": staff_id, "now": now, "note": cleaned, "id": organisation_id},
    )
    await _audit(session, staff, "organisation.rejected", organisation_id, clock, reason=cleaned)
    found = await get(session, organisation_id)
    await _tell_applicant(session, found, "organisation_rejected", now)
    return found


async def tell_invited(session: AsyncSession, payload: dict[str, object], clock: Clock) -> bool:
    """The worker tells an invited client, if they are still invited. True when told."""
    organisation_id, client_id = payload.get("organisation_id"), payload.get("client_id")
    person = payload.get("person")
    if not all(isinstance(v, str) for v in (organisation_id, client_id, person)):
        raise ValueError("an invitation job needs the organisation, client and person")
    still = await session.scalar(
        text(
            "SELECT EXISTS (SELECT FROM organisation_signatories s JOIN organisations o "
            "ON o.id = s.organisation_id WHERE s.organisation_id = :id AND s.client_id = :client "
            "AND s.status = 'invited' AND o.status = 'draft')"
        ),
        {"id": organisation_id, "client": client_id},
    )
    if not still:
        return False
    await notifications.notify(
        session,
        event_name="organisation_invited",
        client_id=str(client_id),
        idempotency_key=f"organisation-invited:{organisation_id}:{client_id}",
        variables={"person": str(person)},
        link=notifications.Link("organisation", str(organisation_id)),
        now=clock.now(),
    )
    return True


# Internals ----------------------------------------------------------------------------------


async def _assemble(session: AsyncSession, row: Row[Any]) -> Organisation:
    signatories = tuple(
        Signatory(
            client_id=s.client_id,
            role=s.role,
            shown_name=s.shown_name,
            account_number=str(s.account_number),
            is_applicant=s.is_applicant,
            status=SignatoryStatus(s.status),
            invited_at=s.invited_at,
            answered_at=s.answered_at,
        )
        for s in (await session.execute(_SIGNATORIES, {"id": row.id})).all()
    )
    documents = tuple(
        Document(
            kind=DocumentKind(d.kind),
            file_id=d.file_id,
            storage_key=d.storage_key,
            content_type=d.content_type,
            byte_size=d.byte_size,
            sha256=str(d.sha256),
            uploaded_at=d.uploaded_at,
        )
        for d in (await session.execute(_DOCUMENTS, {"id": row.id})).all()
    )
    return Organisation(
        id=row.id,
        kind=Kind(row.kind),
        name=row.name,
        registration_number=row.registration_number,
        applicant_id=row.applicant_id,
        approvals_needed=row.approvals_needed,
        status=Status(row.status),
        created_at=row.created_at,
        submitted_at=row.submitted_at,
        checked_by=row.checked_by,
        checked_by_name=row.checked_by_name,
        checked_at=row.checked_at,
        check_note=row.check_note,
        returned_at=row.returned_at,
        return_note=row.return_note,
        decided_by=row.decided_by,
        decided_by_name=row.decided_by_name,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        account_number=str(row.account_number) if row.account_number else None,
        signatories=signatories,
        documents=documents,
    )


async def _approved(session: AsyncSession, client_id: str) -> kyc.Recipient | None:
    found = await kyc.transfer_recipient(session, client_id=client_id)
    if found is None or await client_status(session, client_id) != "active":
        return None
    return found


async def _find(session: AsyncSession, to: str) -> kyc.Recipient | None:
    digits = re.sub(r"[\s-]", "", to.strip())
    if _ACCOUNT_NUMBER.fullmatch(digits):
        found = await kyc.transfer_recipient(session, account_number=digits)
    else:
        client_id = await client_id_for_phone(session, to)
        found = (
            None
            if client_id is None
            else await kyc.transfer_recipient(session, client_id=client_id)
        )
    # Only a personal account signs for an organisation.
    if found is None or not found.account_number.startswith(AccountType.PERSONAL.value):
        return None
    if await client_status(session, found.client_id) != "active":
        return None
    return found


async def _people_still_approved(session: AsyncSession, found: Organisation) -> None:
    """Every person who joined must still be an approved, active client at each staff step."""
    for person in found.current:
        if (
            person.status is not SignatoryStatus.JOINED
            or await _approved(session, person.client_id) is None
        ):
            raise _conflict("signatories", "signatory_not_verified")


async def _draft_of(session: AsyncSession, client: Actor, organisation_id: str) -> str:
    """Lock the organisation, which must be the client's own draft. Returns the client's ID."""
    client_id = _person(client, "client")
    seen = (await session.execute(_PEEK, {"id": organisation_id})).one_or_none()
    if seen is None:
        raise ApplicationError("not_found")
    if seen.applicant_id != client_id:
        # A signatory sees it but only the applicant changes it.
        raise ApplicationError("forbidden")
    row = (await session.execute(_LOCK, {"id": organisation_id})).one()
    if row.status != Status.DRAFT.value:
        raise _conflict("status", f"already_{row.status}")
    return client_id


async def _locked(session: AsyncSession, organisation_id: str, *allowed: Status) -> Kind:
    row = (await session.execute(_LOCK, {"id": organisation_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    if row.status not in {s.value for s in allowed}:
        raise _conflict("status", f"already_{row.status}")
    return Kind(row.kind)


async def _tell_applicant(
    session: AsyncSession, found: Organisation, event: str, now: datetime
) -> None:
    await notifications.notify(
        session,
        event_name=event,
        client_id=found.applicant_id,
        idempotency_key=f"{event}:{found.id}:{int(now.timestamp() * 1000)}",
        variables={"organisation": found.name},
        link=notifications.Link("organisation", found.id),
        now=now,
    )


def _name(value: str) -> str:
    try:
        cleaned = clean_name(value)
    except ValueError:
        raise _invalid("name", "not_a_name") from None
    if cleaned is None:
        raise _invalid("name", "required")
    return cleaned


def _registration(value: str | None) -> str | None:
    try:
        return clean_registration(value)
    except ValueError:
        raise _invalid("registration_number", "not_a_registration_number") from None


def _role(value: str) -> str:
    cleaned = clean_role(value)
    if cleaned is None:
        raise _invalid("role", "required")
    return cleaned


def _approvals(value: int) -> None:
    if not 1 <= value <= MAX_SIGNATORIES:
        raise _invalid("approvals_needed", "out_of_range")


def _note(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > MAX_NOTE:
        raise _invalid("note", "required")
    return cleaned


def _person(actor: Actor, kind: str) -> str:
    if actor.kind != kind or actor.id is None:
        raise ApplicationError("forbidden")
    return actor.id


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=["body", field], code=code)]
    )


def _conflict(field: str, code: str) -> ApplicationError:
    return ApplicationError("conflict", fields=[FieldProblem(field_path=[field], code=code)])


async def _audit(
    session: AsyncSession,
    actor: Actor,
    action: str,
    organisation_id: str,
    clock: Clock,
    *,
    reason: str | None = None,
    **details: str,
) -> None:
    assert actor.id is not None
    category: AuditCategory = (
        "approval"
        if action in ("organisation.approved", "organisation.rejected", "organisation.checked")
        else "change"
    )
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=action,
            resource_type=RESOURCE,
            resource_id=organisation_id,
            actor=AuditActor.client(actor.id)
            if actor.kind == "client"
            else AuditActor.staff(actor.id),
            reason=reason,
            details=dict(details),
        ),
        clock,
    )


async def documents_for_viewing(
    session: AsyncSession, staff: Actor, organisation_id: str, clock: Clock
) -> tuple[Document, ...]:
    """The application's documents, for short-lived links. Each viewing is on the audit trail."""
    _person(staff, "staff")
    found = await get(session, organisation_id)
    assert staff.id is not None
    await audit.record(
        session,
        AuditEvent(
            category="access",
            action="organisation.documents_viewed",
            resource_type=RESOURCE,
            resource_id=organisation_id,
            actor=AuditActor.staff(staff.id),
        ),
        clock,
    )
    return found.documents
