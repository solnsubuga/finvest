"""The client money check: what Finvest owes its clients against what it holds for them. Pure.

What is owed comes from the ledger: every account a client owns that is a liability, at the end
of the last closed day. What is held cannot come from the ledger as well, or the check would be
circular (the books always balance, so held would always equal owed). So it is read from outside
evidence:

- each cash pot's closing balance on that day's bank, ioTec or Pesapal statement, as two people
  signed it off in the day close;
- the bills and bonds at the custodian, at what was paid for them, with the latest custodian
  statement check signed off on or before that day;

less what in those pots is not client money: Finvest's own money in the buffer, its earnings so
far, and what it owes others, such as tax withheld for URA. Those are read from the ledger by
account code, each on its own line, so a reader can see every shilling that is taken away.

Interest clients have earned and not been paid is owed, and is shown on its own line: it is
paid for by the discount on the bills when they mature, so until then it is carried by
Finvest's earnings, which can be below zero.

The ledger balances, so the difference is fully accounted for by two things: the differences
still open on the closed day's statements, and assets on the books that no statement shows
(balances carried over from the paper register and not yet moved into a pot, or a client who
owes Finvest for a payment credited by mistake). Anything left over is `unexplained` and means
a figure was read wrongly.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.modules.reconciliation.domain.statements import StatementSource
from app.shared.money import Currency, Money

BILLS_CODE = "dealing.bills"
INTEREST_CODE = "client.interest_accrued"
EARNINGS = "earnings"


class Standing(StrEnum):
    TIES = "ties"  # held equals owed to the shilling
    SHORT = "short"  # held is less than owed
    OVER = "over"  # held is more than owed


@dataclass(frozen=True, slots=True)
class Balance:
    """One account code at the end of the day, as the ledger reads it (normal direction)."""

    code: str
    kind: str
    name: str
    for_clients: bool
    amount: Money


@dataclass(frozen=True, slots=True)
class PotHeld:
    """One cash pot on the closed day, as the day close saved it."""

    source: StatementSource
    account_reference: str
    name: str
    ledger_code: str
    statement: Money
    ledger: Money
    open_exceptions: Money


@dataclass(frozen=True, slots=True)
class Line:
    """One amount with what it is. `code` is the ledger's, or `EARNINGS` for income less costs."""

    code: str
    name: str
    amount: Money


@dataclass(frozen=True, slots=True)
class ClientMoneyCheck:
    business_date: date
    currency: Currency
    # Every client liability added up: invested, interest earned, paid in awaiting start, and
    # payouts on their way.
    owed: Money
    # Of `owed`, interest earned and not yet paid.
    interest_owed: Money
    pots: tuple[PotHeld, ...]
    # Bills and bonds held, at cost.
    bills: Money
    # What in the pots and bills is not client money, per code. Can be below zero.
    not_client_money: tuple[Line, ...]
    # Assets on the books that no statement shows. Not counted as held.
    not_on_statement: tuple[Line, ...]

    @property
    def on_statements(self) -> Money:
        return sum((pot.statement for pot in self.pots), Money.zero(self.currency))

    @property
    def not_client_money_total(self) -> Money:
        return sum((line.amount for line in self.not_client_money), Money.zero(self.currency))

    @property
    def held(self) -> Money:
        """What Finvest holds for its clients."""
        return self.on_statements + self.bills - self.not_client_money_total

    @property
    def difference(self) -> Money:
        """Held less owed: below zero is a shortfall."""
        return self.held - self.owed

    @property
    def open_exceptions(self) -> Money:
        """How far above the books the statements stand, by the differences still open."""
        return sum((pot.open_exceptions for pot in self.pots), Money.zero(self.currency))

    @property
    def not_on_statement_total(self) -> Money:
        return sum((line.amount for line in self.not_on_statement), Money.zero(self.currency))

    @property
    def unexplained(self) -> Money:
        """What neither the open differences nor the assets off statement account for."""
        return self.difference - self.open_exceptions + self.not_on_statement_total

    @property
    def standing(self) -> Standing:
        if self.difference.is_zero:
            return Standing.TIES
        return Standing.SHORT if self.difference.is_negative else Standing.OVER


def check(
    business_date: date,
    currency: Currency,
    balances: Sequence[Balance],
    pots: Sequence[PotHeld],
) -> ClientMoneyCheck:
    """Work the check out from the ledger's balances and the pots of a closed day.

    A client's liability is owed. Every other liability and equity is money in the pots that is
    not the clients', and so is income less costs, on one line. Assets are the pots (their
    statements are counted, not the books), the bills, and whatever else is on the books, which
    no statement shows.
    """
    zero = Money.zero(currency)
    for found in balances:
        if found.amount.currency is not currency:
            raise ValueError("Every balance must be in the check's currency")
    for pot in pots:
        if pot.statement.currency is not currency:
            raise ValueError("Every pot must be in the check's currency")
    owed = zero
    interest = zero
    bills = zero
    earnings = zero
    others: list[Line] = []
    assets: dict[str, tuple[str, Money]] = {}
    for found in balances:
        if found.kind == "liability" and found.for_clients:
            owed += found.amount
            if found.code == INTEREST_CODE:
                interest += found.amount
        elif found.kind in ("liability", "equity"):
            others.append(Line(found.code, found.name, found.amount))
        elif found.kind == "income":
            earnings += found.amount
        elif found.kind == "expense":
            earnings -= found.amount
        elif found.kind == "asset" and found.code == BILLS_CODE and not found.for_clients:
            bills += found.amount
        elif found.kind == "asset":
            name, so_far = assets.get(found.code, (found.name, zero))
            assets[found.code] = (name, so_far + found.amount)
        else:
            raise ValueError(f"Unknown account kind {found.kind}")
    for pot in pots:
        name, so_far = assets.get(pot.ledger_code, (pot.name, zero))
        assets[pot.ledger_code] = (name, so_far - pot.ledger)
    not_client = [line for line in others if not line.amount.is_zero]
    if not earnings.is_zero:
        not_client.append(Line(EARNINGS, "Finvest's earnings so far, income less costs", earnings))
    return ClientMoneyCheck(
        business_date=business_date,
        currency=currency,
        owed=owed,
        interest_owed=interest,
        pots=tuple(pots),
        bills=bills,
        not_client_money=tuple(not_client),
        not_on_statement=tuple(
            Line(code, name, amount)
            for code, (name, amount) in sorted(assets.items())
            if not amount.is_zero
        ),
    )
