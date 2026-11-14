"""Finvest's ioTec wallet against the ledger, read at any moment of the day. Pure.

The day close compares each pot with its statement once the day is over. This is the check in
between: what ioTec says the wallet holds now, beside what the ledger says the ioTec pots hold
now. It changes nothing, and a difference is expected while a payment is on its way or a fee has
not been posted yet. It tells staff early that something needs looking at.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.shared.money import Money

# The ledger code of the ioTec pots. One wallet holds the money of every network.
WALLET_LEDGER_CODE = "iotec.wallet"


@dataclass(frozen=True, slots=True)
class WalletReading:
    balance: Money
    read_at: datetime


class WalletBalances(Protocol):
    """Reads the wallet's balance from the provider. None when it could not be read."""

    async def wallet_balance(self) -> WalletReading | None: ...


@dataclass(frozen=True, slots=True)
class WalletCheck:
    reading: WalletReading | None
    ledger: Money

    @property
    def difference(self) -> Money | None:
        """The wallet less the ledger. Positive when the wallet holds more."""
        if self.reading is None:
            return None
        return self.reading.balance - self.ledger
