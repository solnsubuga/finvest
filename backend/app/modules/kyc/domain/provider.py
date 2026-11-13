"""The identity provider port, and the rule that turns its answer into a decision.

A provider looks the document up, compares the selfie with it and screens the person against
sanctions and politically exposed person lists. No provider is chosen yet (see
docs/identity-checks.md), so the port is written from what the review screen needs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from app.modules.kyc.domain.checks import DocumentType, IdentityDetails, PhotoKind


class AutomaticCheck(StrEnum):
    DOCUMENT_GENUINE = "document_genuine"  # real, readable and not expired
    SELFIE_MATCH = "selfie_match"  # a live person who is the person on the document
    SCREENING = "screening"  # sanctions and politically exposed person lists


class Verdict(StrEnum):
    PASS = "pass"  # noqa: S105 - a verdict, not a password
    FAIL = "fail"
    UNSURE = "unsure"


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    check: AutomaticCheck
    verdict: Verdict
    # A whole percentage when the provider gives one, such as a face match of 62.
    score: int | None = None

    def __post_init__(self) -> None:
        if self.score is not None and (type(self.score) is not int or not 0 <= self.score <= 100):
            raise ValueError("A score is a whole percentage from 0 to 100")


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """One submission. The key is the same on every retry of the same submission."""

    idempotency_key: str
    document_type: DocumentType
    document_country: str
    details: IdentityDetails
    # Storage keys, not bytes: the adapter fetches or signs links for what it needs.
    photos: Mapping[PhotoKind, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Accepted:
    reference: str


@dataclass(frozen=True, slots=True)
class Refused:
    """The provider will not check this submission, for example an unsupported document."""

    code: str


@dataclass(frozen=True, slots=True)
class Pending:
    pass


@dataclass(frozen=True, slots=True)
class Completed:
    outcomes: tuple[CheckOutcome, ...]


class ProviderDeferred(Exception):
    """The provider certainly did not receive the request. Sending it again later is safe."""


class ProviderUncertain(Exception):
    """The request may or may not have arrived. Ask the provider before sending again."""


class IdentityProvider(Protocol):
    name: str
    # False for a provider that checks nothing, so every submission goes to a person.
    automatic: bool

    async def submit(self, request: ProviderRequest) -> Accepted | Refused: ...

    async def find(self, idempotency_key: str) -> str | None:
        """The reference of a submission the provider already holds, or None."""
        ...

    async def result(self, reference: str) -> Pending | Completed: ...


@dataclass(frozen=True, slots=True)
class Decision:
    approve: bool
    # Why a person must look, as stable codes for the review screen.
    flags: tuple[str, ...] = ()


def decide(outcomes: tuple[CheckOutcome, ...]) -> Decision:
    """Approve only when every automatic check ran and passed. Anything else goes to a person.

    A provider never rejects a client on its own: a failed check is a reason for review.
    """
    seen = {outcome.check: outcome.verdict for outcome in outcomes}
    if len(seen) != len(outcomes):
        return Decision(approve=False, flags=("provider_result_unclear",))
    flags = [
        f"{check.value}_{'not_run' if verdict is None else verdict.value}"
        for check in AutomaticCheck
        if (verdict := seen.get(check)) is not Verdict.PASS
    ]
    return Decision(approve=not flags, flags=tuple(flags))
