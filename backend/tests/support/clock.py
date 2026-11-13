"""Deterministic clock and async barrier for foundation tests."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass
class FakeClock:
    """A UTC clock that advances only when a test asks it to."""

    current: datetime = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, **duration: float) -> datetime:
        self.current += timedelta(**duration)
        return self.current


class AsyncBarrier:
    """A reusable barrier for tests that coordinate independent tasks."""

    def __init__(self, parties: int) -> None:
        if parties < 2:
            raise ValueError("a barrier needs at least two parties")
        self._parties = parties
        self._active = 0
        self._arrived = 0
        self._broken = False
        self._generation = 0
        self._condition = asyncio.Condition()

    async def wait(self) -> int:
        async with self._condition:
            self._active += 1
            if self._broken:
                self._active -= 1
                raise RuntimeError("barrier is broken; call reset before reusing it")
            try:
                generation = self._generation
                self._arrived += 1
                position = self._parties - self._arrived
                if self._arrived == self._parties:
                    self._arrived = 0
                    self._generation += 1
                    self._condition.notify_all()
                    return 0
                try:
                    await self._condition.wait_for(lambda: self._generation != generation)
                except asyncio.CancelledError:
                    self._arrived = 0
                    self._generation += 1
                    self._broken = True
                    self._condition.notify_all()
                    raise
                if self._broken:
                    raise RuntimeError("barrier was cancelled")
                return position
            finally:
                self._active -= 1

    async def reset(self) -> None:
        async with self._condition:
            if not self._broken or self._arrived or self._active:
                raise RuntimeError("barrier can only reset after cancellation")
            self._arrived = 0
            self._broken = False
            self._generation += 1
            self._condition.notify_all()
