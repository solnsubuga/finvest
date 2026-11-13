"""Turn every failure into the one error envelope. Nothing a caller sent is echoed back."""

import traceback

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.request_id import current_request_id
from app.shared.errors import (
    MESSAGES,
    STATUS,
    ApplicationError,
    ErrorCode,
    ErrorDetail,
    ErrorEnvelope,
    FieldProblem,
)

_BY_STATUS: dict[int, ErrorCode] = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    429: "rate_limited",
    503: "unavailable",
}
_MAX_FIELDS = 100


def envelope(
    code: ErrorCode,
    *,
    fields: list[FieldProblem] | None = None,
    retry_after_seconds: int | None = None,
) -> JSONResponse:
    body = ErrorEnvelope(
        request_id=current_request_id(),
        error=ErrorDetail(code=code, message=MESSAGES[code], fields=fields or []),
    )
    headers = {"Retry-After": str(retry_after_seconds)} if retry_after_seconds else None
    return JSONResponse(body.model_dump(mode="json"), status_code=STATUS[code], headers=headers)


async def _application_error(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, ApplicationError)
    structlog.get_logger().info("request_refused", error_code=error.code)
    return envelope(error.code, fields=error.fields, retry_after_seconds=error.retry_after_seconds)


async def _validation_error(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)
    # Only where and what kind: `input`, `ctx` and `msg` can all contain the submitted value.
    fields = [
        FieldProblem(
            field_path=[part for part in problem["loc"] if isinstance(part, str | int)][:32],
            code=str(problem["type"])[:64],
        )
        for problem in error.errors()[:_MAX_FIELDS]
    ]
    structlog.get_logger().info("request_refused", error_code="validation_failed")
    return envelope("validation_failed", fields=fields)


async def _http_error(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, StarletteHTTPException)
    code = _BY_STATUS.get(error.status_code)
    if code is None:
        code = "internal_error" if error.status_code >= 500 else "validation_failed"
    return envelope(code)


class UnexpectedErrorMiddleware:
    """Answer an unhandled exception with the envelope instead of Starlette's plain 500.

    It sits inside the request ID middleware, so the response and the log line carry the ID.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def track(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, receive, track)
        except Exception as error:
            # The exception type and traceback locate the fault. Its message is left out because
            # driver and validation messages can quote values.
            structlog.get_logger().error(
                "unhandled_error",
                error_code="internal_error",
                error_type=type(error).__name__,
                where=_where(error),
            )
            if started:
                raise
            await envelope("internal_error")(scope, receive, send)


def _where(error: BaseException) -> str:
    frames = traceback.extract_tb(error.__traceback__)
    return f"{frames[-1].filename}:{frames[-1].lineno}" if frames else "unknown"


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApplicationError, _application_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
