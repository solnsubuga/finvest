from datetime import UTC, datetime, timedelta

import pytest

from app.modules.identity.domain.pins import LOCK_AFTER, is_locked, pin_problem


@pytest.mark.parametrize("pin", ["48151", "10293", "13579", "90817", "11112", "24680"])
def test_a_pin_of_five_mixed_digits_is_accepted(pin: str) -> None:
    assert pin_problem(pin) is None


@pytest.mark.parametrize("pin", ["", "1234", "123456", "12a45", "1234 ", "١٢٣٤٥", "+1234"])
def test_anything_but_five_digits_is_refused(pin: str) -> None:
    assert pin_problem(pin) == "format"


@pytest.mark.parametrize("pin", ["00000", "77777", "12345", "56789", "54321", "98765", "01234"])
def test_repeated_digits_and_straight_runs_are_too_simple(pin: str) -> None:
    assert pin_problem(pin) == "too_simple"


NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


def test_a_session_locks_after_five_unused_minutes_only_with_a_pin() -> None:
    just_before = NOW - LOCK_AFTER + timedelta(seconds=1)
    assert not is_locked(pin_set=True, last_seen_at=just_before, now=NOW)
    assert is_locked(pin_set=True, last_seen_at=NOW - LOCK_AFTER, now=NOW)
    assert not is_locked(pin_set=False, last_seen_at=NOW - timedelta(days=1), now=NOW)
