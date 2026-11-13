"""The journal entry that settles a dealing order. Pure: no I/O.

Bills are carried at cost. Buying them swaps cash in the trust bank account for bills, and
selling them swaps bills back for cash. What a client is owed never changes here: that is the
investments module's business.

| Order    | Debit                                      | Credit                                   |
| -------- | ------------------------------------------ | ---------------------------------------- |
| purchase | `dealing.bills`, the cost                  | `bank.trust`, the cost                   |
| sale     | `bank.trust`, the proceeds                 | `dealing.bills`, the cost released       |
|          | `dealing.sale_losses`, any shortfall       | `dealing.sale_gains`, any excess         |

The entry is dated on the settlement date, has a fixed idempotency key made from the order's
ID, and points back at the order (source type `dealing.order`).

When a lot matures the issuer pays its face value into the trust bank account:

| Maturity | Debit                                   | Credit                                   |
| -------- | --------------------------------------- | ---------------------------------------- |
|          | `bank.trust`, the face value left       | `dealing.bills`, the cost left           |
|          | `dealing.maturity_losses`, any shortfall | `dealing.bill_income`, face above cost  |

It is dated on the maturity date and points back at the lot (source type `dealing.lot`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.modules.dealing.domain.holdings import Lot, income_at_maturity
from app.modules.dealing.domain.orders import Fill, OrderKind
from app.modules.ledger.domain.entries import (
    AccountKind,
    AccountSpec,
    EntryDraft,
    Line,
    credit,
    debit,
)
from app.shared.money import Currency, Money

BANK_TRUST = "bank.trust"
BILLS = "dealing.bills"
SALE_GAINS = "dealing.sale_gains"
SALE_LOSSES = "dealing.sale_losses"
BILL_INCOME = "dealing.bill_income"
MATURITY_LOSSES = "dealing.maturity_losses"
SOURCE_TYPE = "dealing.order"
PURCHASE_ENTRY = "dealing.purchase"
SALE_ENTRY = "dealing.sale"
LOT_SOURCE_TYPE = "dealing.lot"
MATURITY_ENTRY = "dealing.maturity"


class InvalidSettlement(ValueError):
    """A settlement whose figures contradict the order's result."""


def accounts(currency: Currency) -> dict[str, AccountSpec]:
    """The system accounts a settlement can touch, by code. Opening one twice is safe."""
    return {
        BANK_TRUST: AccountSpec(BANK_TRUST, AccountKind.ASSET, currency, "Trust bank account"),
        BILLS: AccountSpec(BILLS, AccountKind.ASSET, currency, "Bills and bonds held, at cost"),
        SALE_GAINS: AccountSpec(SALE_GAINS, AccountKind.INCOME, currency, "Gains on bills sold"),
        SALE_LOSSES: AccountSpec(
            SALE_LOSSES, AccountKind.EXPENSE, currency, "Losses on bills sold"
        ),
        BILL_INCOME: AccountSpec(
            BILL_INCOME, AccountKind.INCOME, currency, "Income on bills held to maturity"
        ),
        MATURITY_LOSSES: AccountSpec(
            MATURITY_LOSSES, AccountKind.EXPENSE, currency, "Losses on bonds held to maturity"
        ),
    }


@dataclass(frozen=True, slots=True)
class Settlement:
    order_id: str
    kind: OrderKind
    fill: Fill
    # The cost that leaves the books. For a purchase it is what was paid.
    cost: Money

    def __post_init__(self) -> None:
        if self.cost.currency is not self.fill.amount.currency:
            raise InvalidSettlement("The cost is in the order's currency")
        if self.kind.buys and self.cost != self.fill.amount:
            raise InvalidSettlement("A purchase is carried at what was paid")
        if self.cost.is_negative:
            raise InvalidSettlement("The cost released cannot be negative")

    @property
    def result(self) -> Money:
        """Proceeds minus cost for a sale. Zero for a purchase."""
        return Money.zero(self.cost.currency) if self.kind.buys else self.fill.amount - self.cost

    @property
    def idempotency_key(self) -> str:
        return f"dealing-order:{self.order_id}:settle"


def settlement_entry(settlement: Settlement, account_ids: dict[str, str]) -> EntryDraft:
    """The journal entry for `settlement`. `account_ids` maps each code above to its account."""
    fill = settlement.fill
    lines: list[Line]
    if settlement.kind.buys:
        entry_type = PURCHASE_ENTRY
        lines = [
            debit(account_ids[BILLS], fill.amount),
            credit(account_ids[BANK_TRUST], fill.amount),
        ]
    else:
        entry_type = SALE_ENTRY
        lines = [debit(account_ids[BANK_TRUST], fill.amount)]
        if settlement.cost.is_positive:
            lines.append(credit(account_ids[BILLS], settlement.cost))
        result = settlement.result
        if result.is_positive:
            lines.append(credit(account_ids[SALE_GAINS], result))
        elif result.is_negative:
            lines.append(debit(account_ids[SALE_LOSSES], -result))
    return EntryDraft(
        entry_type=entry_type,
        effective_date=fill.settled_on,
        lines=tuple(lines),
        idempotency_key=settlement.idempotency_key,
        source_type=SOURCE_TYPE,
        source_id=settlement.order_id,
    )


def maturity_entry(lot_id: str, lot: Lot, on: date, account_ids: dict[str, str]) -> EntryDraft:
    """The journal entry for what is left of a lot when the issuer pays it on `on`.

    `lot` is the face value and cost still held. Refused before the maturity date.
    """
    income = income_at_maturity(lot, on)
    lines = [debit(account_ids[BANK_TRUST], lot.face), credit(account_ids[BILLS], lot.cost)]
    if income.is_positive:
        lines.append(credit(account_ids[BILL_INCOME], income))
    elif income.is_negative:
        lines.append(debit(account_ids[MATURITY_LOSSES], -income))
    return EntryDraft(
        entry_type=MATURITY_ENTRY,
        effective_date=lot.matures_on,
        lines=tuple(lines),
        idempotency_key=f"dealing-lot:{lot_id}:mature",
        source_type=LOT_SOURCE_TYPE,
        source_id=lot_id,
    )
