"""The counts beside the console menu: how much work waits on each screen. Pure: the service
reads the sources, this decides which screens a reader gets a count for and how far it counts.

A count is a nudge, not a register. Each screen still lists its own work and checks its own
permissions; a reader who may not open a screen gets no count for it, not even zero.
"""

from collections.abc import Iterable
from typing import Literal

from app.modules.identity.domain.permissions import Permission

# The console's menu paths that can carry a count.
Screen = Literal[
    "review-queue",
    "identity-checks",
    "maturities",
    "support",
    "complaints",
    "reconciliation",
    "rates",
    "compliance",
]

# Needed to get a count, the same permission that opens the screen.
VIEW: dict[Screen, Permission] = {
    "review-queue": "review_queue.view",
    "identity-checks": "identity_checks.view",
    "maturities": "maturities.view",
    "support": "support.view",
    "complaints": "complaints.view",
    "reconciliation": "reconciliation.view",
    "rates": "rates.view",
    "compliance": "compliance.view",
}

# Counting stops here. The menu shows "99+" past 99, and nobody needs the exact figure there.
CAP = 100


def counted_screens(permissions: Iterable[Permission]) -> list[Screen]:
    """The screens the reader gets a count for, in menu order."""
    held = set(permissions)
    return [screen for screen, permission in VIEW.items() if permission in held]


def menu_counts(found: dict[Screen, int], permissions: Iterable[Permission]) -> dict[Screen, int]:
    """One count for every screen the reader may open, capped. A screen the reader may not
    open is dropped even if a caller counted it; one nobody counted is zero."""
    counts: dict[Screen, int] = {}
    for screen in counted_screens(permissions):
        value = found.get(screen, 0)
        if value < 0:
            raise ValueError("A count is never negative")
        counts[screen] = min(value, CAP)
    return counts
