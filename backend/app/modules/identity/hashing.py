"""Passwords hashed with Argon2id.

Hashing is deliberately slow and memory-hard, so it runs in a bounded thread pool and never on
the event loop. The bound also caps the memory a burst of sign-ins can take.
"""

import secrets

import anyio
from argon2 import PasswordHasher as Argon2
from argon2 import Type
from argon2.exceptions import InvalidHashError, VerificationError

# The floor is OWASP's minimum for Argon2id. Deployments use the defaults, which are stronger.
MIN_MEMORY_KIB = 19_456
MIN_TIME_COST = 2


class PasswordHasher:
    def __init__(
        self,
        *,
        memory_kib: int = 65_536,
        time_cost: int = 3,
        parallelism: int = 1,
        concurrency: int = 4,
    ) -> None:
        if memory_kib < MIN_MEMORY_KIB or time_cost < MIN_TIME_COST:
            raise ValueError("Argon2id parameters are below the minimum")
        self._argon2 = Argon2(
            time_cost=time_cost, memory_cost=memory_kib, parallelism=parallelism, type=Type.ID
        )
        self._limiter = anyio.CapacityLimiter(concurrency)
        # Checking an unknown account against a real hash keeps the response time the same as
        # for a wrong password, so timing does not reveal which numbers have accounts.
        self._decoy = self._argon2.hash(secrets.token_urlsafe(32))

    def _verify(self, password: str, hashed: str | None) -> bool:
        try:
            return self._argon2.verify(hashed or self._decoy, password) and hashed is not None
        except (VerificationError, InvalidHashError):
            return False

    async def verify(self, password: str, hashed: str | None) -> bool:
        """Whether `password` matches. `hashed=None` (no such account) costs the same and fails."""
        return await anyio.to_thread.run_sync(self._verify, password, hashed, limiter=self._limiter)

    async def hash(self, password: str) -> str:
        return await anyio.to_thread.run_sync(self._argon2.hash, password, limiter=self._limiter)

    def needs_rehash(self, hashed: str) -> bool:
        """True when the hash was made with weaker parameters than today's."""
        return self._argon2.check_needs_rehash(hashed)
