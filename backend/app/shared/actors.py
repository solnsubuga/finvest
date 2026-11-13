"""Who a unit of work acts for. Row policies in PostgreSQL read this, not application code."""

from dataclasses import dataclass
from typing import Literal

from app.shared.push_ids import validate_push_id

ActorKind = Literal["client", "staff", "system"]


@dataclass(frozen=True, slots=True)
class Actor:
    """Authority already derived from an authenticated session, or the system itself.

    `client` and `staff` carry the push ID of the signed-in person. `system` is a worker or
    scheduler acting with no person, and has no ID.
    """

    kind: ActorKind
    id: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "system":
            if self.id is not None:
                raise ValueError("The system actor has no ID")
        elif self.id is None:
            raise ValueError("A client or staff actor needs an ID")
        else:
            validate_push_id(self.id)


SYSTEM = Actor("system")
