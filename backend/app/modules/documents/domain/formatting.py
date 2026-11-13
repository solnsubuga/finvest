"""How figures, dates and references are written on a statement, a receipt and their emails.

Pure. Amounts are written the way every Finvest message writes them: `UGX 1,000,000`, `$5.74`.
Nothing here rounds: an amount is written from its exact count of the smallest unit.
"""

from datetime import date, datetime

from app.shared.business_day import KAMPALA
from app.shared.money import Currency, Money

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_SYMBOLS = {Currency.UGX: "UGX ", Currency.USD: "$"}


def money(value: Money) -> str:
    digits = value.currency.minor_digits
    whole, fraction = divmod(abs(value.minor), 10**digits)
    sign = "-" if value.minor < 0 else ""
    cents = f".{fraction:0{digits}d}" if digits else ""
    return f"{sign}{_SYMBOLS[value.currency]}{whole:,}{cents}"


def signed(value: Money) -> str:
    """An amount with its direction: `+UGX 1,000` in, `-UGX 1,000` out."""
    return ("+" if value.minor > 0 else "") + money(value)


def day(value: date) -> str:
    """`13 Nov 2026`."""
    return f"{value.day} {MONTHS[value.month - 1][:3]} {value.year}"


def long_day(value: date) -> str:
    """`13 November 2026`."""
    return f"{value.day} {MONTHS[value.month - 1]} {value.year}"


def month(value: date) -> str:
    """`October 2026`."""
    return f"{MONTHS[value.month - 1]} {value.year}"


def moment(value: datetime) -> str:
    """`13 Nov 2026, 15:32` in Kampala time. Refuses a time with no zone."""
    if value.utcoffset() is None:
        raise ValueError("Times must be timezone-aware")
    local = value.astimezone(KAMPALA)
    return f"{day(local.date())}, {local:%H:%M}"


def account_number(value: str) -> str:
    """`1001 004 827`: the ten digits grouped 4, 3, 3, as everywhere else."""
    if len(value) != 10 or not value.isdigit():
        raise ValueError("An account number is ten digits")
    return f"{value[:4]} {value[4:7]} {value[7:]}"
