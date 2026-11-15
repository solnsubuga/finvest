"""The rules for paying a card payment back to the card: when it may happen, what goes back,
and every move a refund can make."""

from dataclasses import replace
from itertools import product

import pytest

from app.modules.payments.domain.deposits import DepositStatus, Method, Purpose
from app.modules.payments.domain.provider_refunds import (
    Facts,
    RefundStatus,
    Refusal,
    RequestResult,
    Settlement,
    after_request,
    after_settlement,
    failure_code,
    refund_total,
    refusal,
)
from app.shared.money import Currency, Money


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


FACTS = Facts(
    status=DepositStatus.CONFIRMED,
    purpose=Purpose.INVESTMENT,
    method=Method.CARD,
    provider="pesapal",
    reversed=False,
    refunded=False,
    entry_type="payment.deposit_confirmed",
    confirmation_code="PSP1234",
    amount=ugx(1_000_000),
    waiting=ugx(1_000_000),
)


def test_a_card_payment_still_waiting_for_its_investment_may_go_back() -> None:
    assert refusal(FACTS) is None
    assert refusal(replace(FACTS, provider="card_sandbox")) is None
    # More waiting than the payment, from another payment for the same investment, is fine.
    assert refusal(replace(FACTS, waiting=ugx(1_500_000))) is None


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"status": DepositStatus.UNCERTAIN}, Refusal.NOT_CONFIRMED),
        ({"status": DepositStatus.FAILED}, Refusal.NOT_CONFIRMED),
        ({"purpose": Purpose.GOAL}, Refusal.NOT_FOR_AN_INVESTMENT),
        ({"method": Method.MTN, "provider": "iotec"}, Refusal.NOT_REFUNDABLE),
        ({"method": Method.BANK_TRANSFER}, Refusal.NOT_REFUNDABLE),
        ({"provider": "iotec"}, Refusal.NOT_REFUNDABLE),
        ({"reversed": True}, Refusal.ALREADY_TAKEN_BACK),
        ({"refunded": True}, Refusal.ALREADY_TAKEN_BACK),
        ({"entry_type": "payment.reassignment"}, Refusal.NOT_A_PAYMENT),
        ({"entry_type": None}, Refusal.NOT_A_PAYMENT),
        ({"confirmation_code": None}, Refusal.NO_CONFIRMATION_CODE),
        ({"confirmation_code": ""}, Refusal.NO_CONFIRMATION_CODE),
        ({"waiting": ugx(999_999)}, Refusal.MONEY_MOVED_ON),
        ({"waiting": ugx(0)}, Refusal.MONEY_MOVED_ON),
        ({"waiting": Money(1_000_000, Currency.USD)}, Refusal.MONEY_MOVED_ON),
    ],
)
def test_each_reason_a_payment_cannot_go_back(change: dict[str, object], expected: Refusal) -> None:
    assert refusal(replace(FACTS, **change)) is expected  # type: ignore[arg-type]


def test_the_first_reason_is_given() -> None:
    worst = replace(FACTS, status=DepositStatus.FAILED, purpose=Purpose.GOAL, refunded=True)
    assert refusal(worst) is Refusal.NOT_CONFIRMED


@pytest.mark.parametrize(
    ("amount", "charge", "erred", "total"),
    [
        (1_000_000, 35_000, False, 1_000_000),
        (1_000_000, 35_000, True, 1_035_000),
        (1_000_000, 0, True, 1_000_000),
        (1, 1, True, 2),
    ],
)
def test_the_card_charge_goes_back_only_when_finvest_erred(
    amount: int, charge: int, erred: bool, total: int
) -> None:
    assert refund_total(ugx(amount), ugx(charge), finvest_erred=erred) == ugx(total)


def test_a_charge_in_another_currency_or_below_zero_is_refused() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        refund_total(ugx(100), Money(1, Currency.USD), finvest_erred=True)
    with pytest.raises(ValueError, match="non-negative"):
        refund_total(ugx(100), ugx(-1), finvest_erred=False)


@pytest.mark.parametrize(
    ("result", "status", "code"),
    [
        (RequestResult.ACCEPTED, RefundStatus.REQUESTED, None),
        (RequestResult.REFUSED, RefundStatus.FAILED, "provider_refused"),
        (RequestResult.NOT_SENT, RefundStatus.FAILED, "provider_unavailable"),
        (RequestResult.UNKNOWN, RefundStatus.UNCERTAIN, None),
    ],
)
def test_the_providers_answer_moves_a_sending_refund(
    result: RequestResult, status: RefundStatus, code: str | None
) -> None:
    assert after_request(result) is status
    assert failure_code(result) == code


EXPECTED = {
    (RefundStatus.REQUESTED, Settlement.PAID): RefundStatus.DONE,
    (RefundStatus.REQUESTED, Settlement.NOT_PAID): RefundStatus.FAILED,
    (RefundStatus.UNCERTAIN, Settlement.PAID): RefundStatus.DONE,
    (RefundStatus.UNCERTAIN, Settlement.NOT_PAID): RefundStatus.FAILED,
    (RefundStatus.UNCERTAIN, Settlement.REQUESTED): RefundStatus.REQUESTED,
}


@pytest.mark.parametrize(("current", "found"), list(product(RefundStatus, Settlement)))
def test_what_staff_find_moves_only_a_refund_waiting_on_the_provider(
    current: RefundStatus, found: Settlement
) -> None:
    assert after_settlement(current, found) is EXPECTED.get((current, found))


def test_which_states_hold_money_and_which_are_final() -> None:
    holding = {s for s in RefundStatus if s.holds_money}
    final = {s for s in RefundStatus if s.is_final}
    assert holding == {RefundStatus.SENDING, RefundStatus.REQUESTED, RefundStatus.UNCERTAIN}
    assert final == {
        RefundStatus.SENT_BACK,
        RefundStatus.WITHDRAWN,
        RefundStatus.DONE,
        RefundStatus.FAILED,
    }
    assert [s for s in RefundStatus if s.is_open] == [RefundStatus.PROPOSED]
