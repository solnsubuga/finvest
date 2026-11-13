from dataclasses import replace
from datetime import date

import pytest

from app.modules.dealing.domain.orders import (
    LOT_SIZES,
    Action,
    Fill,
    InvalidOrder,
    Order,
    OrderKind,
    OrderStatus,
    Refusal,
    allowed_actions,
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
HELD = ugx(0)
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
    assert (
        check_result(order, FILL, TODAY, discount_only=True, term_days=91, face_held=HELD)
        is Refusal.WRONG_STATUS
    )
    assert (
        check_result(order, None, TODAY, discount_only=True, term_days=91, face_held=HELD)
        is Refusal.WRONG_STATUS
    )


def test_a_bid_can_be_filled_in_full_in_part_or_not_at_all() -> None:
    part = replace(FILL, face=ugx(100 * M), amount=ugx(97 * M))
    assert (
        check_result(APPROVED, FILL, TODAY, discount_only=True, term_days=91, face_held=HELD)
        is None
    )
    assert (
        check_result(APPROVED, part, TODAY, discount_only=True, term_days=91, face_held=HELD)
        is None
    )
    assert (
        check_result(APPROVED, None, TODAY, discount_only=True, term_days=91, face_held=HELD)
        is None
    )


def test_a_fill_is_never_larger_than_the_order() -> None:
    over = replace(FILL, face=ugx(240 * M + 100_000))
    assert (
        check_result(APPROVED, over, TODAY, discount_only=True, term_days=91, face_held=HELD)
        is Refusal.OVER_THE_ORDER
    )


def test_a_treasury_bill_never_costs_more_than_its_face_and_a_bond_may() -> None:
    at_par = replace(FILL, amount=ugx(240 * M))
    above = replace(FILL, amount=ugx(240 * M + 1))
    assert (
        check_result(APPROVED, at_par, TODAY, discount_only=True, term_days=91, face_held=HELD)
        is None
    )
    assert (
        check_result(APPROVED, above, TODAY, discount_only=True, term_days=91, face_held=HELD)
        is Refusal.ABOVE_FACE
    )
    assert (
        check_result(APPROVED, above, TODAY, discount_only=False, term_days=91, face_held=HELD)
        is None
    )


def test_a_result_is_recorded_only_once_it_has_settled() -> None:
    tomorrow = replace(FILL, settled_on=date(2026, 11, 20))
    earlier = replace(FILL, settled_on=date(2026, 11, 18), matures_on=date(2027, 2, 17))
    assert (
        check_result(APPROVED, tomorrow, TODAY, discount_only=True, term_days=91, face_held=HELD)
        is Refusal.SETTLES_IN_THE_FUTURE
    )
    assert (
        check_result(APPROVED, earlier, TODAY, discount_only=True, term_days=91, face_held=HELD)
        is None
    )


def test_a_fill_in_another_currency_is_refused() -> None:
    dollars = replace(FILL, face=Money(100, Currency.USD), amount=Money(90, Currency.USD))
    assert (
        check_result(APPROVED, dollars, TODAY, discount_only=True, term_days=91, face_held=HELD)
        is Refusal.CURRENCY_MISMATCH
    )


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


def test_a_bill_bought_runs_no_longer_than_the_products_term() -> None:
    def result(days: int, term: int | None) -> Refusal | None:
        fill = replace(FILL, matures_on=date.fromordinal(TODAY.toordinal() + days))
        return check_result(
            APPROVED, fill, TODAY, discount_only=True, term_days=term, face_held=HELD
        )

    assert result(91, 91) is None
    assert result(40, 91) is None  # bought on the secondary market with less time to run
    assert result(92, 91) is Refusal.LONGER_THAN_TERM
    assert result(182, 91) is Refusal.LONGER_THAN_TERM
    assert result(3650, None) is None  # an open term takes any maturity


def test_a_sale_is_checked_against_what_is_held_when_the_result_comes_in() -> None:
    sale = replace(SALE, status=OrderStatus.APPROVED)
    fill = replace(FILL, face=ugx(50 * M), amount=ugx(49 * M))

    def result(held: int) -> Refusal | None:
        return check_result(
            sale, fill, TODAY, discount_only=True, term_days=91, face_held=ugx(held)
        )

    assert result(50 * M) is None
    assert result(50 * M - 1) is Refusal.MORE_THAN_HELD
    # A sale may be of a bill with any time left to run.
    long_dated = replace(fill, matures_on=date(2030, 1, 1))
    assert (
        check_result(
            sale, long_dated, TODAY, discount_only=True, term_days=91, face_held=ugx(50 * M)
        )
        is None
    )
    dollars = Money(100, Currency.USD)
    assert (
        check_result(sale, fill, TODAY, discount_only=True, term_days=91, face_held=dollars)
        is Refusal.CURRENCY_MISMATCH
    )


def test_every_currency_has_a_lot_size_in_its_own_currency() -> None:
    assert set(LOT_SIZES) == set(Currency)
    assert all(lot.currency is currency and lot.is_positive for currency, lot in LOT_SIZES.items())


@pytest.mark.parametrize(
    ("status", "by", "expected"),
    [
        (OrderStatus.PREPARED, "preparer", (Action.WITHDRAW,)),
        (OrderStatus.PREPARED, "other", (Action.APPROVE, Action.SEND_BACK)),
        (OrderStatus.APPROVED, "preparer", (Action.RECORD_RESULT,)),
        (OrderStatus.RESULT_RECORDED, "recorder", ()),
        (OrderStatus.RESULT_RECORDED, "preparer", (Action.CONFIRM_RESULT, Action.REJECT_RESULT)),
        (OrderStatus.SETTLED, "other", ()),
        (OrderStatus.SENT_BACK, "preparer", ()),
    ],
)
def test_each_person_is_offered_only_the_steps_they_may_take(
    status: OrderStatus, by: str, expected: tuple[Action, ...]
) -> None:
    order = Order(
        OrderKind.AUCTION_BID,
        "TREA-91",
        Money(100_000, Currency.UGX),
        "preparer",
        status,
        "recorder" if status is OrderStatus.RESULT_RECORDED else None,
    )
    assert allowed_actions(order, by) == expected
