"""Identity check fixtures: a provider that answers as told, and a client's finished draft."""

import os
import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.sealing import KEY_BYTES, Sealer
from app.modules.kyc import service as kyc
from app.modules.kyc.domain.checks import DocumentType, IdentityDetails, PhotoKind, required_photos
from app.modules.kyc.domain.provider import (
    Accepted,
    AutomaticCheck,
    CheckOutcome,
    Completed,
    Pending,
    ProviderRequest,
    Refused,
    Verdict,
)
from app.shared.actors import Actor
from app.shared.clock import Clock

SHA256 = "ab" * 32
# A fresh key for every test run. No key is ever written into the repository.
SEALER = Sealer(os.urandom(KEY_BYTES), purpose="identity")


def all_passed() -> tuple[CheckOutcome, ...]:
    return tuple(CheckOutcome(check, Verdict.PASS) for check in AutomaticCheck)


def details(document_number: str | None = None) -> IdentityDetails:
    return IdentityDetails(
        surname="NAKATO",
        given_names="Amara",
        # Unique by default: one approved client per identity document. 122 random bits in 32
        # upper-case hex characters, so no two tests share a number. Folding a push id to upper
        # case did not: ids made in the same millisecond could become the same number.
        document_number=document_number or uuid.uuid4().hex.upper(),
        date_of_birth=date(1994, 6, 22),
        document_expires_on=date(2030, 9, 18),
    )


@dataclass
class FakeProvider:
    """Runs automatic checks and returns whatever the test scripted, or raises it."""

    name: str = "fake"
    automatic: bool = True
    submit_answer: Accepted | Refused | Exception = field(
        default_factory=lambda: Accepted("fake-reference")
    )
    result_answer: Pending | Completed | Exception = field(
        default_factory=lambda: Completed(all_passed())
    )
    submitted: list[ProviderRequest] = field(default_factory=list)

    async def submit(self, request: ProviderRequest) -> Accepted | Refused:
        if isinstance(self.submit_answer, Exception):
            raise self.submit_answer
        self.submitted.append(request)
        return self.submit_answer

    async def find(self, idempotency_key: str) -> str | None:
        held = any(request.idempotency_key == idempotency_key for request in self.submitted)
        return "fake-reference" if held else None

    async def result(self, reference: str) -> Pending | Completed:
        if isinstance(self.result_answer, Exception):
            raise self.result_answer
        return self.result_answer


async def add_photos(session: AsyncSession, client: Actor, clock: Clock, *kinds: PhotoKind) -> None:
    for kind in kinds:
        await kyc.add_photo(
            session,
            client,
            kind,
            content_type="image/jpeg",
            byte_size=2048,
            sha256=SHA256,
            clock=clock,
        )


async def ready_draft(
    session: AsyncSession,
    client: Actor,
    clock: Clock,
    *,
    document_type: DocumentType = DocumentType.NATIONAL_ID,
    document_number: str | None = None,
) -> kyc.CheckView:
    """A draft with every photo and confirmed details, ready to submit."""
    await kyc.choose_document(session, SEALER, client, document_type, "UG", clock)
    await add_photos(session, client, clock, *sorted(required_photos(document_type)))
    return await kyc.confirm_details(session, SEALER, client, details(document_number), clock)
