"""Identity check commands and queries. Every function works inside the caller's transaction.

The caller opens `Database.actor_transaction(actor)` for the same actor it passes here; the row
policies and the transition trigger of migration 0050 then hold whatever this code does. A staff
decision, the account number it issues and its audit event commit together or not at all.

No provider is called from here. The worker calls the provider with no transaction open and
hands the answer to `record_provider_result`.
"""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.jobs import JobRequest, enqueue
from app.infrastructure.sealing import Sealer, associated_data
from app.modules.account_numbers.domain.numbers import AccountType
from app.modules.account_numbers.service import issue_account_number
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditCategory, AuditEvent
from app.modules.kyc.domain.checks import (
    CLIENT_EDITABLE,
    COUNTRY,
    ActorKind,
    CheckStatus,
    DocumentType,
    IdentityDetails,
    PhotoKind,
    can_move,
    detail_problems,
    missing_for_submission,
    required_photos,
    retake_problem,
    status_on_submit,
)
from app.modules.kyc.domain.provider import (
    AutomaticCheck,
    CheckOutcome,
    IdentityProvider,
    ProviderRequest,
    Verdict,
    decide,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

RESOURCE = "kyc_check"
# The same as a Finvest 1.0 reference on the migration sheet.
_LEGACY_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,63}")
# The worker's jobs (app/modules/kyc/jobs.py). One send per submission, whatever is retried.
SEND_CHECK = "kyc.send_check"
READ_RESULT = "kyc.read_result"
SWEEP_UNANSWERED = "kyc.sweep_unanswered"
SEND_WITHIN = timedelta(minutes=5)
READ_WITHIN = timedelta(minutes=20)
# Longer than a send and a read can take together, so the sweep never races a live job.
UNANSWERED_AFTER = timedelta(minutes=30)
MAX_PHOTO_BYTES = 10 * 1024 * 1024
PHOTO_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX_CLIENT_MESSAGE = 300
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
# What staff work through, oldest first. `awaiting_client` is listed so they can see who was asked.
QUEUE = (
    CheckStatus.NEEDS_REVIEW,
    CheckStatus.WITH_COMPLIANCE,
    CheckStatus.AWAITING_CLIENT,
    CheckStatus.SUBMITTED,
)


@dataclass(frozen=True, slots=True)
class PhotoView:
    id: str
    kind: PhotoKind
    storage_key: str
    content_type: str
    byte_size: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CheckView:
    id: str
    client_id: str
    status: CheckStatus
    document_type: DocumentType
    document_country: str
    details: IdentityDetails | None
    details_confirmed: bool
    submission: int
    submitted_at: datetime | None
    provider: str | None
    provider_reference: str | None
    automatic_checks: tuple[CheckOutcome, ...]
    review_flags: tuple[str, ...]
    retake_photos: frozenset[PhotoKind]
    client_message: str | None
    decided_at: datetime | None
    decided_by: str | None
    account_number: str | None
    photos: tuple[PhotoView, ...]
    # `finvest1` when the check was carried over from Finvest 1.0, otherwise `finvest2`.
    source: str = "finvest2"

    @property
    def missing(self) -> list[str]:
        """What the client still has to do before submitting."""
        return missing_for_submission(
            self.document_type,
            frozenset(photo.kind for photo in self.photos),
            details_confirmed=self.details_confirmed,
        )


@dataclass(frozen=True, slots=True)
class QueueItem:
    """A row of the review queue. Names are shown to staff, so this never goes into a log."""

    id: str
    status: CheckStatus
    document_type: DocumentType
    surname: str | None
    given_names: str | None
    submitted_at: datetime | None
    review_flags: tuple[str, ...]


