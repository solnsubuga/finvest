"""The client's route to see which terms and privacy notice they agreed to."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.infrastructure.database import Database
from app.modules.identity import terms
from app.modules.identity.api import CurrentClient
from app.modules.identity.domain.terms import AcceptedVia, TermsDocument
from app.shared.contracts import Contract
from app.shared.errors import ErrorEnvelope

router = APIRouter(
    prefix="/client/terms-acceptances",
    tags=["terms"],
    responses={401: {"model": ErrorEnvelope}},
)


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


Db = Annotated[Database, Depends(_database)]


class AcceptanceView(Contract):
    """One document the client agreed to. The website keeps every version's PDF at
    `/legal/<document>-<version>.pdf`."""

    document: TermsDocument
    version: str
    accepted_at: datetime
    via: AcceptedVia


class AcceptancesView(Contract):
    items: list[AcceptanceView]


@router.get("")
async def my_acceptances(client: CurrentClient, database: Db) -> AcceptancesView:
    """The terms and privacy notice versions the client agreed to, newest first."""
    async with database.actor_transaction(client.actor) as session:
        found = await terms.acceptances(session, client.client_id)
    return AcceptancesView(
        items=[
            AcceptanceView(
                document=item.document,
                version=item.version,
                accepted_at=item.accepted_at,
                via=item.via,
            )
            for item in found
        ]
    )
