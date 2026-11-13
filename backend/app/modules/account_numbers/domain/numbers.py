"""The account number format. Pure: no I/O, and the random source is injected.

Ten digits: two for the account type, seven random, and a Luhn check digit over the first nine.
Storage and matching use the ten digits with no spaces; display groups them 4, 3, 3.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

RANDOM_DIGITS = 7
RANDOM_SPACE = 10**RANDOM_DIGITS
_TEN_DIGITS = re.compile(r"[0-9]{10}")
# What a person may type or paste: the digits with spaces between the groups.
_TYPED = re.compile(r"[0-9]+( [0-9]+)*")


class AccountType(StrEnum):
    """The value is the two-digit prefix."""

    PERSONAL = "10"
    JOINT = "20"
    COMPANY = "30"
    GROUP = "40"  # groups and SACCOs


class InvalidAccountNumber(ValueError):
    """Not a Finvest account number: wrong length, unknown type or failed check digit."""


def luhn_check_digit(payload: str) -> str:
    """The digit that makes `payload + digit` pass the Luhn check banks use for card numbers."""
    if not payload or not payload.isascii() or not payload.isdigit():
        raise ValueError("The Luhn payload must be ASCII digits")
    total = 0
    for position, character in enumerate(reversed(payload)):
        digit = int(character)
        if position % 2 == 0:  # every second digit, starting next to the check digit
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


@dataclass(frozen=True, slots=True)
class AccountNumber:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or _TEN_DIGITS.fullmatch(self.value) is None:
            raise InvalidAccountNumber("An account number is exactly 10 digits")
        if self.value[:2] not in _TYPE_PREFIXES:
            raise InvalidAccountNumber("Unknown account type")
        if luhn_check_digit(self.value[:9]) != self.value[9]:
            raise InvalidAccountNumber("The check digit does not match")

    @classmethod
    def compose(cls, account_type: AccountType, random_part: int) -> AccountNumber:
        if type(random_part) is not int or not 0 <= random_part < RANDOM_SPACE:
            raise ValueError("The random part is an integer of at most 7 digits")
        payload = f"{AccountType(account_type).value}{random_part:0{RANDOM_DIGITS}d}"
        return cls(payload + luhn_check_digit(payload))

    @classmethod
    def parse(cls, typed: str) -> AccountNumber:
        """Accept the stored form or the displayed form with single spaces between groups."""
        if not isinstance(typed, str) or _TYPED.fullmatch(typed.strip()) is None:
            raise InvalidAccountNumber("An account number is exactly 10 digits")
        return cls(typed.strip().replace(" ", ""))

    @property
    def account_type(self) -> AccountType:
        return AccountType(self.value[:2])

    @property
    def display(self) -> str:
        return f"{self.value[:4]} {self.value[4:7]} {self.value[7:]}"

    def __str__(self) -> str:
        return self.value


_TYPE_PREFIXES = frozenset(member.value for member in AccountType)


def candidate(account_type: AccountType, randbelow: Callable[[int], int]) -> AccountNumber:
    """A well-formed number from an injected random source such as `secrets.randbelow`.

    Whether it is free is the database's decision, not this function's.
    """
    return AccountNumber.compose(account_type, randbelow(RANDOM_SPACE))