_COLUMNS = (
    "id, client_id, status, document_type, document_country, surname, given_names, "
    "document_number_sealed, date_of_birth_sealed, document_expires_on, tin_sealed, "
    "document_fingerprint, details_confirmed_at, submission, "
    "submitted_at, provider, provider_reference, automatic_checks, review_flags, retake_photos, "
    "retake_requested_at, client_message, decided_at, decided_by, account_number, source"
)
_BY_CLIENT = text(f"SELECT {_COLUMNS} FROM kyc_checks WHERE client_id = :client_id")  # noqa: S608
_BY_CLIENT_LOCKED = text(
    f"SELECT {_COLUMNS} FROM kyc_checks WHERE client_id = :client_id FOR UPDATE"  # noqa: S608
)
_BY_ID = text(f"SELECT {_COLUMNS} FROM kyc_checks WHERE id = :id")  # noqa: S608
_BY_ID_LOCKED = text(f"SELECT {_COLUMNS} FROM kyc_checks WHERE id = :id FOR UPDATE")  # noqa: S608
_INSERT = text(
    "INSERT INTO kyc_checks (id, client_id, document_type, document_country, created_at, "
    "updated_at) VALUES (:id, :client_id, :document_type, :document_country, :now, :now) "
    "ON CONFLICT ON CONSTRAINT uq_kyc_checks_client DO NOTHING"
)
_CHANGE_DOCUMENT = text(
    "UPDATE kyc_checks SET document_type = :document_type, document_country = :document_country, "
    "details_confirmed_at = NULL, document_fingerprint = NULL, updated_at = :now WHERE id = :id"
)
_SAVE_DETAILS = text(
    "UPDATE kyc_checks SET surname = :surname, given_names = :given_names, "
    "document_number_sealed = :document_number, date_of_birth_sealed = :date_of_birth, "
    "document_expires_on = :document_expires_on, tin_sealed = :tin, "
    "document_fingerprint = :document_fingerprint, details_confirmed_at = :now, "
    "updated_at = :now WHERE id = :id"
)
_SUBMIT = text(
    "UPDATE kyc_checks SET status = :status, submission = submission + 1, submitted_at = :now, "
    "provider = :provider, provider_reference = NULL, automatic_checks = '[]'::jsonb, "
    "review_flags = :review_flags, retake_photos = '{}', retake_requested_at = NULL, "
    "client_message = NULL, updated_at = :now WHERE id = :id"
)
_TO_REVIEW = text(
    "UPDATE kyc_checks SET status = 'needs_review', provider_reference = :provider_reference, "
    "automatic_checks = CAST(:automatic_checks AS jsonb), review_flags = :review_flags, "
    "updated_at = :now WHERE id = :id"
)
_APPROVE = text(
    "UPDATE kyc_checks SET status = 'approved', account_number = :account_number, "
    "provider_reference = coalesce(:provider_reference, provider_reference), "
    "automatic_checks = coalesce(CAST(:automatic_checks AS jsonb), automatic_checks), "
    "decided_at = :now, decided_by = :decided_by, updated_at = :now WHERE id = :id"
)
_REJECT = text(
    "UPDATE kyc_checks SET status = 'rejected', client_message = :client_message, "
    "decided_at = :now, decided_by = :decided_by, updated_at = :now WHERE id = :id"
)
_ASK_FOR_PHOTOS = text(
    "UPDATE kyc_checks SET status = 'awaiting_client', retake_photos = :retake_photos, "
    "retake_requested_at = :now, client_message = :client_message, updated_at = :now "
    "WHERE id = :id"
)
_ESCALATE = text(
    "UPDATE kyc_checks SET status = 'with_compliance', updated_at = :now WHERE id = :id"
)
_NOTE_REFERENCE = text(
    "UPDATE kyc_checks SET provider_reference = :provider_reference, updated_at = :now "
    "WHERE id = :id"
)
_GIVE_TO_PERSON = text(
    "UPDATE kyc_checks SET status = 'needs_review', review_flags = :review_flags, "
    "updated_at = :now WHERE id = :id"
)
_UNANSWERED = text(
    "SELECT id, submission FROM kyc_checks WHERE status = 'submitted' AND submitted_at < :before "
    "ORDER BY submitted_at, id LIMIT :limit FOR UPDATE SKIP LOCKED"
)
_INSERT_CARRIED_OVER = text(
    "INSERT INTO kyc_checks (id, client_id, status, document_type, document_country, surname, "
    "given_names, document_number_sealed, date_of_birth_sealed, document_expires_on, tin_sealed, "
    "document_fingerprint, details_confirmed_at, decided_at, account_number, source, "
    "legacy_reference, created_at, updated_at) VALUES (:id, :client_id, 'approved', "
    ":document_type, :document_country, :surname, :given_names, :document_number, "
    ":date_of_birth, :document_expires_on, :tin, :document_fingerprint, :now, :now, "
    ":account_number, 'finvest1', :legacy_reference, :now, :now)"
)
_BY_LEGACY_REFERENCE = text(
    f"SELECT {_COLUMNS} FROM kyc_checks WHERE legacy_reference = :legacy_reference"  # noqa: S608
)
_DOCUMENT_TAKEN = text(
    "SELECT EXISTS (SELECT FROM kyc_checks WHERE status = 'approved' "
    "AND document_fingerprint = :document_fingerprint)"
)
_PHOTOS = text(
    "SELECT id, kind, storage_key, content_type, byte_size, created_at FROM kyc_photos "
    "WHERE check_id = :check_id AND replaced_at IS NULL ORDER BY kind"
)
_REPLACE_PHOTO = text(
    "UPDATE kyc_photos SET replaced_at = :now "
    "WHERE check_id = :check_id AND kind = :kind AND replaced_at IS NULL"
)
_INSERT_PHOTO = text(
    "INSERT INTO kyc_photos (id, check_id, kind, storage_key, content_type, byte_size, sha256, "
    "created_at) VALUES (:id, :check_id, :kind, :storage_key, :content_type, :byte_size, "
    ":sha256, :now)"
)
_QUEUE = text(
    "SELECT id, status, document_type, surname, given_names, submitted_at, review_flags "
    "FROM kyc_checks WHERE status = ANY(:statuses) "
    "ORDER BY array_position(:statuses, status::text), submitted_at, id LIMIT :limit"
)


