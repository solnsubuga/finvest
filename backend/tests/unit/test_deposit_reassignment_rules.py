"""The rules for moving a deposit credited to the wrong client, tried exhaustively."""

import itertools
from dataclasses import replace

import pytest

from app.modules.payments.domain.deposits import DepositStatus, Method, Purpose
from app.modules.payments.domain.reassignments import (
    MAX_TEXT_LENGTH,
    Facts,
    ReassignmentStatus,
    Refusal,
    clean_text,
    reference_names,
    refusal,
)
from app.shared.money import Currency, Money


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


GOOD = Facts(
    method=Method.BANK_TRANSFER,
    status=DepositStatus.CONFIRMED,
    purpose=Purpose.INVESTMENT,
    amount=ugx(1_500_000),
    from_client_id="wrong",
    matched_line_id="line",
    still_waiting=ugx(1_500_000),
    to_client_id="right",
    to_payable=True,
    to_amount=ugx(1_500_000),
    to_has_payment=False,
)


def test_a_confirmed_matched_transfer_moves_to_another_clients_waiting_investment() -> None:
    assert refusal(GOOD) is None


@pytest.mark.parametrize("method", [Method.MTN, Method.AIRTEL])
def test_mobile_money_is_never_moved_to_another_client(method: Method) -> None:
    # It came from the first client's own number. It goes back there as a refund.
    assert refusal(replace(GOOD, method=method)) is Refusal.MOBILE_MONEY_REFUND_INSTEAD


@pytest.mark.parametrize("status", [s for s in DepositStatus if s is not DepositStatus.CONFIRMED])
def test_only_a_confirmed_deposit_moved_money_to_move(status: DepositStatus) -> None:
    assert refusal(replace(GOOD, status=status)) is Refusal.NOT_CONFIRMED


def test_a_goal_deposit_is_not_moved_here() -> None:
    assert refusal(replace(GOOD, purpose=Purpose.GOAL)) is Refusal.NOT_FOR_AN_INVESTMENT


def test_without_a_matched_statement_line_there_is_no_evidence() -> None:
    assert refusal(replace(GOOD, matched_line_id=None)) is Refusal.NOT_MATCHED_TO_A_LINE


@pytest.mark.parametrize(
    "waiting", [ugx(0), ugx(1_499_999), Money(1_500_000, Currency.USD)], ids=str
)
def test_money_no_longer_waiting_for_the_first_investment_is_refused(waiting: Money) -> None:
    assert refusal(replace(GOOD, still_waiting=waiting)) is Refusal.MONEY_MOVED_ON


def test_the_right_client_is_another_client() -> None:
    assert refusal(replace(GOOD, to_client_id="wrong")) is Refusal.SAME_CLIENT


def test_the_investment_must_be_waiting_for_payment() -> None:
    assert refusal(replace(GOOD, to_payable=False)) is Refusal.NOT_WAITING_FOR_PAYMENT


@pytest.mark.parametrize(
    "amount", [ugx(1_499_999), ugx(1_500_001), Money(1_500_000, Currency.USD)], ids=str
)
def test_the_investment_must_be_for_exactly_the_amount(amount: Money) -> None:
    assert refusal(replace(GOOD, to_amount=amount)) is Refusal.AMOUNT_DIFFERS_FROM_INVESTMENT


def test_an_investment_with_a_payment_of_its_own_is_refused() -> None:
    assert refusal(replace(GOOD, to_has_payment=True)) is Refusal.ALREADY_HAS_A_PAYMENT


def test_every_combination_of_problems_is_refused_and_names_one_of_them() -> None:
    changes = {
        "method": (Method.MTN, Refusal.MOBILE_MONEY_REFUND_INSTEAD),
        "status": (DepositStatus.FAILED, Refusal.NOT_CONFIRMED),
        "purpose": (Purpose.GOAL, Refusal.NOT_FOR_AN_INVESTMENT),
        "matched_line_id": (None, Refusal.NOT_MATCHED_TO_A_LINE),
        "still_waiting": (ugx(0), Refusal.MONEY_MOVED_ON),
        "to_client_id": ("wrong", Refusal.SAME_CLIENT),
        "to_payable": (False, Refusal.NOT_WAITING_FOR_PAYMENT),
        "to_amount": (ugx(1), Refusal.AMOUNT_DIFFERS_FROM_INVESTMENT),
        "to_has_payment": (True, Refusal.ALREADY_HAS_A_PAYMENT),
    }
    for size in range(1, len(changes) + 1):
        for chosen in itertools.combinations(changes, size):
            facts = replace(GOOD, **{name: changes[name][0] for name in chosen})  # type: ignore[arg-type]
            found = refusal(facts)
            assert found in {changes[name][1] for name in chosen}, chosen


def test_each_refusal_names_the_field_it_is_about() -> None:
    deposit = {r for r in Refusal if r.field == "deposit_id"}
    assert deposit == {
        Refusal.MOBILE_MONEY_REFUND_INSTEAD,
        Refusal.NOT_CONFIRMED,
        Refusal.NOT_FOR_AN_INVESTMENT,
        Refusal.NOT_MATCHED_TO_A_LINE,
        Refusal.MONEY_MOVED_ON,
    }
    assert {r.field for r in Refusal} == {"deposit_id", "to_investment_id"}


def test_only_a_proposal_is_open() -> None:
    assert [s for s in ReassignmentStatus if s.is_open] == [ReassignmentStatus.PROPOSED]


def test_a_reason_is_trimmed_and_must_say_something_short() -> None:
    assert clean_text("  Matched   to the wrong\nclient ") == "Matched to the wrong client"
    assert clean_text("   ") is None
    assert clean_text("x" * MAX_TEXT_LENGTH) == "x" * MAX_TEXT_LENGTH
    assert clean_text("x" * (MAX_TEXT_LENGTH + 1)) is None


@pytest.mark.parametrize(
    ("reference", "account", "names"),
    [
        ("FV 1234 567 890", "1234567890", True),
        ("payment 1234567890 thanks", "1234567890", True),
        ("1234-567-890", "1234567890", True),
        ("1234567891", "1234567890", False),
        ("", "1234567890", False),
        ("1234567890", None, False),
        ("1234567890", "", False),
        # An old Finvest 1.0 number, however short, must still be long enough to mean something.
        ("FV1-00042 June", "FV1-00042", True),
        ("Ref 12345 school fees", "12345", False),
    ],
)
def test_a_reference_names_an_account_number_whatever_the_bank_did_to_spaces(
    reference: str, account: str | None, names: bool
) -> None:
    assert reference_names(reference, account) is names


def test_a_reference_names_the_client_by_either_number() -> None:
    assert reference_names("old acc CL7731-22", "1234567890", "CL7731-22")
    assert reference_names("FV 1234567890", "1234567890", None)
    assert not reference_names("FV 9999999999", "1234567890", "CL7731-22")
    assert not reference_names("FV 1234567890")
