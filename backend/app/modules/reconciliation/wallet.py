"""Finvest's ioTec wallet beside the ledger, for staff at any moment of the day."""

from __future__ import annotations

from app.infrastructure.database import Database
from app.modules.identity.service import StaffSession
from app.modules.ledger import statements as ledger_statements
from app.modules.reconciliation.domain.wallet import WALLET_LEDGER_CODE, WalletBalances, WalletCheck
from app.shared.money import Currency


async def check(
    database: Database, staff: StaffSession, wallet: WalletBalances | None
) -> WalletCheck:
    """The wallet is read first, with no transaction open, then the ledger."""
    reading = None if wallet is None else await wallet.wallet_balance()
    async with database.actor_transaction(staff.actor) as session:
        ledger = await ledger_statements.total_balance(session, WALLET_LEDGER_CODE, Currency.UGX)
    return WalletCheck(reading, ledger)