# Client steps ---------------------------------------------------------------------------------


async def choose_document(
    session: AsyncSession,
    sealer: Sealer,
    client: Actor,
    document_type: DocumentType,
    document_country: str,
    clock: Clock,
) -> CheckView:
    """Start the client's check, or change the document while it is still a draft."""
    client_id = _person(client, "client")
    if COUNTRY.fullmatch(document_country) is None:
        raise _invalid("document_country", "invalid_country")
    now = clock.now()
    await session.execute(
        _INSERT,
        {
            "id": push_id(),
            "client_id": client_id,
            "document_type": document_type.value,
            "document_country": document_country,
            "now": now,
        },
    )
    row = (await session.execute(_BY_CLIENT_LOCKED, {"client_id": client_id})).one()
    if (row.document_type, row.document_country) != (document_type.value, document_country):
        if row.status != CheckStatus.DRAFT:
            raise ApplicationError("conflict")
        # Details were read from the other document, so they must be confirmed again.
        await session.execute(
            _CHANGE_DOCUMENT,
            {
                "id": row.id,
                "document_type": document_type.value,
                "document_country": document_country,
                "now": now,
            },
        )
    return await _view_by_id(session, sealer, row.id)


def photo_key(check_id: str, photo_id: str) -> str:
    """Where a photo's file is kept in private storage."""
    return f"kyc/{check_id}/{photo_id}"


async def photo_slot(
    session: AsyncSession, client: Actor, kind: PhotoKind, *, content_type: str, byte_size: int
) -> str:
    """The check a photo of this kind may be added to now, or the refusal add_photo would give.

    Run before the file is stored, so a refused photo never leaves a file behind.
    """
    row = await _editable(session, client)
    _check_photo(row, kind, content_type, byte_size)
    return str(row.id)


async def add_photo(
    session: AsyncSession,
    client: Actor,
    kind: PhotoKind,
    *,
    content_type: str,
    byte_size: int,
    sha256: str,
    clock: Clock,
    photo_id: str | None = None,
) -> PhotoView:
    """Record a photo the client uploaded, replacing the current one of the same kind.

    The file itself goes to private storage under `photo_key(check, photo_id)`. The earlier
    photo row is kept, marked as replaced.
    """
    row = await _editable(session, client)
    _check_photo(row, kind, content_type, byte_size)
    now = clock.now()
    photo_id = photo_id or push_id()
    storage_key = photo_key(row.id, photo_id)
    await session.execute(_REPLACE_PHOTO, {"check_id": row.id, "kind": kind.value, "now": now})
    await session.execute(
        _INSERT_PHOTO,
        {
            "id": photo_id,
            "check_id": row.id,
            "kind": kind.value,
            "storage_key": storage_key,
            "content_type": content_type,
            "byte_size": byte_size,
            "sha256": sha256,
            "now": now,
        },
    )
    return PhotoView(photo_id, kind, storage_key, content_type, byte_size, now)


async def confirm_details(
    session: AsyncSession, sealer: Sealer, client: Actor, details: IdentityDetails, clock: Clock
) -> CheckView:
    """Save the details the client confirmed. Only while the check is a draft."""
    row = await _editable(session, client)
    if row.status != CheckStatus.DRAFT:
        raise ApplicationError("conflict")
    details = details.normalised()
    now = clock.now()
    problems = detail_problems(details, now.date())
    if problems:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=[p.field], code=p.code) for p in problems],
        )
    await session.execute(
        _SAVE_DETAILS,
        {
            "id": row.id,
            "surname": details.surname,
            "given_names": details.given_names,
            "document_number": _seal(sealer, row.id, "document_number", details.document_number),
            "date_of_birth": _seal(
                sealer, row.id, "date_of_birth", details.date_of_birth.isoformat()
            ),
            "document_expires_on": details.document_expires_on,
            "tin": _seal(sealer, row.id, "tin", details.tin) if details.tin else None,
            # The country and type are part of it: the same digits on another kind of document
            # are a different document.
            "document_fingerprint": sealer.fingerprint(
                f"{row.document_country}|{row.document_type}|{details.document_number}"
            ),
            "now": now,
        },
    )
    return await _view_by_id(session, sealer, row.id)


