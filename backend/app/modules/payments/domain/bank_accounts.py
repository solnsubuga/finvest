"""The bank account clients are told to pay into by transfer, one per currency. Pure.

Staff set it on the console's Settings screen. One person proposes the details and a different
person approves them, because an account changed by one person alone could send every client's
transfer somewhere else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_NAME = 100
MAX_BRANCH = 100
MAX_REASON = 500
# Ugandan bank account numbers are digits. Spaces and dashes are a way of writing them only.
_ACCOUNT_NUMBER = re.compile(r"[0-9]{6,20}")


class InvalidBankAccount(ValueError):
    """The details cannot be used. `problems` pairs each field with a safe code."""

    def __init__(self, problems: list[tuple[str, str]]) -> None:
        super().__init__(", ".join(f"{field}:{code}" for field, code in problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class BankAccount:
    bank_name: str
    account_name: str
    account_number: str
    branch: str | None


def checked(
    *, bank_name: str, account_name: str, account_number: str, branch: str | None
) -> BankAccount:
    """The details tidied (spaces trimmed, the number's spaces and dashes removed) or refused."""
    problems: list[tuple[str, str]] = []
    bank = " ".join(bank_name.split())
    name = " ".join(account_name.split())
    number = account_number.replace(" ", "").replace("-", "")
    place = " ".join(branch.split()) if branch is not None else ""
    if not bank:
        problems.append(("bank_name", "required"))
    elif len(bank) > MAX_NAME:
        problems.append(("bank_name", "too_long"))
    if not name:
        problems.append(("account_name", "required"))
    elif len(name) > MAX_NAME:
        problems.append(("account_name", "too_long"))
    if _ACCOUNT_NUMBER.fullmatch(number) is None:
        problems.append(("account_number", "invalid"))
    if len(place) > MAX_BRANCH:
        problems.append(("branch", "too_long"))
    if problems:
        raise InvalidBankAccount(problems)
    return BankAccount(bank, name, number, place or None)
