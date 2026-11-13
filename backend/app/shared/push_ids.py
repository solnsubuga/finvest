"""Firebase-style push IDs: 20 characters, 8 for the millisecond and 12 random.

References that cannot be counted or guessed in sequence. They are identifiers, never
credentials: a push ID proves nothing about who holds it and reveals its creation time.

The shared instance serialises generation within a process. Database uniqueness and bounded
retries protect independent processes.
"""

from __future__ import annotations

import os
import re
import secrets
from datetime import UTC, datetime
from threading import Lock
from weakref import WeakSet

from app.shared.clock import Clock, SystemClock

PUSH_ID_PATTERN = r"^[-0-9A-Z_a-z]{20}$"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_instances: WeakSet[PushID] = WeakSet()

PUSH_CHARS = "-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz"


def validate_push_id(value: str) -> str:
    """Validate without coercion or case/whitespace normalisation."""
    if not isinstance(value, str) or re.fullmatch(PUSH_ID_PATTERN, value) is None:
        raise ValueError("Push IDs must be 20 case-sensitive ASCII alphabet characters")
    return value


def _milliseconds(clock: Clock) -> int:
    value = clock.now()
    if value.utcoffset() is None:
        raise ValueError("Push ID clocks must return timezone-aware datetimes")
    elapsed = value - _EPOCH
    return (elapsed.days * 86400 + elapsed.seconds) * 1000 + elapsed.microseconds // 1000


class PushID:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._reset()
        _instances.add(self)

    def _reset(self) -> None:
        self._last_time: int | None = None
        self._random: list[int] = []
        self._lock = Lock()

    def next_id(self, clock: Clock | None = None) -> str:
        with self._lock:
            now = _milliseconds(clock or self._clock)
            if not 0 <= now < 64**8:
                raise ValueError("Push ID timestamp must fit in 48 bits")
            if self._last_time is not None:
                now = max(now, self._last_time)
            if now == self._last_time:
                digits = self._random.copy()
                for i in range(11, -1, -1):
                    if digits[i] < 63:
                        digits[i] += 1
                        break
                    digits[i] = 0
                else:
                    raise ValueError("Push ID random suffix exhausted for this millisecond")
            else:
                digits = [secrets.randbelow(64) for _ in range(12)]
            self._random = digits
            self._last_time = now
            timestamp = ["-"] * 8
            for i in range(7, -1, -1):
                timestamp[i] = PUSH_CHARS[now % 64]
                now //= 64
            return "".join(timestamp) + "".join(PUSH_CHARS[digit] for digit in digits)


def _after_fork() -> None:
    # A parent thread may hold a lock at fork. Replace locks before child callers run,
    # and draw a fresh suffix rather than sharing the parent's next sequence value.
    for instance in list(_instances):
        instance._reset()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)

_generator = PushID(SystemClock())


def push_id(clock: Clock | None = None) -> str:
    return _generator.next_id(clock)
