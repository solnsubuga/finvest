from dataclasses import replace
from datetime import date

import pytest

from app.modules.dealing.domain.orders import (
    Fill,
    InvalidOrder,
    Order,
    OrderKind,
    OrderStatus,
    Refusal,
    check_approval,
    check_confirmation,
    check_new_order,
    check_result,
    check_result_rejection,
    check_send_back,
    check_withdrawal,
)
from app.shared.money import Currency, Money

TODAY = date(2026, 11, 19)
M = 1_000_000


def ugx(amount: int) -> Money:
    return Money(amount, Currency.UGX)


LOT_SIZE = ugx(100_000)
BID = Order(OrderKind.AUCTION_BID, "TREA-91", ugx(240 * M), prepared_by="daniel")
SALE = Order(OrderKind.SECONDARY_SALE, "TREA-91", ugx(50 * M), prepared_by="daniel")
APPROVED = replace(BID, status=OrderStatus.APPROVED)
RECORDED = replace(BID, status=OrderStatus.RESULT_RECORDED, result_recorded_by="grace")
FILL = Fill(
    face=ugx(240 * M),
    amount=ugx(232_800_000),
    settled_on=TODAY,
    matures_on=date(2027, 2, 18),
    custodian_reference="CSD-2026-0481",
)
NOT_RECORDED = [status for status in OrderStatus if status is not OrderStatus.RESULT_RECORDED]


def in_status(status: OrderStatus) -> Order:
    return RECORDED if status is OrderStatus.RESULT_RECORDED else replace(BID, status=status)


def test_only_a_sale_sells() -> None:
    assert [kind for kind in OrderKind if not kind.buys] == [OrderKind.SECONDARY_SALE]


def test_an_order_is_open_until_it_ends() -> None:
    assert {status for status in OrderStatus if status.is_open} == {
        OrderStatus.PREPARED,
        OrderStatus.APPROVED,
        OrderStatus.RESULT_RECORDED,
    }
    assert all(status.is_final is not status.is_open for status in OrderStatus)


@pytest.mark.parametrize(
    ("face", "refusal"),
    [
        (100_000, None),
        (240 * M, None),
        (240 * M + 1, Refusal.NOT_A_WHOLE_LOT),
        (99_999, Refusal.NOT_A_WHOLE_LOT),
    ],
)
def test_an_order_is_for_whole_lots(face: int, refusal: Refusal | None) -> None:
    assert check_new_order(replace(BID, face=ugx(face)), LOT_SIZE, ugx(0)) is refusal


@pytest.mark.parametrize(
    ("held", "refusal"),
    [
        (50 * M, None),
        (900 * M, None),
        (50 * M - 1, Refusal.MORE_THAN_HELD),
        (0, Refusal.MORE_THAN_HELD),
    ],
)
def test_a_sale_cannot_exceed_the_face_held(held: int, refusal: Refusal | None) -> None:
    assert check_new_order(SALE, LOT_SIZE, ugx(held)) is refusal


def test_a_purchase_does_not_depend_on_what_is_held() -> None:
    for kind in (OrderKind.AUCTION_BID, OrderKind.SECONDARY_PURCHASE):
        assert check_new_order(replace(BID, kind=kind), LOT_SIZE, ugx(0)) is None


def test_a_new_order_is_checked_in_its_own_currency_and_first_status() -> None:
    dollars = Money(100_00, Currency.USD)
    assert check_new_order(BID, dollars, ugx(0)) is Refusal.CURRENCY_MISMATCH
    assert check_new_order(BID, LOT_SIZE, dollars) is Refusal.CURRENCY_MISMATCH
    assert check_new_order(APPROVED, LOT_SIZE, ugx(0)) is Refusal.WRONG_STATUS
    with pytest.raises(InvalidOrder):
        check_new_order(BID, ugx(0), ugx(0))


def test_an_order_names_its_product_preparer_and_a_positive_face() -> None:
    for change in ({"product_code": ""}, {"prepared_by": ""}, {"face": ugx(0)}):
        with pytest.raises(InvalidOrder):
            replace(BID, **change)
    with pytest.raises(InvalidOrder):
        replace(BID, status=OrderStatus.RESULT_RECORDED)
    with pytest.raises(InvalidOrder):
        replace(BID, result_recorded_by="grace")


def test_nobody_approves_their_own_order() -> None:
    assert check_approval(BID, "daniel") is Refusal.SAME_PERSON
    assert check_approval(BID, "grace") is None


def test_sending_back_needs_another_person_and_a_note() -> None:
    assert check_send_back(BID, "daniel", "Too large") is Refusal.SAME_PERSON
    assert check_send_back(BID, "grace", "  ") is Refusal.NOTE_REQUIRED
    assert check_send_back(BID, "grace", "x" * 501) is Refusal.NOTE_REQUIRED
    assert check_send_back(BID, "grace", "Too large for this auction") is None


