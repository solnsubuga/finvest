"""The client money check: owed from the ledger, held from the statements and the custodian."""

from datetime import date

import pytest

from app.modules.reconciliation.domain.client_money import (
    EARNINGS,
    Balance,
    PotHeld,
    Standing,
    check,
)
from app.modules.reconciliation.domain.statements import StatementSource
from app.shared.money import Currency, Money

DAY = date(2026, 11, 14)


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


def balance(code: str, kind: str, amount: int, *, for_clients: bool = False) -> Balance:
    return Balance(code, kind, code, for_clients, ugx(amount))


def pot(
    reference: str, code: str, statement: int, ledger: int | None = None, open_: int = 0
) -> PotHeld:
    return PotHeld(
        StatementSource.BANK if code == "bank.trust" else StatementSource.IOTEC,
        reference,
        reference,
        code,
        ugx(statement),
        ugx(statement if ledger is None else ledger),
        ugx(open_),
    )


# A small book that balances: clients paid in 1,000,000 (30,000 of it by mobile money, on which
# ioTec charged 1,000), 600,000 went on bills, Finvest put 50,000 of its own into the buffer,
# and 20,000 of interest accrued, 4,000 of it withheld for URA.
BOOKS = [
    balance("bank.trust", "asset", 970_000 - 600_000 + 50_000),
    balance("iotec.wallet", "asset", 30_000 - 1_000),
    balance("dealing.bills", "asset", 600_000),
    balance("client.holding", "liability", 900_000, for_clients=True),
    balance("client.awaiting_start", "liability", 100_000, for_clients=True),
    balance("client.interest_accrued", "liability", 16_000, for_clients=True),
    balance("tax.withholding_payable", "liability", 4_000),
    balance("dealing.buffer_capital", "equity", 50_000),
    balance("investments.interest_expense", "expense", 20_000),
    balance("fees.provider.iotec", "expense", 1_000),
]
POTS = [pot("trust", "bank.trust", 420_000), pot("mtn", "iotec.wallet", 29_000)]


def test_what_is_held_ties_with_what_is_owed_when_the_statements_match_the_books() -> None:
    found = check(DAY, Currency.UGX, BOOKS, POTS)
    assert found.owed == ugx(1_016_000)
    assert found.interest_owed == ugx(16_000)
    assert found.on_statements == ugx(449_000)
    assert found.bills == ugx(600_000)
    # Tax for URA and the buffer are not client money. Interest accrued is a cost the bills'
    # discount has not paid for yet, so Finvest's earnings stand below zero and add to held.
    assert [(line.code, line.amount) for line in found.not_client_money] == [
        ("tax.withholding_payable", ugx(4_000)),
        ("dealing.buffer_capital", ugx(50_000)),
        (EARNINGS, ugx(-21_000)),
    ]
    assert found.held == ugx(449_000 + 600_000 - 4_000 - 50_000 + 21_000)
    assert found.held == found.owed
    assert (found.difference, found.unexplained) == (ugx(0), ugx(0))
    assert found.standing is Standing.TIES
    assert found.not_on_statement == ()


def test_a_difference_still_open_on_the_statement_shows_as_over_and_is_accounted_for() -> None:
    # The bank shows 5,000 more than the books: a payment in not yet confirmed by staff.
    pots = [pot("trust", "bank.trust", 425_000, 420_000, open_=5_000), POTS[1]]
    found = check(DAY, Currency.UGX, BOOKS, pots)
    assert found.difference == ugx(5_000)
    assert found.open_exceptions == ugx(5_000)
    assert found.unexplained == ugx(0)
    assert found.standing is Standing.OVER


def test_assets_no_statement_shows_are_not_counted_as_held() -> None:
    carried = [
        *BOOKS,
        balance("migration.opening_balances", "asset", 200_000),
        balance("client.holding", "liability", 200_000, for_clients=True),
        balance("receivables.deposit_correction", "asset", 3_000, for_clients=True),
        balance("client.awaiting_start", "liability", 3_000, for_clients=True),
    ]
    found = check(DAY, Currency.UGX, carried, POTS)
    assert [(line.code, line.amount) for line in found.not_on_statement] == [
        ("migration.opening_balances", ugx(200_000)),
        ("receivables.deposit_correction", ugx(3_000)),
    ]
    assert found.difference == ugx(-203_000)
    assert found.standing is Standing.SHORT
    assert found.unexplained == ugx(0)


def test_a_pot_on_the_books_beside_others_on_the_same_code_leaves_only_the_rest() -> None:
    # Two ioTec pots on one code, and another bank account on the trust account's code.
    books = [
        *BOOKS[:1],
        balance("bank.trust", "asset", 7_000),
        balance("iotec.wallet", "asset", 29_000),
        *BOOKS[2:],
        balance("test.owed", "liability", 7_000),
    ]
    pots = [*POTS, pot("airtel", "iotec.wallet", 0)]
    found = check(DAY, Currency.UGX, books, pots)
    assert [(line.code, line.amount) for line in found.not_on_statement] == [
        ("bank.trust", ugx(7_000))
    ]
    assert found.unexplained == ugx(0)


def test_a_statement_that_was_read_wrongly_is_unexplained() -> None:
    # A pot saved with a statement the books do not account for (it cannot close, but if a
    # figure were read wrongly this is where it would show).
    pots = [pot("trust", "bank.trust", 421_000, 420_000), POTS[1]]
    found = check(DAY, Currency.UGX, BOOKS, pots)
    assert found.unexplained == ugx(1_000)


def test_income_counts_as_finvests_own_money() -> None:
    books = [
        balance("bank.trust", "asset", 10_000),
        balance("dealing.bill_income", "income", 10_000),
    ]
    found = check(DAY, Currency.UGX, books, [pot("trust", "bank.trust", 10_000)])
    assert [(line.code, line.amount) for line in found.not_client_money] == [
        (EARNINGS, ugx(10_000))
    ]
    assert (found.held, found.owed, found.standing) == (ugx(0), ugx(0), Standing.TIES)


def test_nothing_on_the_books_ties_at_zero() -> None:
    found = check(DAY, Currency.UGX, [], [])
    assert (found.owed, found.held, found.standing) == (ugx(0), ugx(0), Standing.TIES)
    assert found.not_client_money == ()


def test_money_in_another_currency_is_refused() -> None:
    usd = Balance("bank.trust", "asset", "Trust", False, Money(1, Currency.USD))
    with pytest.raises(ValueError):
        check(DAY, Currency.UGX, [usd], [])
    with pytest.raises(ValueError):
        check(DAY, Currency.USD, [], [POTS[0]])


def test_an_unknown_account_kind_is_refused() -> None:
    with pytest.raises(ValueError):
        check(DAY, Currency.UGX, [balance("odd", "memo", 1)], [])
