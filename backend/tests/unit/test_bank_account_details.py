"""The bank account clients pay into: what details are accepted, and how they are tidied."""

import pytest

from app.modules.payments.domain.bank_accounts import BankAccount, InvalidBankAccount, checked


def account(
    *,
    bank_name: str = "Example Bank Uganda",
    account_name: str = "Finvest Clients Trust Account",
    account_number: str = "9030001234567",
    branch: str | None = "Kampala Road",
) -> BankAccount:
    return checked(
        bank_name=bank_name,
        account_name=account_name,
        account_number=account_number,
        branch=branch,
    )


def test_good_details_are_kept_as_written() -> None:
    assert account() == BankAccount(
        "Example Bank Uganda", "Finvest Clients Trust Account", "9030001234567", "Kampala Road"
    )


def test_spaces_are_tidied_and_the_number_loses_its_spaces_and_dashes() -> None:
    tidied = account(
        bank_name="  Example   Bank ",
        account_name="Finvest\tClients",
        account_number=" 903-000 1234567 ",
        branch="   ",
    )
    assert tidied == BankAccount("Example Bank", "Finvest Clients", "9030001234567", None)


def test_a_missing_branch_is_none() -> None:
    assert account(branch=None).branch is None


@pytest.mark.parametrize(
    ("changes", "problems"),
    [
        ({"bank_name": " "}, [("bank_name", "required")]),
        ({"account_name": ""}, [("account_name", "required")]),
        ({"bank_name": "B" * 101}, [("bank_name", "too_long")]),
        ({"account_name": "A" * 101}, [("account_name", "too_long")]),
        ({"branch": "K" * 101}, [("branch", "too_long")]),
        ({"account_number": "12345"}, [("account_number", "invalid")]),
        ({"account_number": "1" * 21}, [("account_number", "invalid")]),
        ({"account_number": "90300O1234567"}, [("account_number", "invalid")]),
        (
            {"account_number": "\uff19\uff10\uff13\uff10\uff10\uff10\uff11\uff12\uff13"},
            [("account_number", "invalid")],
        ),
        (
            {"bank_name": "", "account_name": "", "account_number": ""},
            [
                ("bank_name", "required"),
                ("account_name", "required"),
                ("account_number", "invalid"),
            ],
        ),
    ],
)
def test_details_that_cannot_be_used_are_refused_field_by_field(
    changes: dict[str, str], problems: list[tuple[str, str]]
) -> None:
    with pytest.raises(InvalidBankAccount) as refused:
        account(**changes)
    assert refused.value.problems == problems


def test_the_shortest_and_longest_numbers_are_accepted() -> None:
    assert account(account_number="123456").account_number == "123456"
    assert account(account_number="1" * 20).account_number == "1" * 20
