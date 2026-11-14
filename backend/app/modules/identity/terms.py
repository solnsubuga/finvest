"""Which terms and privacy notice each client agreed to, and when.

A client agrees at sign-up, or when they claim an account moved over from paper records. The
API records the version that is current at that moment, in the same transaction that creates
or claims the account, so no account exists without its agreement. A record is never changed
or deleted. See docs/documents.md#terms-you-accepted.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.domain.terms import CURRENT_VERSIONS, AcceptedVia, TermsDocument
from app.shared.clock import Clock
from app.shared.push_ids import push_id


@dataclass(frozen=True, slots=True)
class Acceptance:
    document: TermsDocument
    version: str
    accepted_at: datetime
    via: AcceptedVia


_RECORD = text(
    "INSERT INTO identity_terms_acceptances (id, client_id, document, version, via, accepted_at) "
    "VALUES (:id, :client_id, :document, :version, :via, :now)"
)


async def record_current(
    session: AsyncSession, client_id: str, via: AcceptedVia, now: datetime, clock: Clock
) -> None:
    """Record that the client agreed to the current terms and privacy notice at `now`."""
    for document, version in CURRENT_VERSIONS.items():
        await session.execute(
            _RECORD,
            {
                "id": push_id(clock),
                "client_id": client_id,
                "document": document,
                "version": version,
                "via": via,
                "now": now,
            },
        )


async def acceptances(session: AsyncSession, client_id: str) -> list[Acceptance]:
    """Everything the client agreed to, newest first."""
    rows = await session.execute(
        text(
            "SELECT document, version, accepted_at, via FROM identity_terms_acceptances "
            "WHERE client_id = :client_id ORDER BY accepted_at DESC, document"
        ),
        {"client_id": client_id},
    )
    return [Acceptance(row.document, row.version, row.accepted_at, row.via) for row in rows]
