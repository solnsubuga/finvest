"""Staff names by ID, for console screens that only hold IDs.

Many staff routes return who proposed or decided something as a staff ID, because their
modules do not read identity tables. Every member of staff holds `overview.view`, so every
screen can turn those IDs into names here. Only names are returned: no email, role or status.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.infrastructure.database import Database
from app.modules.identity.api import require_permission
from app.modules.identity.service import StaffSession, active_staff_names, staff_names
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope

MAX_IDS = 100

router = APIRouter(
    prefix="/staff",
    tags=["staff people"],
    responses={401: {"model": ErrorEnvelope}, 403: {"model": ErrorEnvelope}},
)
routers = [router]

AnyStaff = Annotated[StaffSession, Depends(require_permission("overview.view"))]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


Db = Annotated[Database, Depends(_database)]


class PersonView(Contract):
    id: PushId
    name: str


class PeopleList(Contract):
    people: list[PersonView]


def _people(names: dict[str, str]) -> PeopleList:
    return PeopleList(people=[PersonView(id=key, name=value) for key, value in names.items()])


@router.get("/people")
async def staff_people(
    staff: AnyStaff,
    database: Db,
    ids: Annotated[list[PushId] | None, Query(max_length=MAX_IDS)] = None,
) -> PeopleList:
    """The names of the members of staff with these IDs, or of every active one if none are given.

    An ID with no account is left out.
    """
    async with database.actor_transaction(staff.actor) as session:
        names = await staff_names(session, ids) if ids else await active_staff_names(session)
    return _people(names)
