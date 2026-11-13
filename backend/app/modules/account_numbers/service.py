"""Issuing account numbers inside the caller's transaction.

The clients module calls `issue_account_number` in the transaction that approves a client, so
the number and the approval commit together. The registry table is insert-only, so a number is
never issued twice and never comes back after an account closes (migration 0011).
"""

import secrets
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.account_numbers.domain.numbers import AccountNumber, AccountType, candidate
from app.shared.clock import Clock

# With ten million numbers per type a collision is rare; needing this many means the space for
# that type is close to full and someone must decide what comes next.
MAX_ATTEMPTS = 20

_CLAIM = text(
    "INSERT INTO account_numbers (number, issued_at) VALUES (:number, :issued_at) "
    "ON CONFLICT (number) DO NOTHING"
)
_EXISTS = text("SELECT EXISTS (SELECT FROM account_numbers WHERE number = :number)")


class AccountNumbersExhausted(Exception):
    """No free number was found for this account type within the attempt limit."""

    code = "account_numbers_exhausted"


async def issue_account_number(
    session: AsyncSession,
    account_type: AccountType,
    clock: Clock,
    *,
    randbelow: Callable[[int], int] = secrets.randbelow,
) -> AccountNumber:
    """Claim a new, unused account number of this type in the caller's transaction.

    The random part comes from the operating system's secure source, so numbers cannot be
    guessed or counted. If a candidate is already taken, by a committed transaction or by one
    still in flight, another is drawn.
    """
    for _ in range(MAX_ATTEMPTS):
        number = candidate(account_type, randbelow)
        claimed = await session.execute(_CLAIM, {"number": number.value, "issued_at": clock.now()})
        if claimed.rowcount == 1:  # type: ignore[attr-defined]
            return number
    raise AccountNumbersExhausted("No free account number was found")


async def is_issued(session: AsyncSession, number: AccountNumber) -> bool:
    return bool((await session.execute(_EXISTS, {"number": number.value})).scalar_one())
