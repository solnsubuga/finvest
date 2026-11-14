"""Where screening reads who to screen: the identity check (kyc module)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.sealing import Sealer
from app.modules.kyc import service as kyc
from app.modules.screening.domain.provider import ScreeningSubject
from app.modules.screening.jobs import ClientPager, SubjectReader


def subjects_from_identity_checks(sealer: Sealer) -> SubjectReader:
    async def read(session: AsyncSession, client_id: str) -> ScreeningSubject | None:
        found = await kyc.screening_subject(session, sealer, client_id)
        if found is None:
            return None
        return ScreeningSubject(
            surname=found.surname,
            given_names=found.given_names,
            date_of_birth=found.date_of_birth,
            document_country=found.document_country.upper(),
        )

    return read


def approved_clients() -> ClientPager:
    async def page(session: AsyncSession, after: str | None, limit: int) -> list[str]:
        return await kyc.approved_client_ids(session, after=after, limit=limit)

    return page
