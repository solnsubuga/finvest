from datetime import date

import pytest

from app.modules.ledger.domain.entries import AccountKind
from app.modules.payouts.domain.destinations import DestinationKind
from app.modules.payouts.domain.payouts import LedgerEffect
from app.modules.payouts.domain.posting import (
    PayoutTerms,
    finish,
    funding_account,
    in_flight_account,
    paid_from_account,
    pending_account,
    reserve,
)
from app.shared.money import Currency, Money

DAY = date(2026, 11, 13)
AMOUNT = Money(250_000, Currency.UGX)
TERMS = PayoutTerms("payout-1", "client-1", AMOUNT, "acct-source", "acct-flight")


def movement(draft: object) -> dict[str, int]:
    return {line.account_id: line.amount.minor for line in draft.lines}  # type: ignore[attr-defined]


def test_reserve_moves_the_money_from_its_source_into_flight() -> None:
    draft = reserve(TERMS, DAY)
    assert movement(draft) == {"acct-source": 250_000, "acct-flight": -250_000}
    assert (draft.entry_type, draft.idempotency_key) == (
        "payout.reserve",
        "payout:payout-1:reserve",
    )
    assert (draft.source_type, draft.source_id, draft.effective_date) == ("payout", "payout-1", DAY)


@pytest.mark.parametrize(
    ("effect", "to_account"),
    [
        (LedgerEffect.SETTLE, "acct-wallet"),
        (LedgerEffect.PARK, "acct-pending"),
        (LedgerEffect.RELEASE, "acct-source"),
    ],
)
def test_finishing_empties_the_in_flight_account_exactly(
    effect: LedgerEffect, to_account: str
) -> None:
    draft = finish(TERMS, effect, DAY, to_account_id=to_account)
    assert movement(draft) == {"acct-flight": 250_000, to_account: -250_000}
    assert draft.idempotency_key == f"payout:payout-1:{effect.value}"
    # Reserve then finish leaves the in-flight account at zero.
    assert movement(reserve(TERMS, DAY))["acct-flight"] + movement(draft)["acct-flight"] == 0


def test_each_step_has_its_own_key_so_none_can_post_twice() -> None:
    keys = {reserve(TERMS, DAY).idempotency_key} | {
        finish(TERMS, effect, DAY, to_account_id="acct-source").idempotency_key
        for effect in LedgerEffect
        if effect is not LedgerEffect.NONE
    }
    assert len(keys) == 4


def test_a_released_payout_goes_only_back_to_its_source() -> None:
    with pytest.raises(ValueError):
        finish(TERMS, LedgerEffect.RELEASE, DAY, to_account_id="acct-elsewhere")


def test_a_step_with_no_money_has_no_entry() -> None:
    with pytest.raises(ValueError):
        finish(TERMS, LedgerEffect.NONE, DAY, to_account_id="acct-source")


def test_the_accounts_are_the_agreed_chart() -> None:
    flight = in_flight_account("payout-1", "client-1", Currency.UGX)
    assert (flight.code, flight.kind, flight.owner_client_id, flight.reference) == (
        "client.payout_in_flight",
        AccountKind.LIABILITY,
        "client-1",
        "payout-1",
    )
    pending = pending_account("client-1", Currency.UGX)
    assert (pending.code, pending.kind, pending.owner_client_id, pending.reference) == (
        "client.payout_pending",
        AccountKind.LIABILITY,
        "client-1",
        None,
    )
    funding = funding_account("client-1", "investment-9", Currency.UGX)
    assert (funding.code, funding.kind, funding.owner_client_id, funding.reference) == (
        "client.payout_pending",
        AccountKind.LIABILITY,
        "client-1",
        "investment-9",
    )
    with pytest.raises(ValueError):
        funding_account("client-1", "", Currency.UGX)
    wallet = paid_from_account(DestinationKind.MOBILE_MONEY, "mtn", Currency.UGX)
    assert (wallet.code, wallet.kind, wallet.owner_client_id, wallet.reference) == (
        "iotec.wallet",
        AccountKind.ASSET,
        None,
        "mtn",
    )
    bank = paid_from_account(DestinationKind.BANK, None, Currency.UGX)
    assert (bank.code, bank.kind, bank.reference) == ("bank.trust", AccountKind.ASSET, None)
    with pytest.raises(ValueError):
        paid_from_account(DestinationKind.MOBILE_MONEY, None, Currency.UGX)
