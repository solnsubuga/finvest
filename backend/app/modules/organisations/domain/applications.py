"""What a company or group account needs before Finvest opens it. Pure: no I/O.

A company or group is applied for by one of its people, who must be an approved personal client.
They add the other people who will sign for it, each also an approved personal client, and the
documents that prove the organisation exists and names those people. One member of staff checks
the application and a different one approves it, which issues the organisation's own account
number (prefix 30 for a company, 40 for a group). See docs/organisations.md.

The organisation cannot invest yet: whether pooled money from a group is a collective scheme is
open with counsel (docs/decisions.md).
"""

import re
from dataclasses import dataclass
from enum import StrEnum

MAX_NAME = 120
MAX_ROLE = 60
MAX_NOTE = 500
MAX_SIGNATORIES = 10
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
DOCUMENT_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png"})
# Uganda Registration Services Bureau numbers are digits, sometimes with a letter prefix and
# slashes or dashes. Loose on purpose: staff check the certificate itself.
_REGISTRATION = re.compile(r"[A-Z0-9][A-Z0-9/ -]{2,38}[A-Z0-9]")


class Kind(StrEnum):
    COMPANY = "company"
    GROUP = "group"  # a savings group, club, association or SACCO


class Status(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    CHECKED = "checked"
    APPROVED = "approved"
    REJECTED = "rejected"


class SignatoryStatus(StrEnum):
    INVITED = "invited"
    JOINED = "joined"
    DECLINED = "declined"
    REMOVED = "removed"


class DocumentKind(StrEnum):
    CERTIFICATE_OF_INCORPORATION = "certificate_of_incorporation"
    BOARD_RESOLUTION = "board_resolution"
    CONSTITUTION = "constitution"
    GROUP_RESOLUTION = "group_resolution"


# The documents each kind needs, in the order the app asks for them.
DOCUMENTS: dict[Kind, tuple[DocumentKind, ...]] = {
    Kind.COMPANY: (DocumentKind.CERTIFICATE_OF_INCORPORATION, DocumentKind.BOARD_RESOLUTION),
    Kind.GROUP: (DocumentKind.CONSTITUTION, DocumentKind.GROUP_RESOLUTION),
}
# A company may have one director. A group always needs two people, so no one person holds a
# group's money alone.
MIN_SIGNATORIES: dict[Kind, int] = {Kind.COMPANY: 1, Kind.GROUP: 2}
MIN_APPROVALS: dict[Kind, int] = {Kind.COMPANY: 1, Kind.GROUP: 2}


class Problem(StrEnum):
    """Why an application cannot be handed in yet, in the order the client should fix them."""

    SIGNATORIES_WAITING = "signatories_waiting"
    TOO_FEW_SIGNATORIES = "too_few_signatories"
    APPROVALS_MORE_THAN_SIGNATORIES = "approvals_more_than_signatories"
    APPROVALS_TOO_FEW = "approvals_too_few"
    DOCUMENTS_MISSING = "documents_missing"
    REGISTRATION_NUMBER_MISSING = "registration_number_missing"


@dataclass(frozen=True, slots=True)
class Readiness:
    kind: Kind
    registration_number: str | None
    approvals_needed: int
    # Counted with the applicant, who joins when they apply.
    joined: int
    invited: int
    documents: frozenset[DocumentKind]


def problems(found: Readiness) -> list[Problem]:
    """Every reason the application cannot be handed in yet. Empty when it can."""
    out: list[Problem] = []
    if found.invited:
        out.append(Problem.SIGNATORIES_WAITING)
    if found.joined < MIN_SIGNATORIES[found.kind]:
        out.append(Problem.TOO_FEW_SIGNATORIES)
    if found.approvals_needed > found.joined:
        out.append(Problem.APPROVALS_MORE_THAN_SIGNATORIES)
    if found.approvals_needed < MIN_APPROVALS[found.kind]:
        out.append(Problem.APPROVALS_TOO_FEW)
    if not set(DOCUMENTS[found.kind]) <= found.documents:
        out.append(Problem.DOCUMENTS_MISSING)
    if found.kind is Kind.COMPANY and not found.registration_number:
        out.append(Problem.REGISTRATION_NUMBER_MISSING)
    return out


def clean_name(value: str) -> str | None:
    """The organisation's name with spacing tidied, or None if it is empty or too long."""
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > MAX_NAME:
        return None
    return cleaned


def clean_registration(value: str | None) -> str | None:
    """The registration number in capitals, or None when none was given.

    Raises ValueError when one was given that cannot be a registration number.
    """
    if value is None or not value.strip():
        return None
    cleaned = " ".join(value.upper().split())
    if not _REGISTRATION.fullmatch(cleaned):
        raise ValueError("not a registration number")
    return cleaned


def clean_role(value: str) -> str | None:
    """What a person is in the organisation, such as Director or Treasurer."""
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > MAX_ROLE:
        return None
    return cleaned


def document_matches_type(content_type: str, data: bytes) -> bool:
    """Whether the file starts the way a file of the claimed type does."""
    if content_type == "application/pdf":
        return data.startswith(b"%PDF-")
    if content_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    return False
