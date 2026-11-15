"""Moving Finvest's own money from one pot to another, such as sweeping collected mobile money
from the ioTec wallet, or card payments settled by Pesapal, to the trust bank account. Pure: no I/O.

A transfer is recorded by one member of staff once the money has been sent, and approved by a
different one. Only then is it posted, as one entry: what arrived into the receiving pot, any
provider fee as an expense, and what left the sending pot. No client's money changes hands, so
no client balance moves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.modules.ledger.domain.entries import AccountKind, AccountSpec, EntryDraft, credit, debit
from app.modules.reconciliation.domain.pots import CASH_POTS, Pot
from app.modules.reconciliation.domain.statements import StatementSource
from app.shared.money import Currency, Money

# The expense account each provider's fee on a transfer out of its pot is booked to. A bank
# charge has no agreed account yet, so a transfer out of the bank carries no fee.
FEE_ACCOUNTS: dict[StatementSource, tuple[str, str]] = {
    StatementSource.IOTEC: ("fees.provider.iotec", "ioTec fees"),
    StatementSource.PESAPAL: ("fees.provider.pesapal", "Pesapal fees"),
}
ENTRY_TYPE = "reconciliation.pot_transfer"
SOURCE_TYPE = "pot_transfer"


class TransferStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    DECLINED = "declined"


class TransferError(Exception):
    """A transfer the rules do not allow. `code` is safe to show."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def find_pot(source: StatementSource, account_reference: str) -> Pot:
    for pot in CASH_POTS:
        if pot.key == (source, account_reference):
            return pot
    raise TransferError("pot_unknown")


@dataclass(frozen=True, slots=True)
class TransferTerms:
    transfer_id: str
    from_pot: Pot
    to_pot: Pot
    sent: Money  # what left the sending pot
    received: Money  # what arrived in the receiving pot; the rest is the provider's fee
    value_date: date

    def __post_init__(self) -> None:
        if self.from_pot.key == self.to_pot.key:
            raise TransferError("transfer_same_pot")
        currency = self.sent.currency
        if {self.received.currency, self.from_pot.currency, self.to_pot.currency} != {currency}:
            raise TransferError("currency_not_supported")
        if not self.sent.is_positive or not self.received.is_positive:
            raise TransferError("amount_not_positive")
        if self.received > self.sent:
            raise TransferError("transfer_received_more_than_sent")
        if self.fee.is_positive and self.from_pot.source not in FEE_ACCOUNTS:
            # Bank charges need their own account first.
            raise TransferError("transfer_fee_not_allowed")

    @property
    def fee(self) -> Money:
        return self.sent - self.received


def fee_account(currency: Currency, source: StatementSource = StatementSource.IOTEC) -> AccountSpec:
    """The expense account for the fee on a transfer out of a `source` pot. A pot with no fee
    account (the bank) books ioTec's, which a transfer with no fee never posts to."""
    code, name = FEE_ACCOUNTS.get(source, FEE_ACCOUNTS[StatementSource.IOTEC])
    return AccountSpec(code, AccountKind.EXPENSE, currency, name)


def posting(
    terms: TransferTerms, *, from_account_id: str, to_account_id: str, fee_account_id: str
) -> EntryDraft:
    """The one entry an approved transfer posts. Its key makes it impossible to post twice."""
    lines = [debit(to_account_id, terms.received)]
    if terms.fee.is_positive:
        lines.append(debit(fee_account_id, terms.fee))
    lines.append(credit(from_account_id, terms.sent))
    return EntryDraft(
        entry_type=ENTRY_TYPE,
        effective_date=terms.value_date,
        lines=tuple(lines),
        idempotency_key=f"pot-transfer:{terms.transfer_id}",
        source_type=SOURCE_TYPE,
        source_id=terms.transfer_id,
    )
