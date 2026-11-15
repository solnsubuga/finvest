"""The client money check on the Reconciliation screen (QA finding F-010).

The rules are in `domain/client_money.py`. This reads the figures for the last closed day: each
pot's statement as the day close saved it, every ledger code's balance at the end of the day
(through the ledger's read functions), the dealing desk's bills held, and the latest custodian
check signed off by then. It moves nothing and saves nothing.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dealing import holdings as dealing
from app.modules.ledger import statements as ledger_statements
from app.modules.reconciliation import custodian, day_close
from app.modules.reconciliation.domain.client_money import Balance, ClientMoneyCheck, PotHeld, check
from app.modules.reconciliation.domain.custodian import ItemKind
from app.modules.reconciliation.domain.day_close import CloseStatus
from app.modules.reconciliation.domain.pots import CASH_POTS
from app.shared.money import Currency, Money


@dataclass(frozen=True, slots=True)
class CustodianEvidence:
    """The custodian check behind the bills line."""

    check_id: str
    statement_date: date
    # Maturities the reviewers noted as not matching before they signed it off.
    differences: int


@dataclass(frozen=True, slots=True)
class ClientMoneyView:
    check: ClientMoneyCheck
    custodian: CustodianEvidence | None
    # Face value less cost of the bills held at the end of the day: what they will pay above
    # what was paid for them, if held to maturity. This is what pays the interest owed.
    discount_to_come: Money


async def client_money(
    session: AsyncSession, currency: Currency = Currency.UGX
) -> ClientMoneyView | None:
    """The check for the last day two people closed, or None before any day is closed."""
    day = await day_close.last_closed(session)
    if day is None:
        return None
    close = await day_close.get_close(session, day)
    if close is None or close.status is not CloseStatus.CLOSED:
        raise RuntimeError("The last closed day has no closed close")
    pots: list[PotHeld] = []
    for position in close.positions:
        if position.ledger_closing.currency is not currency:
            continue
        if position.statement_closing is None:
            raise RuntimeError("A closed day holds a pot with no statement")
        key = (position.source, position.account_reference)
        pot = next((each for each in CASH_POTS if each.key == key), None)
        # A pot no longer listed is still a pot of its source, on that source's ledger code.
        like = pot or next(each for each in CASH_POTS if each.source is position.source)
        pots.append(
            PotHeld(
                source=position.source,
                account_reference=position.account_reference,
                name=like.name if pot else position.account_reference,
                ledger_code=like.ledger_code,
                statement=position.statement_closing,
                ledger=position.ledger_closing,
                open_exceptions=position.open_exceptions_total,
            )
        )
    balances = [
        Balance(each.code, each.kind, each.name, each.for_clients, each.balance)
        for each in await ledger_statements.balances_as_of(session, currency, day)
    ]
    held = [each for each in await dealing.holdings_on(session, day) if each.currency is currency]
    signed = await custodian.last_signed_off_by(session, day)
    evidence = None
    if signed is not None:
        items = await custodian.items_of(session, signed.id)
        evidence = CustodianEvidence(
            check_id=signed.id,
            statement_date=signed.statement_date,
            differences=sum(1 for each in items if each.item.kind is not ItemKind.MATCH),
        )
    return ClientMoneyView(
        check=check(day, currency, balances, pots),
        custodian=evidence,
        discount_to_come=Money(sum(each.face_minor - each.cost_minor for each in held), currency),
    )
