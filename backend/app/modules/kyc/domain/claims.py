"""Claim checks: the photos a client carried over from Finvest 1.0 takes when they claim.

A carried-over identity check has no photos. At the claim the client takes a selfie and a photo
of the front of the document on file, and one member of staff says whether they match each other
and the details carried over. PostgreSQL holds the same table (migration 0053).
"""

from enum import StrEnum

from app.modules.kyc.domain.checks import PhotoKind


class ClaimCheckStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    AWAITING_CLIENT = "awaiting_client"
    MATCHED = "matched"
    NOT_MATCHED = "not_matched"


CLAIM_PHOTOS = frozenset({PhotoKind.FRONT, PhotoKind.SELFIE})
DECIDED = frozenset({ClaimCheckStatus.MATCHED, ClaimCheckStatus.NOT_MATCHED})

_MOVES: dict[str, dict[ClaimCheckStatus, frozenset[ClaimCheckStatus]]] = {
    "client": {
        ClaimCheckStatus.DRAFT: frozenset({ClaimCheckStatus.NEEDS_REVIEW}),
        ClaimCheckStatus.AWAITING_CLIENT: frozenset({ClaimCheckStatus.NEEDS_REVIEW}),
    },
    "staff": {
        ClaimCheckStatus.NEEDS_REVIEW: frozenset(
            {
                ClaimCheckStatus.MATCHED,
                ClaimCheckStatus.NOT_MATCHED,
                ClaimCheckStatus.AWAITING_CLIENT,
            }
        ),
    },
}


def can_move(actor: str, current: ClaimCheckStatus, target: ClaimCheckStatus) -> bool:
    """Whether this kind of actor may move a claim check from `current` to `target`."""
    return target in _MOVES.get(actor, {}).get(current, frozenset())


def retake_problem(photos: frozenset[PhotoKind]) -> str | None:
    """Why staff cannot ask for these photos again, or None if they can."""
    if not photos:
        return "no_photos_named"
    if not photos <= CLAIM_PHOTOS:
        return "photo_not_part_of_claim"
    return None
