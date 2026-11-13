import random
from dataclasses import dataclass, field
from datetime import timedelta


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff with jitter in the upper half of each delay.

    The delay before attempt n+1 lies in [d/2, d] where d = min(cap, base * 2**(n-1)).
    """

    base: timedelta = timedelta(seconds=5)
    cap: timedelta = timedelta(minutes=15)
    rng: random.Random = field(default_factory=random.Random, compare=False)

    def __post_init__(self) -> None:
        if self.base <= timedelta(0) or self.cap < self.base:
            raise ValueError("Retry delays need 0 < base <= cap")

    def ceiling(self, attempt: int) -> timedelta:
        """The longest delay `delay(attempt)` can return."""
        if attempt < 1:
            raise ValueError("Attempts are numbered from 1")
        # Cap the exponent first so large attempt numbers cannot overflow.
        ceiling: timedelta = min(self.cap, self.base * 2 ** min(attempt - 1, 32))
        return ceiling

    def delay(self, attempt: int) -> timedelta:
        jittered: timedelta = self.ceiling(attempt) * (0.5 + self.rng.random() / 2)
        return jittered
