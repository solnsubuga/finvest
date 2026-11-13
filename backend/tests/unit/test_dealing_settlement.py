from datetime import date

import pytest

from app.modules.dealing.domain.orders import Fill, OrderKind
from app.modules.dealing.domain.settlement import (
    BANK_TRUST,
    BILLS,
    SALE_GAINS,
    SALE_LOSSES,
    SOURCE_TYPE,
    InvalidSettlement,
    Settlement,
    accounts,
    settlement_entry,
)
from app.modules.ledger.domain.entries import AccountKind
from app.shared.money import Currency, Money

IDS = {
    BANK_TRUST: "acct-bank",
    BILLS: "acct-bills",
    SALE_GAINS: "acct-gain",
    SALE_LOSSES: "acct-loss",
}
SETTLED = date(2026, 11, 19)


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def fill(face: int, amount: int) -> Fill:
    return Fill(ugx(face), ugx(amount), SETTLED, date(2027, 2, 18), "CSD-2026-0502")


def lines(settlement: Settlement) -> dict[str, int]:
    entry = settlement_entry(settlement, IDS)
    assert sum(line.amount.minor for line in entry.lines) == 0
    return {line.account_id: line.amount.minor for line in entry.lines}


def test_a_purchase_swaps_bank_cash_for_bills_at_cost() -> None:
    settlement = Settlement("-OdOrder", OrderKind.AUCTION_BID, fill(240, 233), ugx(233))
    entry = settlement_entry(settlement, IDS)
    assert lines(settlement) == {"acct-bills": 233, "acct-bank": -233}
    assert (entry.entry_type, entry.effective_date) == ("dealing.purchase", SETTLED)
    assert (entry.source_type, entry.source_id) == (SOURCE_TYPE, "-OdOrder")
    assert entry.idempotency_key == "dealing-order:-OdOrder:settle"


@pytest.mark.parametrize(
    ("proceeds", "expected"),
    [
        (150, {"acct-bank": 150, "acct-bills": -145, "acct-gain": -5}),
        (140, {"acct-bank": 140, "acct-bills": -145, "acct-loss": 5}),
        (145, {"acct-bank": 145, "acct-bills": -145}),
    ],
)
def test_a_sale_books_the_difference_from_cost_as_a_gain_or_a_loss(
    proceeds: int, expected: dict[str, int]
) -> None:
    settlement = Settlement("-OdOrder", OrderKind.SECONDARY_SALE, fill(150, proceeds), ugx(145))
    assert lines(settlement) == expected
    assert settlement_entry(settlement, IDS).entry_type == "dealing.sale"


def test_a_sale_that_released_no_cost_is_all_gain() -> None:
    settlement = Settlement("-OdOrder", OrderKind.SECONDARY_SALE, fill(1, 1), ugx(0))
    assert lines(settlement) == {"acct-bank": 1, "acct-gain": -1}


def test_a_purchase_is_carried_at_what_was_paid() -> None:
    with pytest.raises(InvalidSettlement):
        Settlement("-OdOrder", OrderKind.SECONDARY_PURCHASE, fill(240, 233), ugx(232))


@pytest.mark.parametrize("cost", [Money(1, Currency.USD), ugx(-1)])
def test_the_cost_is_in_the_orders_currency_and_not_negative(cost: Money) -> None:
    with pytest.raises(InvalidSettlement):
        Settlement("-OdOrder", OrderKind.SECONDARY_SALE, fill(240, 233), cost)


def test_the_accounts_are_the_desks_own_in_one_currency() -> None:
    specs = accounts(Currency.USD)
    assert {code: spec.kind for code, spec in specs.items()} == {
        BANK_TRUST: AccountKind.ASSET,
        BILLS: AccountKind.ASSET,
        SALE_GAINS: AccountKind.INCOME,
        SALE_LOSSES: AccountKind.EXPENSE,
    }
    assert all(
        spec.currency is Currency.USD and spec.owner_client_id is None for spec in specs.values()
    )
