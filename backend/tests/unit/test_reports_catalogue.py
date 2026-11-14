"""Which reports each role downloads, the periods each takes, and how a file is written."""

from datetime import date

import pytest

from app.modules.identity.domain.permissions import PERMISSIONS, permissions_for
from app.modules.reports.domain.catalogue import (
    MAX_ROWS,
    REPORTS,
    BadPeriod,
    Table,
    TooManyRows,
    cell,
    check_period,
    default_period,
    file_name,
    may_download,
    to_csv,
)
from app.modules.reports.domain.complaints import Complaint, lines
from app.shared.money import Currency, Money

TODAY = date(2026, 11, 14)


def keys(role: str) -> list[str]:
    return [r.key for r in REPORTS.values() if may_download(r, permissions_for(role))]  # type: ignore[arg-type]


def test_every_report_names_a_real_permission() -> None:
    assert all(report.permission in PERMISSIONS for report in REPORTS.values())


def test_each_role_downloads_the_reports_its_screens_show() -> None:
    assert keys("admin") == list(REPORTS)
    assert keys("finance") == [
        "daily_position",
        "deposits_and_withdrawals",
        "interest_and_tax",
        "maturity_ladder",
        "client_statements",
    ]
    assert keys("compliance") == ["identity_and_risk", "client_statements", "complaints_quarterly"]
    assert "daily_position" not in keys("operations")
    # Support has no Reports screen.
    assert keys("support") == []


@pytest.mark.parametrize(
    ("kind", "date_from", "date_to", "code"),
    [
        ("day", date(2026, 11, 13), date(2026, 11, 14), "one_day_only"),
        ("days", date(2026, 10, 1), date(2026, 11, 1), "too_many_days"),
        ("days", date(2026, 11, 15), date(2026, 11, 15), "period_in_future"),
        ("days", date(2026, 11, 10), date(2026, 11, 9), "days_backwards"),
        ("month", date(2026, 10, 2), date(2026, 10, 31), "whole_month_only"),
        ("month", date(2026, 10, 1), date(2026, 10, 30), "whole_month_only"),
        ("quarter", date(2026, 8, 1), date(2026, 10, 31), "whole_quarter_only"),
        ("ahead", date(2026, 11, 14), date(2027, 2, 14), "too_many_days"),
        ("now", date(2026, 11, 13), date(2026, 11, 13), "today_only"),
    ],
)
def test_a_period_that_does_not_fit_is_refused(
    kind: str, date_from: date, date_to: date, code: str
) -> None:
    with pytest.raises(BadPeriod) as refused:
        check_period(kind, date_from, date_to, today=TODAY)  # type: ignore[arg-type]
    assert refused.value.code == code


def test_the_offered_period_always_fits() -> None:
    for report in REPORTS.values():
        first, last = default_period(report.period, TODAY)
        check_period(report.period, first, last, today=TODAY)
    assert default_period("month", TODAY) == (date(2026, 10, 1), date(2026, 10, 31))
    assert default_period("quarter", TODAY) == (date(2026, 7, 1), date(2026, 9, 30))
    assert default_period("quarter", date(2026, 2, 3)) == (date(2025, 10, 1), date(2025, 12, 31))
    assert default_period("day", TODAY) == (date(2026, 11, 13), date(2026, 11, 13))


def test_cells_are_safe_and_amounts_exact() -> None:
    assert cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert cell(Money(-1500, Currency.UGX)) == "-1500"
    assert cell(Money(1234, Currency.USD)) == "12.34"
    assert (cell(None), cell(True), cell(7), cell(TODAY)) == ("", "yes", "7", "2026-11-14")


def test_a_file_has_its_header_and_refuses_too_many_rows() -> None:
    table = Table(("A", "B"), [(1, "x")])
    assert to_csv(table) == "A,B\r\n1,x\r\n"
    with pytest.raises(TooManyRows):
        to_csv(Table(("A",), [(n,) for n in range(MAX_ROWS + 1)]))
    with pytest.raises(ValueError):
        Table(("A", "B"), [(1,)])


def test_file_names_say_the_report_currency_and_period() -> None:
    assert (
        file_name("daily_position", "UGX", TODAY, TODAY)
        == "finvest-daily-position-ugx-2026-11-14.csv"
    )
    assert (
        file_name("complaints_quarterly", None, date(2026, 7, 1), date(2026, 9, 30))
        == "finvest-complaints-quarterly-2026-07-01-to-2026-09-30.csv"
    )


def test_the_quarters_complaints_by_category() -> None:
    first, last = date(2026, 7, 1), date(2026, 9, 30)
    found = lines(
        [
            # Received and resolved in the quarter.
            Complaint("payments", date(2026, 7, 6), date(2026, 7, 13), date(2026, 7, 8), "upheld"),
            # Received before the quarter, resolved in it, after its deadline.
            Complaint(
                "payments", date(2026, 6, 25), date(2026, 7, 2), date(2026, 7, 7), "not_upheld"
            ),
            # Still open at the end, past its deadline.
            Complaint("service", date(2026, 9, 1), date(2026, 9, 8), None, None),
            # Received after the quarter: not counted.
            Complaint("service", date(2026, 10, 1), date(2026, 10, 8), None, None),
        ],
        first=first,
        last=last,
    )
    by = {line.category: line for line in found}
    assert found[-1].category == "all"
    payments = by["payments"]
    assert (payments.received, payments.resolved, payments.upheld, payments.not_upheld) == (
        1,
        2,
        1,
        1,
    )
    assert (payments.past_deadline, payments.open_at_end) == (1, 0)
    # Two and eight working days.
    assert payments.average_days == "5.0"
    assert (by["service"].received, by["service"].open_at_end, by["service"].past_deadline) == (
        1,
        1,
        1,
    )
    assert by["fees"].average_days == ""
    assert by["all"].received == 2
