"""Primitives shared by wire contracts. No HTTP or persistence dependencies."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.shared.push_ids import PUSH_ID_PATTERN


class Contract(BaseModel):
    """Base for every request and response body.

    Strict, so an amount sent as `"1000"` or `1000.0` is refused instead of coerced: money is an
    integer in the smallest currency unit and nothing at the boundary may round it. Unknown
    fields are refused, and validation errors never echo what was submitted.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
        hide_input_in_errors=True,
        validate_default=True,
    )


# Resource IDs are case-sensitive; never strip or lowercase them at a boundary.
PushId = Annotated[
    str,
    StringConstraints(strict=True, min_length=20, max_length=20, pattern=PUSH_ID_PATTERN),
]
