"""The identity check: what a client hands in, its states and who may move it between them.

Pure rules. Nothing here reads a clock, a database or a provider. Migration 0050 repeats the
transition table in a trigger, so a bug in a service cannot approve a client either.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Literal

ActorKind = Literal["client", "staff", "system"]


class DocumentType(StrEnum):
    NATIONAL_ID = "national_id"
    PASSPORT = "passport"
    DRIVING_PERMIT = "driving_permit"
    REFUGEE_ID = "refugee_id"


class PhotoKind(StrEnum):
    FRONT = "front"
    BACK = "back"
    SELFIE = "selfie"


class CheckStatus(StrEnum):
    DRAFT = "draft"  # the client is still adding photos and details
    SUBMITTED = "submitted"  # automatic checks are running
    NEEDS_REVIEW = "needs_review"  # a person must decide
    WITH_COMPLIANCE = "with_compliance"  # escalated by the reviewer
    AWAITING_CLIENT = "awaiting_client"  # staff asked for new photos
    APPROVED = "approved"  # final; the account number is issued
    REJECTED = "rejected"  # final


FINAL = frozenset({CheckStatus.APPROVED, CheckStatus.REJECTED})
# States in which the client may still change photos and details.
CLIENT_EDITABLE = frozenset({CheckStatus.DRAFT, CheckStatus.AWAITING_CLIENT})

_STAFF_QUEUE = frozenset({CheckStatus.NEEDS_REVIEW, CheckStatus.WITH_COMPLIANCE})
_TRANSITIONS: dict[ActorKind, dict[CheckStatus, frozenset[CheckStatus]]] = {
    "client": dict.fromkeys(
        CLIENT_EDITABLE, frozenset({CheckStatus.SUBMITTED, CheckStatus.NEEDS_REVIEW})
    ),
    "system": {
        CheckStatus.SUBMITTED: frozenset({CheckStatus.APPROVED, CheckStatus.NEEDS_REVIEW}),
    },
    "staff": {
        CheckStatus.NEEDS_REVIEW: frozenset(
            {
                CheckStatus.APPROVED,
                CheckStatus.REJECTED,
                CheckStatus.AWAITING_CLIENT,
                CheckStatus.WITH_COMPLIANCE,
            }
        ),
        CheckStatus.WITH_COMPLIANCE: frozenset(
            {CheckStatus.APPROVED, CheckStatus.REJECTED, CheckStatus.AWAITING_CLIENT}
        ),
    },
}


def can_move(actor: ActorKind, current: CheckStatus, target: CheckStatus) -> bool:
    return target in _TRANSITIONS[actor].get(current, frozenset())


def staff_moves(current: CheckStatus) -> frozenset[CheckStatus]:
    """Where staff may take a check from here. Empty when there is nothing for them to decide."""
    return _TRANSITIONS["staff"].get(current, frozenset())


def mask_document_number(value: str) -> str:
    """Enough of a number to recognise it, as "CM90••••••••4PL". A short one shows nothing."""
    if len(value) <= 7:
        return "•" * len(value)
    return value[:4] + "•" * (len(value) - 7) + value[-3:]


_BOTH_SIDES = frozenset({PhotoKind.FRONT, PhotoKind.BACK, PhotoKind.SELFIE})
_REQUIRED_PHOTOS: dict[DocumentType, frozenset[PhotoKind]] = {
    DocumentType.NATIONAL_ID: _BOTH_SIDES,
    DocumentType.DRIVING_PERMIT: _BOTH_SIDES,
    DocumentType.REFUGEE_ID: _BOTH_SIDES,
    # Only the photo page.
    DocumentType.PASSPORT: frozenset({PhotoKind.FRONT, PhotoKind.SELFIE}),
}


def required_photos(document_type: DocumentType) -> frozenset[PhotoKind]:
    return _REQUIRED_PHOTOS[document_type]


def always_needs_person(document_type: DocumentType) -> bool:
    """Refugee IDs are not in any automatic lookup, so a person always checks them."""
    return document_type is DocumentType.REFUGEE_ID


def status_on_submit(document_type: DocumentType, *, automatic_checks: bool) -> CheckStatus:
    """Where a submitted check goes first: to the provider, or straight to the review queue."""
    if automatic_checks and not always_needs_person(document_type):
        return CheckStatus.SUBMITTED
    return CheckStatus.NEEDS_REVIEW


COUNTRY = re.compile(r"[A-Z]{2}")
MINIMUM_AGE = 18
MAX_NAME_LENGTH = 100
_NAME = re.compile(r"[^\W\d_]+(?:[ '\-.][^\W\d_]+)*\.?")
_DOCUMENT_NUMBER = re.compile(r"[A-Z0-9]{5,32}")
_TIN = re.compile(r"[0-9]{10}")

DetailField = Literal[
    "surname", "given_names", "document_number", "date_of_birth", "document_expires_on", "tin"
]


@dataclass(frozen=True, slots=True)
class IdentityDetails:
    """What the client confirmed, as printed on the document."""

    surname: str
    given_names: str
    document_number: str
    date_of_birth: date
    document_expires_on: date
    tin: str | None = None

    def normalised(self) -> IdentityDetails:
        return IdentityDetails(
            surname=" ".join(self.surname.split()),
            given_names=" ".join(self.given_names.split()),
            document_number=re.sub(r"[\s-]", "", self.document_number).upper(),
            date_of_birth=self.date_of_birth,
            document_expires_on=self.document_expires_on,
            tin=re.sub(r"\s", "", self.tin) or None if self.tin else None,
        )


@dataclass(frozen=True, slots=True)
class DetailProblem:
    field: DetailField
    code: str


def detail_problems(details: IdentityDetails, today: date) -> list[DetailProblem]:
    """Every problem with already-normalised details. An empty list means they can be saved."""
    problems: list[DetailProblem] = []
    for field in ("surname", "given_names"):
        value: str = getattr(details, field)
        if len(value) > MAX_NAME_LENGTH or _NAME.fullmatch(value) is None:
            problems.append(DetailProblem(field, "invalid_name"))
    if _DOCUMENT_NUMBER.fullmatch(details.document_number) is None:
        problems.append(DetailProblem("document_number", "invalid_document_number"))
    if details.date_of_birth > today:
        problems.append(DetailProblem("date_of_birth", "in_the_future"))
    elif _years_between(details.date_of_birth, today) < MINIMUM_AGE:
        problems.append(DetailProblem("date_of_birth", "under_age"))
    if details.document_expires_on < today:
        problems.append(DetailProblem("document_expires_on", "document_expired"))
    if details.tin is not None and _TIN.fullmatch(details.tin) is None:
        problems.append(DetailProblem("tin", "invalid_tin"))
    return problems


def _years_between(earlier: date, later: date) -> int:
    before_birthday = (later.month, later.day) < (earlier.month, earlier.day)
    return later.year - earlier.year - (1 if before_birthday else 0)


def missing_for_submission(
    document_type: DocumentType,
    photos: frozenset[PhotoKind],
    *,
    details_confirmed: bool,
) -> list[str]:
    """What still stops the client submitting: photo kinds, then `details`."""
    missing = [kind.value for kind in PhotoKind if kind in required_photos(document_type) - photos]
    if not details_confirmed:
        missing.append("details")
    return missing


def retake_problem(document_type: DocumentType, photos: frozenset[PhotoKind]) -> str | None:
    """Why staff cannot ask for these photos again, or None if they can."""
    if not photos:
        return "no_photos_named"
    if not photos <= required_photos(document_type):
        return "photo_not_part_of_document"
    return None


def photo_matches_type(content_type: str, data: bytes) -> bool:
    """Whether the file starts the way a file of the claimed type does.

    The type is the client's claim. Checking the first bytes keeps anything else, such as a
    page a browser would run, out of the bucket under an image's name.
    """
    if content_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False