def test_only_the_preparer_withdraws() -> None:
    assert check_withdrawal(BID, "grace") is Refusal.NOT_THE_PREPARER
    assert check_withdrawal(BID, "daniel") is None


@pytest.mark.parametrize("status", [s for s in OrderStatus if s is not OrderStatus.PREPARED])
def test_only_a_prepared_order_is_approved_sent_back_or_withdrawn(status: OrderStatus) -> None:
    order = in_status(status)
    assert check_approval(order, "grace") is Refusal.WRONG_STATUS
    assert check_send_back(order, "grace", "No") is Refusal.WRONG_STATUS
    assert check_withdrawal(order, "daniel") is Refusal.WRONG_STATUS


@pytest.mark.parametrize("status", [s for s in OrderStatus if s is not OrderStatus.APPROVED])
def test_a_result_is_recorded_only_against_an_approved_order(status: OrderStatus) -> None:
    order = in_status(status)
    assert check_result(order, FILL, TODAY, discount_only=True) is Refusal.WRONG_STATUS
    assert check_result(order, None, TODAY, discount_only=True) is Refusal.WRONG_STATUS


def test_a_bid_can_be_filled_in_full_in_part_or_not_at_all() -> None:
    part = replace(FILL, face=ugx(100 * M), amount=ugx(97 * M))
    assert check_result(APPROVED, FILL, TODAY, discount_only=True) is None
    assert check_result(APPROVED, part, TODAY, discount_only=True) is None
    assert check_result(APPROVED, None, TODAY, discount_only=True) is None


def test_a_fill_is_never_larger_than_the_order() -> None:
    over = replace(FILL, face=ugx(240 * M + 100_000))
    assert check_result(APPROVED, over, TODAY, discount_only=True) is Refusal.OVER_THE_ORDER


def test_a_treasury_bill_never_costs_more_than_its_face_and_a_bond_may() -> None:
    at_par = replace(FILL, amount=ugx(240 * M))
    above = replace(FILL, amount=ugx(240 * M + 1))
    assert check_result(APPROVED, at_par, TODAY, discount_only=True) is None
    assert check_result(APPROVED, above, TODAY, discount_only=True) is Refusal.ABOVE_FACE
    assert check_result(APPROVED, above, TODAY, discount_only=False) is None


def test_a_result_is_recorded_only_once_it_has_settled() -> None:
    tomorrow = replace(FILL, settled_on=date(2026, 11, 20))
    earlier = replace(FILL, settled_on=date(2026, 11, 18))
    assert (
        check_result(APPROVED, tomorrow, TODAY, discount_only=True) is Refusal.SETTLES_IN_THE_FUTURE
    )
    assert check_result(APPROVED, earlier, TODAY, discount_only=True) is None


def test_a_fill_in_another_currency_is_refused() -> None:
    dollars = replace(FILL, face=Money(100, Currency.USD), amount=Money(90, Currency.USD))
    assert check_result(APPROVED, dollars, TODAY, discount_only=True) is Refusal.CURRENCY_MISMATCH


@pytest.mark.parametrize(
    "change",
    [
        {"face": ugx(0)},
        {"amount": ugx(0)},
        {"amount": Money(100, Currency.USD)},
        {"matures_on": TODAY},
        {"matures_on": date(2026, 11, 18)},
        {"custodian_reference": ""},
        {"custodian_reference": " CSD-1 "},
        {"custodian_reference": "x" * 65},
    ],
)
def test_a_fill_that_contradicts_itself_is_refused(change: dict[str, object]) -> None:
    with pytest.raises(InvalidOrder):
        replace(FILL, **change)  # type: ignore[arg-type]


def test_a_result_is_confirmed_by_someone_who_did_not_record_it() -> None:
    assert check_confirmation(RECORDED, "grace") is Refusal.SAME_PERSON
    assert check_confirmation(RECORDED, "daniel") is None
    assert check_confirmation(RECORDED, "amina") is None


def test_rejecting_a_result_needs_another_person_and_a_note() -> None:
    assert check_result_rejection(RECORDED, "grace", "Wrong cost") is Refusal.SAME_PERSON
    assert check_result_rejection(RECORDED, "daniel", "") is Refusal.NOTE_REQUIRED
    assert check_result_rejection(RECORDED, "daniel", "Cost differs from the note") is None


@pytest.mark.parametrize("status", NOT_RECORDED)
def test_only_a_recorded_result_is_confirmed_or_rejected(status: OrderStatus) -> None:
    order = in_status(status)
    assert check_confirmation(order, "amina") is Refusal.WRONG_STATUS
    assert check_result_rejection(order, "amina", "No") is Refusal.WRONG_STATUS
