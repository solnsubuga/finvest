"""The client downloads a copy of what Finvest holds about them, as one JSON file."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.infrastructure.database import Database
from app.infrastructure.sealing import Sealer
from app.modules.client_data.export import client_copy
from app.modules.identity.api import CurrentClient
from app.modules.identity.passkeys import RECENT_SIGN_IN
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, ErrorEnvelope


def create_router(sealer: Sealer | None) -> APIRouter:
    router = APIRouter(
        prefix="/client",
        tags=["client data"],
        responses={code: {"model": ErrorEnvelope} for code in (401, 403, 503)},
    )

    def database(request: Request) -> Database:
        held: Database = request.app.state.database
        return held

    def clock(request: Request) -> Clock:
        held: Clock = request.app.state.clock
        return held

    Db = Annotated[Database, Depends(database)]
    Time = Annotated[Clock, Depends(clock)]

    @router.get(
        "/data-export",
        response_class=Response,
        responses={200: {"content": {"application/json": {}}}},
        summary="Download a copy of my data",
    )
    async def export(client: CurrentClient, db: Db, now: Time) -> Response:
        """Everything Finvest holds about the signed-in client, as an attachment.

        It holds the client's ID number and details, so it needs a sign-in from the last few
        minutes. Each download is written to the audit trail.
        """
        if sealer is None:
            raise ApplicationError("unavailable")
        if now.now() - client.signed_in_at > RECENT_SIGN_IN:
            raise ApplicationError("sign_in_again")
        async with db.actor_transaction(client.actor) as session:
            copy = await client_copy(session, sealer, client.actor, now)
        day = now.now().date().isoformat()
        return Response(
            content=json.dumps(copy, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="finvest-my-data-{day}.json"',
                "Cache-Control": "no-store",
            },
        )

    return router
