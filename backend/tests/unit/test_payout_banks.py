"""The banks a client may be paid to."""

import re

from app.modules.payouts.domain.banks import BANKS, find_bank
from app.modules.payouts.domain.destinations import BankAccount


def test_each_bank_has_one_code_of_the_shape_an_account_takes() -> None:
    codes = [bank.code for bank in BANKS]
    assert len(codes) == len(set(codes))
    assert len({bank.name for bank in BANKS}) == len(BANKS)
    for code in codes:
        # A code a bank account can carry, so every listed bank can be paid to.
        assert BankAccount(code, "1234567890").bank_code == code
        assert re.fullmatch(r"[a-z][a-z0-9_]+", code)


def test_a_bank_is_found_by_its_code_only() -> None:
    found = find_bank("stanbic")
    assert found is not None and found.name == "Stanbic Bank"
    assert find_bank("Stanbic") is None
    assert find_bank("moon_bank") is None
