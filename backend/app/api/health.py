from typing import Literal

from fastapi import APIRouter, Request

from app.infrastructure.database import Database
from app.shared.contracts import Contract
from app.shared.errors import ApplicationError, ErrorEnvelope

router = APIRouter(prefix="/health", tags=["health"])


class Health(Contract):
    status: Literal["ok"]


@router.get("/live")
async def live() -> Health:
    """The process is running. Says nothing about its dependencies."""
    return Health(status="ok")


@router.get("/ready", responses={503: {"model": ErrorEnvelope}})
async def ready(request: Request) -> Health:
    """The process can reach PostgreSQL and should receive traffic."""
    database: Database = request.app.state.database
    if not await database.ready():
        raise ApplicationError("unavailable")
    return Health(status="ok")
