"""The address Resend's delivery events reach, through the router at hooks.fidiba.com.

Nobody is signed in here. A request is believed only when its Svix signature checks out with
`RESEND_HOOK_SECRET`. The route saves the event once and answers at once; the worker does the
rest. An event of a type we do not act on, such as an open or a click, is answered and dropped.
"""

import structlog
from fastapi import APIRouter, Request, Response

from app.infrastructure.database import Database
from app.infrastructure.email.webhooks import InvalidSignature, WebhookVerifier
from app.modules.notifications import email_delivery
from app.modules.notifications.domain.email_events import UnreadableEvent, read_event
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, ErrorEnvelope

# Resend's events are a few kilobytes. A bounce quotes the receiving server's reply.
MAX_EVENT_BYTES = 64 * 1024

logger = structlog.get_logger()


def create_router(verifier: WebhookVerifier | None) -> APIRouter:
    router = APIRouter(prefix="/webhooks", tags=["email delivery"])

    @router.post(
        "/resend",
        status_code=204,
        responses={
            401: {"model": ErrorEnvelope},
            404: {"model": ErrorEnvelope},
            413: {"model": ErrorEnvelope},
        },
    )
    async def resend_event(request: Request) -> Response:
        if verifier is None:
            raise ApplicationError("not_found")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_EVENT_BYTES:
                raise ApplicationError("payload_too_large")
        clock: Clock = request.app.state.clock
        try:
            svix_id = verifier.verify(request.headers, body, clock.now())
        except InvalidSignature:
            raise ApplicationError("unauthorized") from None
        try:
            event = read_event(body)
        except UnreadableEvent:
            # Signed by the router, so it is ours, but nothing in it can be used.
            logger.warning("email_event_unreadable")
            raise ApplicationError("validation_failed") from None
        if event is None:
            return Response(status_code=204)
        database: Database = request.app.state.database
        async with database.transaction() as session:
            await email_delivery.record_event(
                session, svix_id, event, clock.now(), request.app.state.email_hash_key
            )
        return Response(status_code=204)

    return router
