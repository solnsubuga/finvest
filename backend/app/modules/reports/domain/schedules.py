"""How often each report can be sent, which period each send covers, and the email that says it
is ready. Pure.

A schedule sends a link, never the file: the person signs in to the console and downloads the
report there, so the file only ever reaches someone whose sign-in and permissions are checked
at that moment. The email names the report and the period, and holds no figure.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal
from urllib.parse import urlencode

from app.modules.reports.domain.catalogue import (
    REPORTS,
    PeriodKind,
    ReportKey,
    month_end,
    quarter_end,
    quarter_start,
)
from app.shared import email_layout as layout

Frequency = Literal["daily", "weekly", "monthly", "quarterly"]
FREQUENCIES: tuple[Frequency, ...] = ("daily", "weekly", "monthly", "quarterly")
MAX_RECIPIENTS = 20

# What each kind of period can be sent as. A day's report goes daily; a month's monthly; a
# quarter's quarterly. A report of how things stand now can go at any of the three, and is
# read on the day the email arrives.
ALLOWED: dict[PeriodKind, tuple[Frequency, ...]] = {
    "day": ("daily",),
    "days": ("daily", "weekly", "monthly"),
    "month": ("monthly",),
    "quarter": ("quarterly",),
    "ahead": ("daily", "weekly"),
    "now": ("daily", "weekly", "monthly"),
}

# The ladder sent daily covers the coming week, and weekly the coming four weeks.
AHEAD_DAYS: dict[Frequency, int] = {"daily": 7, "weekly": 28}


def allowed(key: ReportKey) -> tuple[Frequency, ...]:
    return ALLOWED[REPORTS[key].period]


def is_due(frequency: Frequency, today: date) -> bool:
    """Daily every day, weekly on Mondays, monthly on the 1st, quarterly on the first day of
    January, April, July and October. All in Kampala."""
    if frequency == "daily":
        return True
    if frequency == "weekly":
        return today.weekday() == 0
    if frequency == "monthly":
        return today.day == 1
    return today.day == 1 and today.month in (1, 4, 7, 10)


def period(key: ReportKey, frequency: Frequency, today: date) -> tuple[date, date]:
    """The period a send on `today` covers: the one that has just ended, or for the ladder the
    days ahead, or for a report of how things stand, today."""
    kind = REPORTS[key].period
    if frequency not in ALLOWED[kind]:
        raise ValueError(f"{key} cannot be sent {frequency}")
    yesterday = today - timedelta(days=1)
    if kind == "now":
        return today, today
    if kind == "ahead":
        return today, today + timedelta(days=AHEAD_DAYS[frequency] - 1)
    if frequency == "daily":
        return yesterday, yesterday
    if frequency == "weekly":
        monday = today - timedelta(days=today.weekday() + 7)
        return monday, monday + timedelta(days=6)
    if frequency == "monthly":
        first = yesterday.replace(day=1)
        return first, month_end(first)
    first = quarter_start(yesterday)
    return first, quarter_end(first)


def due(key: ReportKey, frequency: Frequency, today: date) -> tuple[date, date] | None:
    """The period to send today, or None when this schedule sends nothing today."""
    return period(key, frequency, today) if is_due(frequency, today) else None


EVERY: dict[Frequency, str] = {
    "daily": "every day",
    "weekly": "every Monday",
    "monthly": "on the 1st of every month",
    "quarterly": "at the start of every quarter",
}


@dataclass(frozen=True, slots=True)
class ReadyEmail:
    subject: str
    html: str
    text: str


def period_label(date_from: date, date_to: date) -> str:
    if date_from == date_to:
        return f"{date_from.day} {date_from:%B %Y}"
    if date_from == quarter_start(date_from) and date_to == quarter_end(date_from):
        return f"Q{(date_from.month - 1) // 3 + 1} {date_from.year}"
    if date_from.day == 1 and date_to == month_end(date_from):
        return f"{date_from:%B %Y}"
    return f"{date_from.day} {date_from:%b %Y} to {date_to.day} {date_to:%b %Y}"


def download_link(
    console: str, key: ReportKey, date_from: date, date_to: date, currency: str | None
) -> str:
    """The console's Reports page, opened on this report and period."""
    query = {"report": key, "from": date_from.isoformat(), "to": date_to.isoformat()}
    if currency is not None:
        query["currency"] = currency
    return f"{console}/reports?{urlencode(query)}"


def ready_email(
    *,
    first_name: str,
    key: ReportKey,
    frequency: Frequency,
    date_from: date,
    date_to: date,
    currency: str | None,
    console: str,
    links: layout.Links = layout.PRODUCTION,
) -> ReadyEmail:
    report = REPORTS[key]
    label = period_label(date_from, date_to)
    named = f"{report.name}{f' ({currency})' if currency else ''}"
    link = download_link(console, key, date_from, date_to, currency)
    now = report.period == "now"
    lines = [
        f"Hi {first_name},",
        f"Your {named} report for {label} is ready to download from the console."
        if not now
        else f"Your {named} report is ready. It shows how things stand when you open it today.",
    ]
    body = (
        layout.paragraphs(lines)
        + layout.status("Report ready", named, label)
        + layout.button("Download the report", link)
        + layout.link_fallback(link)
        + layout.small(
            f"You get this {EVERY[frequency]} because a schedule sends it to you. "
            "Sign in to download it: the file is never attached to an email. To stop it, open "
            "Reports in the console and stop the schedule."
        )
    )
    subject = f"{named} for {label} is ready"
    text = (
        "\n\n".join(lines)
        + f"\n\nDownload it: {link}\n\n"
        + f"You get this {EVERY[frequency]} because a schedule sends it to you. Sign in to "
        "download it: the file is never attached to an email.\n\n" + layout.FOOTER_TEXT
    )
    return ReadyEmail(
        subject=subject,
        html=layout.page(
            preheader=f"Ready to download: {label}", title=subject, body=body, links=links
        ),
        text=text,
    )
