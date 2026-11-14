"""The banks a client may be paid to. Pure: no I/O.

Banks licensed by the Bank of Uganda, by the name clients know them by. A payout account names
one by its code, and an account for a bank not on this list is refused. When the payout provider
needs its own code for each bank, it is added here beside ours.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Bank:
    code: str
    name: str


BANKS: tuple[Bank, ...] = (
    Bank("absa", "Absa Bank"),
    Bank("bank_of_africa", "Bank of Africa"),
    Bank("bank_of_baroda", "Bank of Baroda"),
    Bank("centenary", "Centenary Bank"),
    Bank("citibank", "Citibank"),
    Bank("dfcu", "dfcu Bank"),
    Bank("diamond_trust", "Diamond Trust Bank"),
    Bank("ecobank", "Ecobank"),
    Bank("equity", "Equity Bank"),
    Bank("exim", "Exim Bank"),
    Bank("finance_trust", "Finance Trust Bank"),
    Bank("gtbank", "Guaranty Trust Bank"),
    Bank("housing_finance", "Housing Finance Bank"),
    Bank("i_and_m", "I&M Bank"),
    Bank("kcb", "KCB Bank"),
    Bank("ncba", "NCBA Bank"),
    Bank("opportunity", "Opportunity Bank"),
    Bank("pride", "Pride Bank"),
    Bank("stanbic", "Stanbic Bank"),
    Bank("standard_chartered", "Standard Chartered"),
    Bank("tropical", "Tropical Bank"),
    Bank("uba", "United Bank for Africa"),
)

_BY_CODE = {bank.code: bank for bank in BANKS}


def find_bank(code: str) -> Bank | None:
    return _BY_CODE.get(code)
