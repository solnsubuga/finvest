"""The pots of real money that are reconciled every day. Pure.

A pot is one place money sits: it has one ledger asset account and one statement a day. The
custodian is not here yet: it holds bills, not cash, and is compared with the dealing desk's
holdings when that module lands.
"""

from dataclasses import dataclass

from app.modules.ledger.domain.entries import AccountKind, AccountSpec
from app.modules.reconciliation.domain.statements import StatementSource
from app.shared.money import Currency


@dataclass(frozen=True, slots=True)
class Pot:
    source: StatementSource
    # The name statements are imported under, such as "mtn" or "trust".
    account_reference: str
    ledger_code: str
    ledger_reference: str | None
    name: str
    currency: Currency = Currency.UGX
    # A pot added after reconciliation began, such as Pesapal's, needs no statement on the days
    # before its ledger account was first used: nothing was ever in it, so it stands at zero.
    starts_when_used: bool = False

    @property
    def key(self) -> tuple[StatementSource, str]:
        return (self.source, self.account_reference)

    @property
    def ledger_account(self) -> AccountSpec:
        return AccountSpec(
            self.ledger_code,
            AccountKind.ASSET,
            self.currency,
            self.name,
            reference=self.ledger_reference,
        )


CASH_POTS: tuple[Pot, ...] = (
    Pot(StatementSource.IOTEC, "mtn", "iotec.wallet", "mtn", "ioTec wallet"),
    Pot(StatementSource.IOTEC, "airtel", "iotec.wallet", "airtel", "ioTec wallet"),
    # Card payments collected by Pesapal, until Pesapal settles them to the trust account. The
    # ledger account is the one card deposits are paid into (`payments.cards.provider_balance`).
    Pot(
        StatementSource.PESAPAL,
        "cards",
        "pesapal.balance",
        None,
        "Pesapal balance",
        starts_when_used=True,
    ),
    Pot(StatementSource.BANK, "trust", "bank.trust", None, "Trust bank account"),
)