async def submit(
    session: AsyncSession, sealer: Sealer, client: Actor, provider: IdentityProvider, clock: Clock
) -> CheckView:
    """Hand the check in. It goes to the provider, or to a person when nothing automatic applies."""
    row = await _editable(session, client)
    view = await _view(session, sealer, row)
    missing = view.missing
    if row.status == CheckStatus.AWAITING_CLIENT:
        # Strictly later: a photo from the same instant as the request is the one staff saw.
        fresh = {p.kind for p in view.photos if p.created_at > row.retake_requested_at}
        missing += [kind.value for kind in PhotoKind if kind in view.retake_photos - fresh]
    if missing:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=[name], code="missing") for name in missing],
        )
    target = status_on_submit(view.document_type, automatic_checks=provider.automatic)
    _require_move("client", view.status, target)
    flags: list[str] = []
    if target is CheckStatus.NEEDS_REVIEW:
        flags = ["no_automatic_checks" if not provider.automatic else "document_needs_person"]
    now = clock.now()
    await session.execute(
        _SUBMIT,
        {
            "id": row.id,
            "status": target.value,
            "provider": provider.name,
            "review_flags": flags,
            "now": now,
        },
    )
    if target is CheckStatus.SUBMITTED:
        # Saved with the submission, so the check cannot be handed in and never sent.
        await enqueue(
            session,
            JobRequest(
                kind=SEND_CHECK,
                idempotency_key=submission_key(row.id, row.submission + 1),
                payload={"check_id": row.id, "submission": row.submission + 1},
                max_age=SEND_WITHIN,
            ),
            now=now,
        )
    await _audit(session, client, "change", "kyc.check.submitted", row.id, clock)
    return await _view_by_id(session, sealer, row.id)


async def check_for_client(
    session: AsyncSession, sealer: Sealer, client: Actor
) -> CheckView | None:
    row = (
        await session.execute(_BY_CLIENT, {"client_id": _person(client, "client")})
    ).one_or_none()
    return None if row is None else await _view(session, sealer, row)


# The worker, around the provider -----------------------------------------------------------


def submission_key(check_id: str, submission: int) -> str:
    """The idempotency key of one submission, at the provider and in the job queue."""
    return f"{check_id}:{submission}"


async def provider_request(
    session: AsyncSession, sealer: Sealer, check_id: str, submission: int
) -> ProviderRequest | None:
    """What to send to the provider, or None when this submission no longer waits for one."""
    row = (await session.execute(_BY_ID, {"id": check_id})).one_or_none()
    if row is None or row.status != CheckStatus.SUBMITTED or row.submission != submission:
        return None
    view = await _view(session, sealer, row)
    if view.details is None:  # the database refuses a submitted check without details
        return None
    return ProviderRequest(
        idempotency_key=submission_key(check_id, submission),
        document_type=view.document_type,
        document_country=view.document_country,
        details=view.details,
        photos={photo.kind: photo.storage_key for photo in view.photos},
    )


async def awaited_reference(session: AsyncSession, check_id: str, submission: int) -> str | None:
    """The provider's reference for a submission that still waits for its result."""
    row = (await session.execute(_BY_ID, {"id": check_id})).one_or_none()
    if row is None or row.status != CheckStatus.SUBMITTED or row.submission != submission:
        return None
    reference: str | None = row.provider_reference
    return reference


async def note_provider_reference(
    session: AsyncSession, check_id: str, submission: int, provider_reference: str, clock: Clock
) -> bool:
    """Save the reference the provider gave. False when the submission already moved on."""
    row = await _awaiting_provider(session, check_id, submission)
    if row is None:
        return False
    await session.execute(
        _NOTE_REFERENCE,
        {"id": row.id, "provider_reference": provider_reference, "now": clock.now()},
    )
    return True


async def give_to_person(
    session: AsyncSession, check_id: str, submission: int, flag: str, clock: Clock
) -> bool:
    """Send a submission the provider did not check to staff, with the reason as its flag."""
    row = await _awaiting_provider(session, check_id, submission)
    if row is None:
        return False
    await session.execute(
        _GIVE_TO_PERSON, {"id": row.id, "review_flags": [flag], "now": clock.now()}
    )
    await _audit(
        session,
        Actor("system"),
        "change",
        "kyc.check.sent_to_review",
        row.id,
        clock,
        details={"flags": [flag]},
    )
    return True


async def give_unanswered_to_people(
    session: AsyncSession, clock: Clock, *, limit: int = MAX_PAGE_SIZE
) -> int:
    """Checks the provider never answered go to staff, so no client waits on a lost job."""
    rows = await session.execute(
        _UNANSWERED, {"before": clock.now() - UNANSWERED_AFTER, "limit": limit}
    )
    moved = 0
    for row in rows.all():
        moved += await give_to_person(session, row.id, row.submission, "provider_no_answer", clock)
    return moved


async def _awaiting_provider(
    session: AsyncSession, check_id: str, submission: int
) -> Row[Any] | None:
    row = (await session.execute(_BY_ID_LOCKED, {"id": check_id})).one_or_none()
    if row is None or row.status != CheckStatus.SUBMITTED or row.submission != submission:
        return None
    return row


