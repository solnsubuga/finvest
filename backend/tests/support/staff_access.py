"""A Cloudflare Access list in memory, for tests. It can answer, refuse, time out, or take an
address without showing it yet."""

from dataclasses import dataclass, field
from typing import Literal

from app.modules.identity.domain.staff_access import AccessRefused, AccessUnavailable

Mode = Literal["ok", "unavailable", "refused", "token_refused", "lagging", "duplicate_refused"]


@dataclass
class FakeAccessList:
    emails: set[str] = field(default_factory=set)
    mode: Mode = "ok"
    # Every write, in order, as ("add" | "remove", address).
    writes: list[tuple[str, str]] = field(default_factory=list)
    # How many times it was asked whether an address is on it.
    reads: int = 0

    async def contains(self, email: str) -> bool:
        self.reads += 1
        if self.mode == "unavailable":
            raise AccessUnavailable("cloudflare_unavailable")
        if self.mode == "token_refused":
            raise AccessRefused("cloudflare_token_refused")
        return email.lower() in self.emails

    async def add(self, email: str) -> None:
        self._write("add", email)

    async def remove(self, email: str) -> None:
        self._write("remove", email)

    def _write(self, action: str, email: str) -> None:
        if self.mode == "refused":
            raise AccessRefused("cloudflare_refused")
        self.writes.append((action, email))
        if self.mode == "lagging":
            return  # accepted, not showing yet
        if action == "add":
            self.emails.add(email.lower())
        else:
            self.emails.discard(email.lower())
        if self.mode == "duplicate_refused":
            raise AccessRefused("cloudflare_refused")
