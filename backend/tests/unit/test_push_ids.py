from datetime import UTC, datetime

import pytest

from app.shared.push_ids import PUSH_CHARS, PushID, push_id, validate_push_id
from tests.support.clock import FakeClock


def test_ids_are_twenty_characters_from_the_alphabet() -> None:
    value = push_id()
    assert len(value) == 20
    assert set(value) <= set(PUSH_CHARS)
    assert validate_push_id(value) == value


def test_ids_in_one_millisecond_are_unique_and_ascending(fake_clock: FakeClock) -> None:
    generator = PushID(fake_clock)
    ids = [generator.next_id() for _ in range(1000)]
    assert len(set(ids)) == 1000
    assert ids == sorted(ids)
    assert len({value[:8] for value in ids}) == 1


def test_later_ids_sort_after_earlier_ones(fake_clock: FakeClock) -> None:
    generator = PushID(fake_clock)
    first = generator.next_id()
    fake_clock.advance(milliseconds=1)
    assert generator.next_id() > first


def test_a_clock_that_goes_backwards_does_not_reorder_ids(fake_clock: FakeClock) -> None:
    generator = PushID(fake_clock)
    first = generator.next_id()
    fake_clock.advance(seconds=-5)
    assert generator.next_id() > first


def test_the_timestamp_prefix_encodes_the_clock() -> None:
    clock = FakeClock(datetime(1970, 1, 1, 0, 0, 0, 1000, tzinfo=UTC))
    assert PushID(clock).next_id()[:8] == "-------0"


def test_naive_clocks_are_refused() -> None:
    class Naive:
        def now(self) -> datetime:
            return datetime(2026, 1, 1)

    with pytest.raises(ValueError, match="timezone-aware"):
        PushID(Naive()).next_id()


@pytest.mark.parametrize(
    "value", ["", "short", "a" * 21, "a" * 19 + " ", "a" * 19 + "!", " " + "a" * 19, "é" * 20]
)
def test_malformed_ids_are_refused(value: str) -> None:
    with pytest.raises(ValueError, match="Push IDs"):
        validate_push_id(value)
