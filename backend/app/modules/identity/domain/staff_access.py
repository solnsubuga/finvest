"""Who may reach the console's addresses through Cloudflare Access.

Cloudflare Access stands in front of the console and, before launch, the client app. It lets in
the addresses on a Zero Trust list. The team's approved changes decide who belongs on it: every
member of staff who is invited or active, and nobody who is deactivated.

The list is brought to that state, never sent a blind command. Each attempt asks Cloudflare
whether the address is on the list, changes it only if it is not where it should be, and asks
again. So an attempt that timed out is simply run again: it starts by finding out what happened.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.modules.identity.domain.team import StaffStatus


class AccessState(StrEnum):
    # A change was approved and the worker has not finished bringing the list in line.
    WAITING = "waiting"
    # On the list: Cloudflare lets them through to the console.
    IN_ACCESS = "in_access"
    # Off the list.
    REMOVED = "removed"
    # Cloudflare refused, or every attempt failed. Staff can ask for it to be tried again.
    FAILED = "failed"
    # The worker has no Cloudflare account, list or token, so nothing was sent.
    NOT_SET_UP = "not_set_up"


def belongs_on_list(status: StaffStatus) -> bool:
    """Invited staff need Access to open their set-up link, so they belong on it too."""
    return status != "disabled"


def settled_state(on_list: bool) -> AccessState:
    return AccessState.IN_ACCESS if on_list else AccessState.REMOVED


class AccessUnavailable(Exception):
    """Cloudflare could not be asked or did not answer. Trying again is safe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AccessRefused(Exception):
    """Cloudflare answered and will not do it, such as a token without the right permission."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StaffAccessList(Protocol):
    """The Zero Trust email list behind the console's Access policy."""

    async def contains(self, email: str) -> bool: ...

    async def add(self, email: str) -> None: ...

    async def remove(self, email: str) -> None: ...


@dataclass(frozen=True, slots=True)
class Target:
    """What the list should say about one member of staff, read when the attempt starts."""

    staff_id: str
    email: str
    on_list: bool