async def record_provider_result(
    session: AsyncSession,
    sealer: Sealer,
    check_id: str,
    submission: int,
    provider_reference: str,
    outcomes: tuple[CheckOutcome, ...],
    clock: Clock,
    *,
    automatic_approval: bool = False,
) -> CheckView:
    """Apply the provider's answer. Unless `automatic_approval` is on, a person always decides.

    With it on, a client is approved when every check passed and their document is still valid.

    An answer for an earlier submission, or for a check that already moved on, changes nothing.
    """
    row = (await session.execute(_BY_ID_LOCKED, {"id": check_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    if row.status != CheckStatus.SUBMITTED or row.submission != submission:
        return await _view(session, sealer, row)
    decision = decide(outcomes, automatic_approval=automatic_approval)
    flags = list(decision.flags)
    if decision.approve and row.document_expires_on < clock.now().date():
        flags = ["document_expired"]
    elif decision.approve and await _document_taken(session, row):
        flags = ["duplicate_document"]
    checks = json.dumps([_outcome_json(outcome) for outcome in outcomes], separators=(",", ":"))
    system = Actor("system")
    if not flags:
        approved = await _approve(
            session,
            row,
            decided_by=None,
            provider_reference=provider_reference,
            automatic_checks=checks,
            clock=clock,
        )
        if approved:
            await _audit(session, system, "change", "kyc.check.approved", row.id, clock)
            return await _view_by_id(session, sealer, row.id)
        flags = ["duplicate_document"]
    await session.execute(
        _TO_REVIEW,
        {
            "id": row.id,
            "provider_reference": provider_reference,
            "automatic_checks": checks,
            "review_flags": flags,
            "now": clock.now(),
        },
    )
    await _audit(
        session,
        system,
        "change",
        "kyc.check.sent_to_review",
        row.id,
        clock,
        details={"flags": flags},
    )
    return await _view_by_id(session, sealer, row.id)


# Finvest 1.0 ----------------------------------------------------------------------------------


async def record_carried_over_check(
    session: AsyncSession,
    sealer: Sealer,
    system: Actor,
    *,
    client_id: str,
    document_type: DocumentType,
    document_country: str,
    details: IdentityDetails,
    legacy_reference: str,
    reason: str,
    clock: Clock,
) -> CheckView:
    """Carry over an identity Finvest 1.0 checked, approved, with a new account number.

    The switch calls it in the worker, in its own transaction, for a client whose document has
    not expired; one whose document has expired does the full check when they claim. An unknown
    expiry is accepted. Calling again with the same reference for the same client returns the
    same check and issues nothing. The client is not told: they hear at the claim.
    """
    if system.kind != "system":
        raise ApplicationError("forbidden")
    existing = (
        await session.execute(_BY_LEGACY_REFERENCE, {"legacy_reference": legacy_reference})
    ).one_or_none()
    if existing is not None:
        if existing.client_id != client_id:
            raise ApplicationError("conflict")
        return await _view(session, sealer, existing)
    if COUNTRY.fullmatch(document_country) is None:
        raise _invalid("document_country", "invalid_country")
    if not _LEGACY_REFERENCE.fullmatch(legacy_reference):
        raise _invalid("legacy_reference", "invalid")
    if not reason.strip():
        raise _invalid("reason", "missing")
    details = details.normalised()
    now = clock.now()
    problems = detail_problems(details, now.date())
    if problems:
        raise ApplicationError(
            "validation_failed",
            fields=[FieldProblem(field_path=[p.field], code=p.code) for p in problems],
        )
    if (await session.execute(_BY_CLIENT, {"client_id": client_id})).one_or_none() is not None:
        # The client already handed in a check of their own.
        raise ApplicationError("conflict")
    check_id = push_id()
    fingerprint = sealer.fingerprint(
        f"{document_country}|{document_type.value}|{details.document_number}"
    )
    taken = await session.execute(_DOCUMENT_TAKEN, {"document_fingerprint": fingerprint})
    if taken.scalar_one():
        raise _invalid("document_number", "document_taken")
    try:
        async with session.begin_nested():
            number = await issue_account_number(session, AccountType.PERSONAL, clock)
            await session.execute(
                _INSERT_CARRIED_OVER,
                {
                    "id": check_id,
                    "client_id": client_id,
                    "document_type": document_type.value,
                    "document_country": document_country,
                    "surname": details.surname,
                    "given_names": details.given_names,
                    "document_number": _seal(
                        sealer, check_id, "document_number", details.document_number
                    ),
                    "date_of_birth": _seal(
                        sealer, check_id, "date_of_birth", details.date_of_birth.isoformat()
                    ),
                    "document_expires_on": details.document_expires_on,
                    "tin": _seal(sealer, check_id, "tin", details.tin) if details.tin else None,
                    "document_fingerprint": fingerprint,
                    "account_number": number.value,
                    "legacy_reference": legacy_reference,
                    "now": now,
                },
            )
    except IntegrityError as error:
        constraint = str(error.orig)
        if "uq_kyc_checks_approved_document" in constraint:
            raise _invalid("document_number", "document_taken") from None
        if "uq_kyc_checks_client" in constraint or "uq_kyc_checks_legacy_reference" in constraint:
            raise ApplicationError("conflict") from None
        raise
    await _audit(
        session,
        system,
        "approval",
        "kyc.check.carried_over",
        check_id,
        clock,
        reason=reason,
        details={"legacy_reference": legacy_reference},
    )
    return await _view_by_id(session, sealer, check_id)


# Staff ----------------------------------------------------------------------------------------


async def review_queue(session: AsyncSession, *, limit: int = DEFAULT_PAGE_SIZE) -> list[QueueItem]:
    """Checks that are not decided, the ones needing a person first. Staff transactions only."""
    rows = await session.execute(
        _QUEUE,
        {"statuses": [s.value for s in QUEUE], "limit": max(1, min(limit, MAX_PAGE_SIZE))},
    )
    return [
        QueueItem(
            id=row.id,
            status=CheckStatus(row.status),
            document_type=DocumentType(row.document_type),
            surname=row.surname,
            given_names=row.given_names,
            submitted_at=row.submitted_at,
            review_flags=tuple(row.review_flags),
        )
        for row in rows.all()
    ]


async def check_for_staff(session: AsyncSession, sealer: Sealer, check_id: str) -> CheckView:
    return await _view_by_id(session, sealer, check_id)


_VERIFIED_NAME = text(
    "SELECT surname, given_names FROM kyc_checks "
    "WHERE client_id = :client_id AND status = 'approved'"
)


async def verified_name(session: AsyncSession, client_id: str) -> str | None:
    """The name on an approved client's identity document, as "SURNAME Given names".

    None until the client is approved. For staff transactions, such as checking that a payout
    destination is in the client's own name.
    """
    row = (await session.execute(_VERIFIED_NAME, {"client_id": client_id})).one_or_none()
    return None if row is None else f"{row.surname} {row.given_names}"


@dataclass(frozen=True, slots=True)
class Identity:
    """What a client's check says about who they are, for a card that names them."""

    # "SURNAME Given names" from the document. None until the check is approved.
    verified_name: str | None
    # Issued when the check is approved.
    account_number: str | None


_IDENTITIES = text(
    "SELECT client_id, status, surname, given_names, account_number FROM kyc_checks "
    "WHERE client_id = ANY(CAST(:ids AS text[]))"
)


async def identities(session: AsyncSession, client_ids: list[str]) -> dict[str, Identity]:
    """`verified_name` and the account number for each of these clients, in one query.

    A client who has not started a check is left out. For staff transactions.
    """
    if not client_ids:
        return {}
    rows = await session.execute(_IDENTITIES, {"ids": list(dict.fromkeys(client_ids))})
    return {
        row.client_id: Identity(
            verified_name=f"{row.surname} {row.given_names}" if row.status == "approved" else None,
            account_number=row.account_number,
        )
        for row in rows
    }


@dataclass(frozen=True, slots=True)
class AccountHolder:
    """Who an approved account belongs to, as printed on a statement or a receipt."""

    surname: str
    given_names: str
    account_number: str  # ten digits, issued at approval


_ACCOUNT_HOLDER = text(
    "SELECT surname, given_names, account_number FROM kyc_checks "
    "WHERE client_id = :client_id AND status = 'approved'"
)


async def account_holder(session: AsyncSession, client_id: str) -> AccountHolder | None:
    """The approved client's name and account number, or None until they are approved.

    For the worker and staff, such as the documents module writing a statement.
    """
    row = (await session.execute(_ACCOUNT_HOLDER, {"client_id": client_id})).one_or_none()
    if row is None or row.account_number is None:
        return None
    return AccountHolder(row.surname, row.given_names, row.account_number)


async def show_document_number(
    session: AsyncSession, sealer: Sealer, staff: Actor, check_id: str, clock: Clock
) -> str:
    """The whole document number, for the staff member who asked. Each look is on the trail."""
    _person(staff, "staff")
    view = await _view_by_id(session, sealer, check_id)
    if view.details is None:
        raise ApplicationError("not_found")
    await _audit(session, staff, "access", "kyc.check.document_number_shown", check_id, clock)
    return view.details.document_number


async def photos_for_viewing(
    session: AsyncSession, sealer: Sealer, staff: Actor, check_id: str, clock: Clock
) -> tuple[PhotoView, ...]:
    """The check's current photos, for signing links to them. Each request is on the trail."""
    _person(staff, "staff")
    view = await _view_by_id(session, sealer, check_id)
    await _audit(session, staff, "access", "kyc.check.photos_viewed", check_id, clock)
    return view.photos


@dataclass(frozen=True, slots=True)
class CheckSummary:
    """Where a client's identity check stands, for the console's client page. No details."""

    id: str
    status: str
    submitted_at: datetime | None
    decided_at: datetime | None
    account_number: str | None


_SUMMARY = text(
    "SELECT id, status, submitted_at, decided_at, account_number FROM kyc_checks "
    "WHERE client_id = :client_id"
)
_BY_ACCOUNT_NUMBER = text(
    "SELECT client_id FROM kyc_checks WHERE account_number = :number AND status = 'approved'"
)


async def check_summary(session: AsyncSession, client_id: str) -> CheckSummary | None:
    """The client's check, or None before they start one. For staff transactions."""
    row = (await session.execute(_SUMMARY, {"client_id": client_id})).one_or_none()
    if row is None:
        return None
    return CheckSummary(row.id, row.status, row.submitted_at, row.decided_at, row.account_number)


async def client_for_account_number(session: AsyncSession, number: str) -> str | None:
    """The client an account number was issued to, for staff looking a client up."""
    client_id = await session.scalar(_BY_ACCOUNT_NUMBER, {"number": number})
    return str(client_id) if client_id is not None else None


async def approve(
    session: AsyncSession, sealer: Sealer, staff: Actor, check_id: str, reason: str, clock: Clock
) -> CheckView:
    """Approve the client and issue their account number in this same transaction."""
    row = await _for_decision(session, staff, check_id, CheckStatus.APPROVED)
    # The document was valid when the client confirmed it. It must still be valid today.
    if row.document_expires_on < clock.now().date():
        raise _invalid("document_expires_on", "document_expired")
    approved = await _approve(
        session,
        row,
        decided_by=staff.id,
        provider_reference=None,
        automatic_checks=None,
        clock=clock,
    )
    if not approved:
        # Another approved client already holds this identity document.
        raise ApplicationError("conflict")
    await _audit(session, staff, "approval", "kyc.check.approved", row.id, clock, reason=reason)
    return await _view_by_id(session, sealer, row.id)


async def reject(
    session: AsyncSession,
    sealer: Sealer,
    staff: Actor,
    check_id: str,
    reason: str,
    clock: Clock,
    *,
    client_message: str | None = None,
) -> CheckView:
    row = await _for_decision(session, staff, check_id, CheckStatus.REJECTED)
    await session.execute(
        _REJECT,
        {
            "id": row.id,
            "client_message": _message(client_message, required=False),
            "decided_by": staff.id,
            "now": clock.now(),
        },
    )
    await _audit(session, staff, "approval", "kyc.check.rejected", row.id, clock, reason=reason)
    return await _view_by_id(session, sealer, row.id)


async def ask_for_photos(
    session: AsyncSession,
    sealer: Sealer,
    staff: Actor,
    check_id: str,
    photos: frozenset[PhotoKind],
    client_message: str,
    clock: Clock,
) -> CheckView:
    """Ask the client to take named photos again. Everything else they handed in is kept."""
    row = await _for_decision(session, staff, check_id, CheckStatus.AWAITING_CLIENT)
    problem = retake_problem(DocumentType(row.document_type), photos)
    if problem is not None:
        raise _invalid("photos", problem)
    kinds = sorted(kind.value for kind in photos)
    await session.execute(
        _ASK_FOR_PHOTOS,
        {
            "id": row.id,
            "retake_photos": kinds,
            "client_message": _message(client_message, required=True),
            "now": clock.now(),
        },
    )
    # The message to the client can name them, so only the photo kinds reach the trail.
    await _audit(
        session,
        staff,
        "approval",
        "kyc.check.photos_requested",
        row.id,
        clock,
        reason="Asked the client for new photos",
        details={"photos": kinds},
    )
    return await _view_by_id(session, sealer, row.id)


async def escalate(
    session: AsyncSession, sealer: Sealer, staff: Actor, check_id: str, reason: str, clock: Clock
) -> CheckView:
    row = await _for_decision(session, staff, check_id, CheckStatus.WITH_COMPLIANCE)
    await session.execute(_ESCALATE, {"id": row.id, "now": clock.now()})
    await _audit(session, staff, "approval", "kyc.check.escalated", row.id, clock, reason=reason)
    return await _view_by_id(session, sealer, row.id)


# Internals ------------------------------------------------------------------------------------


def _seal(sealer: Sealer, check_id: str, column: str, value: str) -> bytes:
    return sealer.seal(value, associated_data("kyc_checks", column, check_id))


def _open(sealer: Sealer, check_id: str, column: str, sealed: bytes) -> str:
    return sealer.open(sealed, associated_data("kyc_checks", column, check_id))


def _person(actor: Actor, kind: ActorKind) -> str:
    if actor.kind != kind or actor.id is None:
        raise ApplicationError("forbidden")
    return actor.id


def _invalid(field: str, code: str) -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[field], code=code)]
    )


