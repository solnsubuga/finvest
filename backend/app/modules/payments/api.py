"""The address a payment provider calls back. Nobody is signed in here.

A callback is a hint, never proof. This route checks its signature, records it once and queues
a job that reads the real status from the provider. It changes no deposit and reads none.
"""

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.infrastructure.database import Database
from app.modules.payments import cards, service
from app.modules.payments.domain.provider import (
    CardCollectionsProvider,
    CollectionsProvider,
    InvalidNotice,
)
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, ErrorEnvelope

MAX_NOTICE_BYTES = 16 * 1024


def create_router(
    provider: CollectionsProvider | None,
    card_provider: CardCollectionsProvider | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/provider-callbacks", tags=["payments"])

    @router.api_route(
        "/cards/{provider_name}/{token}",
        methods=["GET", "POST"],
        responses={403: {"model": ErrorEnvelope}, 404: {"model": ErrorEnvelope}},
        include_in_schema=False,
    )
    async def card_notice(provider_name: str, token: str, request: Request) -> Response:
        """A card provider says an order changed. Pesapal does not sign this, so the address
        carries a long random token, and the notice is only ever a reason to read the status."""
        if card_provider is None or provider_name != card_provider.name:
            raise ApplicationError("not_found")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_NOTICE_BYTES:
                raise ApplicationError("payload_too_large")
        try:
            notice = card_provider.read_notice(token, dict(request.query_params), body)
        except InvalidNotice:
            raise ApplicationError("forbidden") from None
        database: Database = request.app.state.database
        clock: Clock = request.app.state.clock
        # Pesapal repeats a notification until it is answered. Repeats in the same minute are
        # one notice; a later one is a new reason to read.
        keyed = type(notice)(
            f"{notice.notice_id}:{clock.now().strftime('%Y%m%d%H%M')}",
            notice.request_id,
            notice.provider_reference,
        )
        try:
            async with database.transaction() as session:
                await service.record_notice(
                    session, card_provider.name, keyed, clock, verify_kind=cards.VERIFY_KIND
                )
        except service.UnusableNotice:
            raise ApplicationError("validation_failed") from None
        kind = notice.notice_id.rsplit(":", 1)[-1]
        return JSONResponse(
            {
                "orderNotificationType": kind,
                "orderTrackingId": notice.provider_reference,
                "orderMerchantReference": notice.request_id,
                "status": 200,
            }
        )

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
