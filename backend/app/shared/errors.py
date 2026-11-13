"""One error envelope with stable codes. Messages are fixed text, never submitted values."""

from typing import Annotated, Literal

from pydantic import Field

from app.shared.contracts import Contract, PushId

ErrorCode = Literal[
    "validation_failed",
    "not_found",
    "unauthorized",
    "forbidden",
    "conflict",
    "idempotency_key_required",
    "idempotency_key_reused",
    "method_not_allowed",
    "payload_too_large",
    "rate_limited",
    "unavailable",
    "internal_error",
]

STATUS: dict[ErrorCode, int] = {
    "validation_failed": 422,
    "not_found": 404,
    "unauthorized": 401,
    "forbidden": 403,
    "conflict": 409,
    "idempotency_key_required": 428,
    "idempotency_key_reused": 422,
    "method_not_allowed": 405,
    "payload_too_large": 413,
    "rate_limited": 429,
    "unavailable": 503,
    "internal_error": 500,
}

MESSAGES: dict[ErrorCode, str] = {
    "validation_failed": "The request is not valid.",
    "not_found": "Not found.",
    "unauthorized": "Sign in to continue.",
    "forbidden": "You are not allowed to do this.",
    "conflict": "The request conflicts with the current state.",
    "idempotency_key_required": "This request needs an Idempotency-Key header.",
    "idempotency_key_reused": "This idempotency key was already used for a different request.",
    "method_not_allowed": "Method not allowed.",
    "payload_too_large": "The request is too large.",
    "rate_limited": "Too many requests. Try again later.",
    "unavailable": "The service is temporarily unavailable.",
    "internal_error": "Something went wrong on our side.",
}


class FieldProblem(Contract):
    """Where a request was wrong and why, without the value that was sent."""

    field_path: Annotated[list[str | int], Field(max_length=32)]
    code: Annotated[str, Field(min_length=1, max_length=64)]


class ErrorDetail(Contract):
    code: ErrorCode
    message: Annotated[str, Field(min_length=1, max_length=256)]
    fields: Annotated[list[FieldProblem], Field(max_length=100)]


class ErrorEnvelope(Contract):
    request_id: PushId
    error: ErrorDetail


class ApplicationError(Exception):
    """Raised by services and turned into the envelope at the HTTP edge."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        fields: list[FieldProblem] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code: ErrorCode = code
        self.fields = fields or []
        self.retry_after_seconds = retry_after_seconds