def _message(value: str | None, *, required: bool) -> str | None:
    cleaned = " ".join(value.split()) if value else ""
    if not cleaned:
        if required:
            raise _invalid("client_message", "missing")
        return None
    if len(cleaned) > MAX_CLIENT_MESSAGE:
        raise _invalid("client_message", "too_long")
    return cleaned


def _require_move(actor: ActorKind, current: CheckStatus, target: CheckStatus) -> None:
    if not can_move(actor, current, target):
        raise ApplicationError("conflict")


def _check_photo(row: Row[Any], kind: PhotoKind, content_type: str, byte_size: int) -> None:
    if kind not in _required(row):
        raise _invalid("kind", "photo_not_part_of_document")
    if row.status == CheckStatus.AWAITING_CLIENT and kind.value not in row.retake_photos:
        raise ApplicationError("conflict")
    if content_type not in PHOTO_CONTENT_TYPES:
        raise _invalid("content_type", "unsupported_content_type")
    if type(byte_size) is not int or not 0 < byte_size <= MAX_PHOTO_BYTES:
        raise _invalid("byte_size", "invalid_size")


def _required(row: Row[Any]) -> frozenset[PhotoKind]:
    return required_photos(DocumentType(row.document_type))


async def _editable(session: AsyncSession, client: Actor) -> Row[Any]:
    row = (
        await session.execute(_BY_CLIENT_LOCKED, {"client_id": _person(client, "client")})
    ).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    if CheckStatus(row.status) not in CLIENT_EDITABLE:
        raise ApplicationError("conflict")
    return row


