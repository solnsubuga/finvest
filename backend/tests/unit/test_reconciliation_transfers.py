"""Transfers between pots: which are allowed and the one entry an approval posts."""

from datetime import date

import pytest

from app.modules.ledger.domain.entries import AccountKind
from app.modules.reconciliation.domain import transfers
from app.modules.reconciliation.domain.pots import Pot
from app.modules.reconciliation.domain.statements import StatementSource
from app.modules.reconciliation.domain.transfers import TransferError, TransferTerms
from app.shared.money import Currency, Money

MTN = transfers.find_pot(StatementSource.IOTEC, "mtn")
AIRTEL = transfers.find_pot(StatementSource.IOTEC, "airtel")
BANK = transfers.find_pot(StatementSource.BANK, "trust")
DAY = date(2026, 11, 12)


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


def terms(
    sent: int = 1_000_000, received: int = 990_000, *, start: Pot = MTN, end: Pot = BANK
) -> TransferTerms:
    return TransferTerms("-Nabc0000000000000T1", start, end, ugx(sent), ugx(received), DAY)


def test_a_sweep_posts_what_arrived_the_fee_and_what_left_in_one_entry() -> None:
    draft = transfers.posting(
        terms(), from_account_id="wallet", to_account_id="bank", fee_account_id="fees"
    )
    assert [(line.account_id, line.amount) for line in draft.lines] == [
        ("bank", ugx(990_000)),
        ("fees", ugx(10_000)),
        ("wallet", ugx(-1_000_000)),
    ]
    assert sum(line.amount.minor for line in draft.lines) == 0
    assert (draft.entry_type, draft.effective_date) == ("reconciliation.pot_transfer", DAY)
    assert (draft.source_type, draft.source_id) == ("pot_transfer", "-Nabc0000000000000T1")
    assert draft.idempotency_key == "pot-transfer:-Nabc0000000000000T1"


def test_a_transfer_with_no_fee_has_no_fee_line() -> None:
    draft = transfers.posting(
        terms(500, 500, start=BANK, end=AIRTEL),
        from_account_id="bank",
        to_account_id="wallet",
        fee_account_id="fees",
    )
    assert [line.account_id for line in draft.lines] == ["wallet", "bank"]
    assert terms(500, 500).fee == ugx(0)


@pytest.mark.parametrize(
    ("sent", "received", "start", "end", "code"),
    [
        (100, 100, MTN, MTN, "transfer_same_pot"),
        (0, 0, MTN, BANK, "amount_not_positive"),
        (100, 0, MTN, BANK, "amount_not_positive"),
        (100, 101, MTN, BANK, "transfer_received_more_than_sent"),
        (100, 99, BANK, MTN, "transfer_fee_not_allowed"),
    ],
)
def test_transfers_the_rules_refuse(
    sent: int, received: int, start: Pot, end: Pot, code: str
) -> None:
    with pytest.raises(TransferError) as refused:
        terms(sent, received, start=start, end=end)
    assert refused.value.code == code


def test_a_currency_mismatch_and_an_unknown_pot_are_refused() -> None:
    with pytest.raises(TransferError) as mixed:
        TransferTerms("-Nabc0000000000000T1", MTN, BANK, ugx(5), Money(5, Currency.USD), DAY)
    assert mixed.value.code == "currency_not_supported"
    with pytest.raises(TransferError) as unknown:
        transfers.find_pot(StatementSource.CUSTODIAN, "bills")
    assert unknown.value.code == "pot_unknown"


def test_fees_are_an_expense() -> None:
    account = transfers.fee_account(Currency.UGX)
    assert (account.code, account.kind) == ("fees.provider.iotec", AccountKind.EXPENSE)
