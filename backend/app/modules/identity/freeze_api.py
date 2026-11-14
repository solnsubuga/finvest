"""The client's route to freeze their own account.

Freezing needs no fresh sign-in: someone who has lost their phone must be able to shut the
account quickly, and freezing never moves money. The session making the call ends with all the
others, so the app signs out once it has its answer.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.infrastructure.database import Database
from app.modules.identity import freezes
from app.modules.identity.api import CurrentClient
from app.shared.clock import Clock
from app.shared.contracts import Contract
from app.shared.errors import ErrorEnvelope

_REFUSALS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorEnvelope} for code in (401, 409, 422)
}
router = APIRouter(prefix="/client/account-freeze", tags=["account freeze"], responses=_REFUSALS)


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


class FreezeBody(Contract):
    reason: str | None = Field(default=None, max_length=freezes.MAX_REASON + 50)


class FreezeView(Contract):
    frozen_at: datetime


@router.post("", status_code=201)
async def freeze_account(
    body: FreezeBody, client: CurrentClient, database: Db, clock: Time
) -> FreezeView:
    """Lock the account now and end every session, this one included.

    To unfreeze, the client contacts support and two members of staff unlock it.
    """
    async with database.actor_transaction(client.actor) as session:
        found = await freezes.freeze(session, client.actor, body.reason, clock)
    return FreezeView(frozen_at=found.frozen_at)
