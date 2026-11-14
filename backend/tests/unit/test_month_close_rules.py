"""When a month can be closed, and the fingerprint of the books it signs."""

from datetime import date

import pytest

from app.modules.finance.domain.month_close import (
    CannotClose,
    SignedLine,
    balances,
    check_can_close,
    digest,
)
from app.modules.finance.month_close import next_month

OCT = date(2026, 10, 1)
CASH = SignedLine("UGX", "bank.trust", "asset", 0, 500, 0, 500)
OWED = SignedLine("UGX", "client.holding", "liability", 0, 0, 500, 500)


def refused(**changes: object) -> str:
    arguments: dict[str, object] = {
        "today": date(2026, 11, 14),
        "closed_through": date(2026, 11, 13),
        "last_closed_month": date(2026, 9, 1),
        "lines": (CASH, OWED),
    }
    arguments.update(changes)
    month = changes.pop("month", OCT)
    arguments.pop("month", None)
    try:
        check_can_close(month, **arguments)  # type: ignore[arg-type]
    except CannotClose as refusal:
        return refusal.code
    return "ok"


def test_the_next_month_with_its_days_closed_and_balanced_books_can_close() -> None:
    assert refused() == "ok"
    assert refused(last_closed_month=None) == "ok"
    assert refused(closed_through=date(2026, 10, 31)) == "ok"


def test_each_reason_a_month_cannot_close() -> None:
    assert refused(month=date(2026, 11, 1)) == "month_not_ended"
    assert refused(today=date(2026, 10, 31)) == "month_not_ended"
    assert refused(last_closed_month=OCT) == "month_already_closed"
    assert refused(last_closed_month=date(2026, 8, 1)) == "close_months_in_order"
    assert refused(closed_through=date(2026, 10, 30)) == "days_not_closed"
    assert refused(closed_through=None) == "days_not_closed"
    assert refused(lines=(CASH,)) == "books_do_not_balance"


def test_december_is_followed_by_january() -> None:
    assert (
        refused(
            month=date(2027, 1, 1),
            today=date(2027, 2, 2),
            closed_through=date(2027, 1, 31),
            last_closed_month=date(2026, 12, 1),
        )
        == "ok"
    )
    assert next_month(date(2026, 12, 1), date(2027, 2, 2)) == date(2027, 1, 1)
    assert next_month(None, date(2027, 1, 14)) == date(2026, 12, 1)
    assert next_month(None, date(2026, 11, 14)) == date(2026, 10, 1)


def test_the_digest_ignores_order_and_changes_with_any_figure() -> None:
    assert digest(OCT, [CASH, OWED]) == digest(OCT, [OWED, CASH])
    moved = SignedLine("UGX", "bank.trust", "asset", 0, 501, 0, 501)
    assert digest(OCT, [CASH, OWED]) != digest(OCT, [moved, OWED])
    assert digest(OCT, [CASH, OWED]) != digest(date(2026, 9, 1), [CASH, OWED])


def test_books_balance_in_each_currency_on_its_own() -> None:
    dollars = SignedLine("USD", "bank.trust", "asset", 0, 7, 0, 7)
    assert balances([CASH, OWED])
    assert not balances([CASH, OWED, dollars])
    assert balances([])


def test_a_month_is_named_by_its_first_day() -> None:
    with pytest.raises(ValueError):
        check_can_close(
            date(2026, 10, 2),
            today=date(2026, 11, 14),
            closed_through=None,
            last_closed_month=None,
            lines=(),
        )
