"""When a scheduled report is due, which period it covers, and the email that says it is ready."""

from datetime import date, timedelta

import pytest

from app.modules.reports.domain.catalogue import REPORTS, ReportKey, check_period
from app.modules.reports.domain.schedules import (
    ALLOWED,
    FREQUENCIES,
    allowed,
    due,
    is_due,
    period,
    period_label,
    ready_email,
)

# A Monday that is also the first day of a quarter.
QUARTER_MONDAY = date(2029, 1, 1)


def test_each_frequency_is_due_on_its_own_day() -> None:
    days = [date(2026, 1, 1) + timedelta(days=n) for n in range(730)]
    assert all(is_due("daily", day) for day in days)
    assert {day.weekday() for day in days if is_due("weekly", day)} == {0}
    assert {day.day for day in days if is_due("monthly", day)} == {1}
    quarterly = [day for day in days if is_due("quarterly", day)]
    assert [(d.month, d.day) for d in quarterly[:4]] == [(1, 1), (4, 1), (7, 1), (10, 1)]
    assert len(quarterly) == 8


@pytest.mark.parametrize("key", list(REPORTS))
def test_every_period_sent_is_one_the_report_accepts(key: ReportKey) -> None:
    for frequency in allowed(key):
        for offset in range(400):
            today = date(2026, 1, 1) + timedelta(days=offset)
            found = due(key, frequency, today)
            if found is None:
                continue
            check_period(REPORTS[key].period, *found, today=today)


def test_a_frequency_the_report_does_not_take_is_refused() -> None:
    for key, report in REPORTS.items():
        for frequency in FREQUENCIES:
            if frequency in ALLOWED[report.period]:
                continue
            with pytest.raises(ValueError):
                period(key, frequency, QUARTER_MONDAY)


def test_the_periods_are_the_ones_just_ended() -> None:
    today = date(2026, 11, 16)  # a Monday
    assert period("daily_position", "daily", today) == (date(2026, 11, 15), date(2026, 11, 15))
    assert period("deposits_and_withdrawals", "weekly", today) == (
        date(2026, 11, 9),
        date(2026, 11, 15),
    )
    assert period("interest_and_tax", "monthly", date(2026, 12, 1)) == (
        date(2026, 11, 1),
        date(2026, 11, 30),
    )
    assert period("complaints_quarterly", "quarterly", date(2027, 1, 1)) == (
        date(2026, 10, 1),
        date(2026, 12, 31),
    )
    assert period("maturity_ladder", "weekly", today) == (today, date(2026, 12, 13))
    assert period("maturity_ladder", "daily", today) == (today, date(2026, 11, 22))
    assert period("identity_and_risk", "monthly", date(2026, 12, 1)) == (
        date(2026, 12, 1),
        date(2026, 12, 1),
    )
    assert due("interest_and_tax", "monthly", today) is None


def test_period_labels() -> None:
    assert period_label(date(2026, 11, 13), date(2026, 11, 13)) == "13 November 2026"
    assert period_label(date(2026, 10, 1), date(2026, 10, 31)) == "October 2026"
    assert period_label(date(2026, 10, 1), date(2026, 12, 31)) == "Q4 2026"
    assert period_label(date(2026, 11, 9), date(2026, 11, 15)) == "9 Nov 2026 to 15 Nov 2026"


def test_the_email_links_to_the_console_and_carries_no_file_or_figure() -> None:
    email = ready_email(
        first_name="Brian",
        key="daily_position",
        frequency="daily",
        date_from=date(2026, 11, 13),
        date_to=date(2026, 11, 13),
        currency="UGX",
        console="https://admin.finvestug.com",
    )
    link = (
        "https://admin.finvestug.com/reports?report=daily_position&from=2026-11-13"
        "&to=2026-11-13&currency=UGX"
    )
    assert email.subject == "Daily position (UGX) for 13 November 2026 is ready"
    assert link in email.text
    assert link.replace("&", "&amp;") in email.html
    assert "never attached" in email.text
    assert "Hi Brian" in email.html


def test_the_email_escapes_a_name() -> None:
    email = ready_email(
        first_name="<b>",
        key="identity_and_risk",
        frequency="weekly",
        date_from=date(2026, 11, 16),
        date_to=date(2026, 11, 16),
        currency=None,
        console="https://admin.finvestug.com",
    )
    assert "<b>," not in email.html
    assert "every Monday" in email.text
