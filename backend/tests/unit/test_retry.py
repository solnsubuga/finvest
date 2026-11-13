import random
from datetime import timedelta

import pytest

from app.infrastructure.jobs.errors import JobError, PermanentJobError, UncertainOutcomeError
from app.infrastructure.jobs.retry import RetryPolicy


def test_delays_double_up_to_the_cap_with_jitter_in_the_upper_half() -> None:
    policy = RetryPolicy(base=timedelta(seconds=5), cap=timedelta(minutes=1), rng=random.Random(7))
    ceilings = [policy.ceiling(attempt).total_seconds() for attempt in range(1, 7)]
    assert ceilings == [5, 10, 20, 40, 60, 60]
    for attempt in range(1, 40):
        delay = policy.delay(attempt)
        assert policy.ceiling(attempt) / 2 <= delay <= policy.ceiling(attempt)


def test_huge_attempt_numbers_do_not_overflow() -> None:
    assert RetryPolicy().ceiling(10_000) == timedelta(minutes=15)


def test_bad_policies_and_attempts_are_refused() -> None:
    with pytest.raises(ValueError, match="base <= cap"):
        RetryPolicy(base=timedelta(seconds=10), cap=timedelta(seconds=5))
    with pytest.raises(ValueError, match="numbered from 1"):
        RetryPolicy().ceiling(0)


def test_error_messages_are_bounded_and_uncertain_is_permanent() -> None:
    assert JobError("x" * 900).safe_message == "x" * 500
    assert JobError().safe_message is None
    assert JobError(code="c" * 100).code == "c" * 64
    uncertain = UncertainOutcomeError("no reply from the provider")
    assert isinstance(uncertain, PermanentJobError)
    assert uncertain.code == "uncertain_outcome"
