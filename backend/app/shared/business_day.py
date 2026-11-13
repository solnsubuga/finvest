"""The business day. Pure: no I/O.

Uganda keeps East Africa Time all year, so a client's "today", a value date and the day that is
closed each evening are all the Kampala calendar day. Use this wherever a moment becomes a date.
"""

from datetime import date, datetime, timedelta, timezone

KAMPALA = timezone(timedelta(hours=3), "EAT")


def business_date(moment: datetime) -> date:
    """The Kampala calendar day that a moment falls in. Refuses a time with no zone."""
    if moment.utcoffset() is None:
        raise ValueError("Times must be timezone-aware")
    return moment.astimezone(KAMPALA).date()
