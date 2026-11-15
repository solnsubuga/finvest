"""Reading tax withheld from the ledger: by month, by client for URA's return, and for one
client's yearly certificate. Read-only, in the caller's staff or worker transaction.

The figures come from the ledger alone, through `ledger.statements.system_totals`: lines on
`interest.expense` and `tax.withholding_payable` from entries whose source is an investment.
The investments module keeps every entry that moves a client's tax under that source (agreed
with the investing thread, 2026-11-13). Investments are then read in batches for the client,
product and dates. Bond coupons are entries whose source is a bond holding
(`bonds.coupons.SOURCE_TYPE`), read the same way: a holding is a line of its own, named after
the bond, from the day it started to the bond's maturity.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.sealing import Sealer
from app.modules.bonds import holdings as bond_holdings
from app.modules.investments import service as investments
from app.modules.kyc import service as kyc
from app.modules.ledger import statements
from app.modules.tax.domain.periods import TaxYear, month_end
from app.modules.tax.domain.withholding import (
    INTEREST_EXPENSE,
    INVESTMENT_SOURCE,
    TAX_PAYABLE,
    Certificate,
    InvestmentTax,
    Payee,
    certificate,
    from_ledger,
    payees,
)
from app.shared.money import Currency, Money

INVESTMENT_BATCH = 500
INTEREST_ACCRUED = "client.interest_accrued"
# Bond coupons are posted with the holding as their source, and the holding is in this account.
BOND_SOURCE = "bond_holding"
BOND_HOLDING = "client.bond_holding"
SOURCES = (INVESTMENT_SOURCE, BOND_SOURCE)


class NoAccount(Exception):
    """The client has no approved account, so there is no name or number to print."""


async def withheld_by_month(
    session: AsyncSession, currency: Currency, *, date_from: date, date_to: date
) -> dict[date, Money]:
    """Tax withheld in each month that had any, by the entries' dates."""
    zero = Money.zero(currency)
    found: dict[date, Money] = {}
    for source_type in SOURCES:
        rows = await statements.system_totals(
            session,
            codes=[TAX_PAYABLE],
            currency=currency,
            source_type=source_type,
            date_from=date_from,
            date_to=date_to,
            by_source=False,
        )
        for row in rows:
            found[row.month] = found.get(row.month, zero) - row.amount
    return dict(sorted(found.items()))


async def by_investment(
    session: AsyncSession,
    currency: Currency,
    *,
    date_from: date,
    date_to: date,
    investment_ids: list[str] | None = None,
) -> list[InvestmentTax]:
    """Interest before tax and tax withheld per investment over the dates, both included.

    Bond holdings are lines too, with the holding's ID in `investment_id`.
    """
    found = await _by_investment_only(
        session, currency, date_from=date_from, date_to=date_to, investment_ids=investment_ids
    )
    return found + await _by_bond_holding(
        session, currency, date_from=date_from, date_to=date_to, holding_ids=investment_ids
    )


async def _by_bond_holding(
    session: AsyncSession,
    currency: Currency,
    *,
    date_from: date,
    date_to: date,
    holding_ids: list[str] | None,
) -> list[InvestmentTax]:
    rows = await statements.system_totals(
        session,
        codes=[INTEREST_EXPENSE, TAX_PAYABLE],
        currency=currency,
        source_type=BOND_SOURCE,
        date_from=date_from,
        date_to=date_to,
        by_source=True,
        source_ids=holding_ids,
    )
    zero = Money.zero(currency)
    expense: dict[str, Money] = {}
    payable: dict[str, Money] = {}
    for row in rows:
        if row.source_id is None:
            continue
        into = expense if row.account_code == INTEREST_EXPENSE else payable
        into[row.source_id] = into.get(row.source_id, zero) + row.amount
    found: list[InvestmentTax] = []
    for holding_id in sorted(set(expense) | set(payable)):
        holding = await bond_holdings.get_holding(session, holding_id)
        gross, tax = from_ledger(expense.get(holding_id, zero), payable.get(holding_id, zero))
        found.append(
            InvestmentTax(
                investment_id=holding.id,
                client_id=holding.client_id,
                product_name=holding.name,
                start_date=holding.start_date,
                maturity_date=holding.issue.maturity_date,
                gross=gross,
                tax=tax,
            )
        )
    return found


