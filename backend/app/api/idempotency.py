from typing import Annotated

from fastapi import Depends, Header

from app.infrastructure.idempotency import KEY_PATTERN, parse_key
from app.shared.errors import ApplicationError, FieldProblem

HEADER = "Idempotency-Key"


def _idempotency_key(
    value: Annotated[
        str | None,
        Header(
            alias=HEADER,
            description=(
                "A value the caller chooses once per intended action and sends again on every "
                f"retry of it. Pattern: `{KEY_PATTERN}`."
            ),
        ),
    ] = None,
) -> str:
    try:
        return parse_key(value)
    except ApplicationError as error:
        error.fields = [FieldProblem(field_path=["header", HEADER], code=error.code)]
        raise


IdempotencyKey = Annotated[str, Depends(_idempotency_key)]
"""Declare this parameter on every route that moves money."""
