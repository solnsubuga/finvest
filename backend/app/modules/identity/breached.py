"""The breach list check behind new passwords (docs/decisions.md, 2026-11-15).

A new password passes the built-in rules first (`domain.passwords`). Then, outside any
database transaction, it is looked up in a breach list. An answer of None means the list
could not be asked, and the password stands on the built-in rules alone.
"""

from typing import Protocol


class BreachedPasswords(Protocol):
    async def seen(self, password: str) -> bool | None: ...


class NoBreachList:
    """No list is configured: every password stands on the built-in rules."""

    async def seen(self, password: str) -> bool | None:
        return None