async def _for_decision(
    session: AsyncSession, staff: Actor, check_id: str, target: CheckStatus
) -> Row[Any]:
    _person(staff, "staff")
    row = (await session.execute(_BY_ID_LOCKED, {"id": check_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    _require_move("staff", CheckStatus(row.status), target)
    return row


async def _document_taken(session: AsyncSession, row: Row[Any]) -> bool:
    taken = await session.execute(
        _DOCUMENT_TAKEN, {"document_fingerprint": row.document_fingerprint}
    )
    return bool(taken.scalar_one())


async def _approve(
    session: AsyncSession,
    row: Row[Any],
    *,
    decided_by: str | None,
    provider_reference: str | None,
    automatic_checks: str | None,
    clock: Clock,
) -> bool:
    """Issue the account number and approve. False when the identity document is already taken.

    The unique index decides, inside a savepoint, so two approvals racing for one document
    cannot both win and the loser's account number is not kept.
    """
    if await _document_taken(session, row):
        return False
    try:
        async with session.begin_nested():
            number = await issue_account_number(session, AccountType.PERSONAL, clock)
            await session.execute(
                _APPROVE,
                {
                    "id": row.id,
                    "account_number": number.value,
                    "provider_reference": provider_reference,
                    "automatic_checks": automatic_checks,
                    "decided_by": decided_by,
                    "now": clock.now(),
                },
            )
    except IntegrityError as error:
        if "uq_kyc_checks_approved_document" not in str(error.orig):
            raise
        return False
    return True


async def _audit(
    session: AsyncSession,
    actor: Actor,
    category: AuditCategory,
    action: str,
    check_id: str,
    clock: Clock,
    *,
    reason: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    if category == "approval" and not (reason and reason.strip()):
        raise _invalid("reason", "missing")
    await audit.record(
        session,
        AuditEvent(
            category=category,
            action=action,
            resource_type=RESOURCE,
            resource_id=check_id,
            actor=AuditActor(actor.kind, actor.id),
            reason=reason,
            details=details or {},
        ),
        clock,
    )


def _outcome_json(outcome: CheckOutcome) -> dict[str, str | int | None]:
    return {"check": outcome.check.value, "verdict": outcome.verdict.value, "score": outcome.score}


async def _view_by_id(session: AsyncSession, sealer: Sealer, check_id: str) -> CheckView:
    row = (await session.execute(_BY_ID, {"id": check_id})).one_or_none()
    if row is None:
        raise ApplicationError("not_found")
    return await _view(session, sealer, row)


async def _view(session: AsyncSession, sealer: Sealer, row: Row[Any]) -> CheckView:
    photos = await session.execute(_PHOTOS, {"check_id": row.id})
    details = None
    if row.surname is not None:
        details = IdentityDetails(
            surname=row.surname,
            given_names=row.given_names,
            document_number=_open(sealer, row.id, "document_number", row.document_number_sealed),
            date_of_birth=date.fromisoformat(
                _open(sealer, row.id, "date_of_birth", row.date_of_birth_sealed)
            ),
            document_expires_on=row.document_expires_on,
            tin=_open(sealer, row.id, "tin", row.tin_sealed) if row.tin_sealed else None,
        )
    return CheckView(
        id=row.id,
        client_id=row.client_id,
        status=CheckStatus(row.status),
        document_type=DocumentType(row.document_type),
        document_country=row.document_country,
        details=details,
        details_confirmed=row.details_confirmed_at is not None,
        submission=row.submission,
        submitted_at=row.submitted_at,
        provider=row.provider,
        provider_reference=row.provider_reference,
        automatic_checks=tuple(
            CheckOutcome(AutomaticCheck(item["check"]), Verdict(item["verdict"]), item["score"])
            for item in row.automatic_checks
        ),
        review_flags=tuple(row.review_flags),
        retake_photos=frozenset(PhotoKind(kind) for kind in row.retake_photos),
        client_message=row.client_message,
        decided_at=row.decided_at,
        decided_by=row.decided_by,
        account_number=row.account_number,
        source=row.source,
        photos=tuple(
            PhotoView(
                id=photo.id,
                kind=PhotoKind(photo.kind),
                storage_key=photo.storage_key,
                content_type=photo.content_type,
                byte_size=photo.byte_size,
                created_at=photo.created_at,
            )
            for photo in photos.all()
        ),
    )