async def _by_investment_only(
    session: AsyncSession,
    currency: Currency,
    *,
    date_from: date,
    date_to: date,
    investment_ids: list[str] | None,
) -> list[InvestmentTax]:
    rows = await statements.system_totals(
        session,
        codes=[INTEREST_EXPENSE, TAX_PAYABLE],
        currency=currency,
        source_type=INVESTMENT_SOURCE,
        date_from=date_from,
        date_to=date_to,
        by_source=True,
        source_ids=investment_ids,
    )
    zero = Money.zero(currency)
    expense: dict[str, Money] = {}
    payable: dict[str, Money] = {}
    for row in rows:
        if row.source_id is None:
            continue
        into = expense if row.account_code == INTEREST_EXPENSE else payable
        into[row.source_id] = into.get(row.source_id, zero) + row.amount
    ids = sorted(set(expense) | set(payable))
    found: list[InvestmentTax] = []
    for start in range(0, len(ids), INVESTMENT_BATCH):
        batch = ids[start : start + INVESTMENT_BATCH]
        for investment in await investments.get_investments(session, batch):
            gross, tax = from_ledger(
                expense.get(investment.id, zero), payable.get(investment.id, zero)
            )
            found.append(
                InvestmentTax(
                    investment_id=investment.id,
                    client_id=investment.client_id,
                    product_name=investment.product_name,
                    start_date=investment.start_date,
                    maturity_date=investment.maturity_date,
                    gross=gross,
                    tax=tax,
                )
            )
    if len(found) != len(ids):
        # Every source is an investment this transaction can see: anything else is a bug.
        raise LookupError("A tax entry names an investment that cannot be read")
    return found


@dataclass(frozen=True, slots=True)
class PayeeView:
    """One client in a month's return, as staff see it."""

    client_id: str
    name: str | None  # "SURNAME Given names" from the approved check
    account_number: str | None
    tin: str | None
    gross: Money
    tax: Money


@dataclass(frozen=True, slots=True)
class MonthReport:
    month: date
    currency: Currency
    gross: Money
    withheld: Money
    payees: tuple[PayeeView, ...]
    # False when the API has no key to open tax numbers, so every `tin` is None.
    tins_shown: bool


async def month_report(
    session: AsyncSession, sealer: Sealer | None, currency: Currency, month: date
) -> MonthReport:
    """Every client with interest or tax in the month, with their name, account number and TIN."""
    items = await by_investment(session, currency, date_from=month, date_to=month_end(month))
    lines: list[Payee] = payees(items, currency)
    ids = [line.client_id for line in lines]
    names = await kyc.identities(session, ids)
    tins = await kyc.tax_numbers(session, sealer, ids) if sealer is not None else {}
    zero = Money.zero(currency)
    return MonthReport(
        month=month,
        currency=currency,
        gross=sum((line.gross for line in lines), zero),
        withheld=sum((line.tax for line in lines), zero),
        payees=tuple(
            PayeeView(
                client_id=line.client_id,
                name=names[line.client_id].verified_name if line.client_id in names else None,
                account_number=(
                    names[line.client_id].account_number if line.client_id in names else None
                ),
                tin=tins.get(line.client_id),
                gross=line.gross,
                tax=line.tax,
            )
            for line in lines
        ),
        tins_shown=sealer is not None,
    )


async def certificate_figures(
    session: AsyncSession, sealer: Sealer, client_id: str, tax_year: TaxYear
) -> Certificate:
    """A client's withholding tax certificate for a tax year, from the ledger.

    For the worker or staff, in a read-only transaction if the caller likes: it writes nothing.
    Raises `NoAccount` for a client who is not approved, and `DoesNotAddUp` if the figures do not
    add up, in which case nothing should be issued. A certificate with no lines means the client
    earned nothing in the year.
    """
    holder = await kyc.account_holder(session, client_id)
    if holder is None:
        raise NoAccount("The client has no approved account")
    owned = await statements.client_owed_as_of(session, client_id, tax_year.last_day)
    references = sorted(
        {
            account.account_reference
            for account in owned
            if account.account_code in (INTEREST_ACCRUED, BOND_HOLDING)
            and account.account_reference
        }
    )
    items: list[InvestmentTax] = []
    if references:
        for currency in Currency:
            items += await by_investment(
                session,
                currency,
                date_from=tax_year.first_day,
                date_to=tax_year.last_day,
                investment_ids=references,
            )
    tins = await kyc.tax_numbers(session, sealer, [client_id])
    return certificate(
        client_id=client_id,
        holder_name=holder.name,
        account_number=holder.account_number,
        tin=tins.get(client_id),
        tax_year=tax_year,
        items=items,
    )
