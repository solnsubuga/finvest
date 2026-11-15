"""The addresses delivery events reach on the API: Resend's for email, at
`POST /webhooks/resend`, and Africa's Talking's for text messages, at
`POST /provider-callbacks/sms/africastalking/{token}`.

Nobody is signed in here. A request is believed only when its Svix signature checks out with
`RESEND_HOOK_SECRET`. The route saves the event once and answers at once; the worker does the
rest. An event of a type we do not act on, such as an open or a click, is answered and dropped.

Africa's Talking signs nothing, so its report is believed only when the token in the address
matches `FINVEST_SMS_AFRICASTALKING_CALLBACK_TOKEN`, and the worker applies it only to a text
Africa's Talking sent for us under that message id.
"""

import hmac

import structlog
from fastapi import APIRouter, Request, Response
from pydantic import SecretStr

from app.infrastructure.database import Database
from app.infrastructure.email.webhooks import InvalidSignature, WebhookVerifier
from app.modules.notifications import email_delivery, sms_delivery
from app.modules.notifications.domain.email_events import UnreadableEvent, read_event
from app.modules.notifications.domain.sms_reports import UnreadableReport, read_report
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, ErrorEnvelope

# Resend's events are a few kilobytes. A bounce quotes the receiving server's reply.
MAX_EVENT_BYTES = 64 * 1024
# A delivery report is a handful of short form fields.
MAX_REPORT_BYTES = 4 * 1024

logger = structlog.get_logger()


async def _read_body(request: Request, limit: int) -> bytes:
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise ApplicationError("payload_too_large")
    return body


def create_sms_router(callback_token: SecretStr | None) -> APIRouter:
    router = APIRouter(prefix="/provider-callbacks/sms", tags=["sms delivery"])

    @router.post(
        "/africastalking/{token}",
        status_code=204,
        responses={
            404: {"model": ErrorEnvelope},
            413: {"model": ErrorEnvelope},
            422: {"model": ErrorEnvelope},
        },
    )
    async def africastalking_report(token: str, request: Request) -> Response:
        # A wrong token looks the same as no address at all.
        if callback_token is None or not hmac.compare_digest(
            token.encode(), callback_token.get_secret_value().encode()
        ):
            raise ApplicationError("not_found")
        body = await _read_body(request, MAX_REPORT_BYTES)
        try:
            report = read_report(body, request.headers.get("content-type"))
        except UnreadableReport:
            logger.warning("sms_report_unreadable")
            raise ApplicationError("validation_failed") from None
        if report is None:
            return Response(status_code=204)
        database: Database = request.app.state.database
        clock: Clock = request.app.state.clock
        async with database.transaction() as session:
            await sms_delivery.record_report(session, report, clock.now())
        return Response(status_code=204)

    return router


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
        body = await _read_body(request, MAX_EVENT_BYTES)
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
