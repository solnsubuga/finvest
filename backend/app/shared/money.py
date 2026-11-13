"""Money as an integer count of the smallest currency unit. Pure: no I/O, no floats.

Whole shillings for UGX, cents for USD. `Money` refuses floats and booleans at construction,
never mixes currencies, and stays inside the range of a PostgreSQL `bigint`, so a value that
exists in Python can always be stored. The only way to apply a rate is `mul_ratio`, which takes
the rate as two integers and makes the caller choose the rounding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

MAX_MINOR = 2**63 - 1
_MAJOR = re.compile(r"(-?)([0-9]{1,18})(?:\.([0-9]+))?")


class Currency(StrEnum):
    UGX = "UGX"
    USD = "USD"

    @property
    def minor_digits(self) -> int:
        """Decimal places between the major unit and the smallest unit."""
        return _MINOR_DIGITS[self]


_MINOR_DIGITS = {Currency.UGX: 0, Currency.USD: 2}


class Rounding(StrEnum):
    """How `mul_ratio` settles a result that falls between two minor units."""

    DOWN = "down"  # towards zero
    UP = "up"  # away from zero
    HALF_UP = "half_up"  # nearest; ties away from zero
    HALF_EVEN = "half_even"  # nearest; ties to the even unit


class MoneyError(ValueError):
    """An amount that is not a valid integer amount of money."""


class CurrencyMismatch(MoneyError):
    """Two amounts in different currencies were combined or compared."""


@dataclass(frozen=True, slots=True)
class Money:
    minor: int
    currency: Currency

    def __post_init__(self) -> None:
        # `type(...) is int` on purpose: bool is a subclass of int, and floats must never pass.
        if type(self.minor) is not int:
            raise MoneyError("Money amounts are integers in the smallest currency unit")
        if not isinstance(self.currency, Currency):
            raise MoneyError("Unknown currency")
        if not -MAX_MINOR <= self.minor <= MAX_MINOR:
            raise MoneyError("Money amount is out of range")

    @classmethod
    def zero(cls, currency: Currency) -> Money:
        return cls(0, currency)

    @classmethod
    def from_major(cls, text: str, currency: Currency) -> Money:
        """Parse a plain decimal string such as "1500" or "12.34". Never accepts a float.

        A string with more decimal places than the currency has is refused, not rounded.
        """
        if not isinstance(text, str):
            raise MoneyError("Amounts are parsed from strings, never from floats")
        match = _MAJOR.fullmatch(text)
        if match is None:
            raise MoneyError("Amount must be a plain decimal number")
        sign, whole, fraction = match.group(1), match.group(2), match.group(3) or ""
        digits = currency.minor_digits
        if len(fraction) > digits:
            raise MoneyError(f"{currency} amounts have at most {digits} decimal places")
        minor = int(whole) * 10**digits + int(fraction.ljust(digits, "0") or "0")
        return cls(-minor if sign else minor, currency)

    def to_major(self) -> str:
        """The exact decimal string, such as "1500" for UGX or "12.34" for USD."""
        digits = self.currency.minor_digits
        sign = "-" if self.minor < 0 else ""
        whole, fraction = divmod(abs(self.minor), 10**digits)
        return f"{sign}{whole}" + (f".{fraction:0{digits}d}" if digits else "")

    @property
    def is_zero(self) -> bool:
        return self.minor == 0

    @property
    def is_positive(self) -> bool:
        return self.minor > 0

    @property
    def is_negative(self) -> bool:
        return self.minor < 0

    def __add__(self, other: Money) -> Money:
        return Money(self.minor + self._same(other).minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        return Money(self.minor - self._same(other).minor, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.minor, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.minor), self.currency)

    def __lt__(self, other: Money) -> bool:
        return self.minor < self._same(other).minor

    def __le__(self, other: Money) -> bool:
        return self.minor <= self._same(other).minor

    def __gt__(self, other: Money) -> bool:
        return self.minor > self._same(other).minor

    def __ge__(self, other: Money) -> bool:
        return self.minor >= self._same(other).minor

    def mul_ratio(self, numerator: int, denominator: int, rounding: Rounding) -> Money:
        """This amount times `numerator / denominator`, in exact integer arithmetic.

        A rate of 12.5% a year for 91 days is `mul_ratio(125 * 91, 1000 * 365, rounding)`.
        Rounding is applied to the magnitude, so a negative amount rounds as its mirror image.
        """
        if type(numerator) is not int or type(denominator) is not int:
            raise MoneyError("Ratios are given as two integers, never as a float")
        if denominator <= 0:
            raise MoneyError("The denominator must be positive")
        if not isinstance(rounding, Rounding):
            raise MoneyError("Unknown rounding")
        product = self.minor * numerator
        quotient, remainder = divmod(abs(product), denominator)
        if remainder and _rounds_away(rounding, quotient, remainder, denominator):
            quotient += 1
        return Money(-quotient if product < 0 else quotient, self.currency)

    def _same(self, other: Money) -> Money:
        if not isinstance(other, Money):
            raise MoneyError("Money combines only with Money")
        if other.currency is not self.currency:
            raise CurrencyMismatch(f"Cannot combine {self.currency} with {other.currency}")
        return other


def _rounds_away(rounding: Rounding, quotient: int, remainder: int, denominator: int) -> bool:
    twice = remainder * 2
    if rounding is Rounding.DOWN:
        return False
    if rounding is Rounding.UP:
        return True
    if rounding is Rounding.HALF_UP:
        return twice >= denominator
    return twice > denominator or (twice == denominator and quotient % 2 == 1)
