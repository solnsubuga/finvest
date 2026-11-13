from typing import Literal

import structlog
from fastapi import APIRouter, Request

from app.infrastructure.database import Database
from app.infrastructure.operations import operations_signals
from app.shared.clock import Clock
from app.shared.contracts import Contract
from app.shared.errors import ApplicationError, ErrorEnvelope

logger = structlog.get_logger()
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


@router.get("/operations", responses={503: {"model": ErrorEnvelope}})
async def operations(request: Request) -> Health:
    """Background work is moving. 503 while any job is overdue, stalled or recently failed, or a
    recurring run is late. An uptime alert watches this.

    The answer is only ok or not, because anyone may call it. Which count is above zero goes to
    the logs, where the person who gets the alert looks next.
    """
    database: Database = request.app.state.database
    clock: Clock = request.app.state.clock
    try:
        signals = await operations_signals(database, clock.now())
    except Exception as error:  # the alert must fire when the check itself cannot run
        logger.warning("operations_unchecked", error_type=type(error).__name__)
        raise ApplicationError("unavailable") from None
    raised = {name: total for name, total in signals.items() if total > 0}
    for name, total in sorted(raised.items()):
        logger.warning("operations_signal", signal=name, total=total)
    if raised:
        raise ApplicationError("unavailable")
    return Health(status="ok")
