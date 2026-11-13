import json

import pytest
from pydantic import ValidationError

from app.modules.payments.settings import BankAccountDetails, BankTransferSettings
from app.shared.money import Currency


def test_no_accounts_are_set_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINVEST_BANK_TRANSFER_ACCOUNTS", raising=False)
    assert BankTransferSettings().accounts == {}


def test_accounts_are_read_per_currency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "FINVEST_BANK_TRANSFER_ACCOUNTS",
        json.dumps(
            {
                "UGX": {
                    "bank_name": "Example Bank Uganda",
                    "account_name": "Finvest Clients Trust Account",
                    "account_number": "9030001234567",
                }
            }
        ),
    )
    assert BankTransferSettings().accounts == {
        Currency.UGX: BankAccountDetails(
            bank_name="Example Bank Uganda",
            account_name="Finvest Clients Trust Account",
            account_number="9030001234567",
            branch=None,
        )
    }


@pytest.mark.parametrize(
    "accounts",
    [
        {"KES": {"bank_name": "B", "account_name": "A", "account_number": "1"}},
        {"UGX": {"bank_name": "", "account_name": "A", "account_number": "1"}},
        {"UGX": {"bank_name": "B", "account_name": "A"}},
        {"UGX": {"bank_name": "B", "account_name": "A", "account_number": "1", "swift": "X"}},
    ],
)
def test_an_account_that_is_not_complete_is_refused_at_start_up(
    monkeypatch: pytest.MonkeyPatch, accounts: object
) -> None:
    monkeypatch.setenv("FINVEST_BANK_TRANSFER_ACCOUNTS", json.dumps(accounts))
    with pytest.raises(ValidationError):
        BankTransferSettings()
