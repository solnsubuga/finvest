"""Whether a client is closing their account, for the modules that start money coming in.

This file imports nothing from other modules, so investments, payments, goals and automatic
investing can call it without a cycle through the closures service, which reads their counts.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.errors import ApplicationError, FieldProblem

_PENDING = text("SELECT closure_pending(:client_id)")


async def closing(session: AsyncSession, client_id: str) -> bool:
    """True while the client's request to close waits for staff, and once it closed.

    Holds a share lock on that request until the caller's transaction ends, so an approval in
    progress finishes first and the answer is then true.
    """
    return bool(await session.scalar(_PENDING, {"client_id": client_id}))


async def refuse_if_closing(session: AsyncSession, client_id: str) -> None:
    """Refuse anything new while the account is being closed.

    The client may cancel their request to start again. The refusal names the field `account`
    with the code `closing`, so a screen can say so.
    """
    if await closing(session, client_id):
        raise ApplicationError(
            "conflict", fields=[FieldProblem(field_path=["account"], code="closing")]
        )
