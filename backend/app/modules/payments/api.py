"""The address a payment provider calls back. Nobody is signed in here.

A callback is a hint, never proof. This route checks its signature, records it once and queues
a job that reads the real status from the provider. It changes no deposit and reads none.
"""

from fastapi import APIRouter, Request, Response

from app.infrastructure.database import Database
from app.modules.payments import service
from app.modules.payments.domain.provider import CollectionsProvider, InvalidNotice
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, ErrorEnvelope

MAX_NOTICE_BYTES = 16 * 1024


def create_router(provider: CollectionsProvider | None) -> APIRouter:
    router = APIRouter(prefix="/provider-callbacks", tags=["payments"])

    @router.post(
        "/collections/{provider_name}",
        status_code=204,
        responses={403: {"model": ErrorEnvelope}, 404: {"model": ErrorEnvelope}},
    )
    async def collection_notice(provider_name: str, request: Request) -> Response:
        if provider is None or provider_name != provider.name:
            raise ApplicationError("not_found")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_NOTICE_BYTES:
                raise ApplicationError("payload_too_large")
        try:
            notice = provider.read_notice(dict(request.headers), body)
        except InvalidNotice:
            raise ApplicationError("forbidden") from None
        database: Database = request.app.state.database
        clock: Clock = request.app.state.clock
        try:
            async with database.transaction() as session:
                await service.record_notice(session, provider.name, notice, clock)
        except service.UnusableNotice:
            raise ApplicationError("validation_failed") from None
        return Response(status_code=204)

    return router
