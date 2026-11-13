from datetime import UTC, datetime

import pytest

from app.modules.audit.domain.search import (
    AuditFilter,
    Cursor,
    InvalidFilter,
    csv_cell,
    decode_cursor,
    encode_cursor,
)

ID = "abcdefghij0123456789"


def test_a_cursor_round_trips() -> None:
    cursor = Cursor(datetime(2026, 11, 13, 9, 30, 1, 5, tzinfo=UTC), ID)
    assert decode_cursor(encode_cursor(cursor)) == cursor


@pytest.mark.parametrize(
    "value",
    [
        "",
        "!!!",
        "bm90IGEgY3Vyc29y",
        encode_cursor(Cursor(datetime(2026, 1, 1, tzinfo=UTC), ID))[:-3],
        "x" * 201,
    ],
)
def test_a_damaged_cursor_is_refused(value: str) -> None:
    with pytest.raises(InvalidFilter):
        decode_cursor(value)


@pytest.mark.parametrize("action", ["rates.rate_changed", "rates.", "identity.team.", "a.b.c"])
def test_whole_actions_and_prefixes_are_accepted(action: str) -> None:
    assert AuditFilter(action=action).action == action


@pytest.mark.parametrize("action", ["rates", "Rates.x", "rates.%", "rates..", ".rates", "a_b"])
def test_anything_else_is_refused(action: str) -> None:
    with pytest.raises(InvalidFilter):
        AuditFilter(action=action)


def test_only_a_prefix_is_a_prefix() -> None:
    assert AuditFilter(action="rates.").is_prefix
    assert not AuditFilter(action="rates.changed").is_prefix
    assert not AuditFilter().is_prefix


def test_times_must_carry_a_zone_and_run_forwards() -> None:
    with pytest.raises(InvalidFilter):
        AuditFilter(since=datetime(2026, 1, 1))
    later, earlier = datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(InvalidFilter):
        AuditFilter(since=later, until=earlier)
    assert AuditFilter(since=earlier, until=later).until == later


@pytest.mark.parametrize("field", ["actor_id", "resource_type", "resource_id", "correlation_id"])
def test_identifiers_must_look_like_identifiers(field: str) -> None:
    with pytest.raises(InvalidFilter):
        AuditFilter(**{field: "has space"})  # type: ignore[arg-type]


@pytest.mark.parametrize("start", ["=", "+", "-", "@", "\t", "\r"])
def test_a_cell_that_would_run_as_a_formula_is_kept_as_text(start: str) -> None:
    assert csv_cell(f"{start}SUM(A1)") == f"'{start}SUM(A1)"


def test_an_ordinary_cell_is_unchanged() -> None:
    assert csv_cell("Board memo 12") == "Board memo 12"
    assert csv_cell("") == ""
