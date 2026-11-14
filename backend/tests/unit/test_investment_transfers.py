"""The rules for sending an investment to another client. No database."""

from datetime import date

import pytest

from app.modules.investments.domain.transfers import (
    TransferRefusal,
    check_transfer,
    display_name,
    needs_review,
    remaining_term,
    split,
)
from app.shared.money import Currency, Money

TODAY = date(2026, 11, 14)
MATURES = date(2027, 1, 9)
MINIMUM = Money(100_000, Currency.UGX)


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


def refusal(amount: int, value: int = 5_049_486, **changes: object) -> TransferRefusal | None:
    facts: dict[str, object] = {
        "running": True,
        "withdrawal_requested": False,
        "maturity": MATURES,
        "today": TODAY,
        "value": ugx(value),
        "amount": ugx(amount),
        "minimum": MINIMUM,
    }
    facts.update(changes)
    return check_transfer(**facts)  # type: ignore[arg-type]


def test_part_of_an_investment_can_be_sent() -> None:
    assert refusal(1_000_000) is None


def test_the_whole_value_can_be_sent() -> None:
    assert refusal(5_049_486) is None


def test_the_recipient_gets_at_least_the_product_minimum() -> None:
    assert refusal(99_999) is TransferRefusal.BELOW_MINIMUM
    assert refusal(100_000) is None


def test_the_sender_keeps_nothing_or_at_least_the_minimum() -> None:
    assert refusal(4_949_487) is TransferRefusal.REMAINDER_BELOW_MINIMUM
    assert refusal(4_949_486) is None


def test_nobody_sends_more_than_the_investment_is_worth() -> None:
    assert refusal(5_049_487) is TransferRefusal.MORE_THAN_VALUE


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"running": False}, TransferRefusal.NOT_RUNNING),
        ({"withdrawal_requested": True}, TransferRefusal.WITHDRAWAL_REQUESTED),
        ({"today": MATURES}, TransferRefusal.MATURES_TODAY),
        ({"today": date(2027, 1, 10)}, TransferRefusal.MATURES_TODAY),
        ({"amount": Money(100_000, Currency.USD)}, TransferRefusal.CURRENCY),
    ],
)
def test_what_cannot_be_sent(changes: dict[str, object], expected: TransferRefusal) -> None:
    amount = changes.pop("amount", None)
    if amount is not None:
        facts = {"amount": amount}
        result = check_transfer(
            running=True,
            withdrawal_requested=False,
            maturity=MATURES,
            today=TODAY,
            value=ugx(5_049_486),
            minimum=MINIMUM,
            **facts,  # type: ignore[arg-type]
        )
    else:
        result = refusal(1_000_000, **changes)  # type: ignore[arg-type]
    assert result is expected


def test_the_day_before_maturity_is_the_last_day_to_send() -> None:
    assert refusal(1_000_000, today=date(2027, 1, 8)) is None


def test_an_open_term_can_be_sent_at_any_time() -> None:
    assert refusal(1_000_000, maturity=None, today=date(2030, 1, 1)) is None


def test_a_split_gives_the_recipient_exactly_what_was_sent_and_the_sender_the_rest() -> None:
    shares = split(ugx(5_049_486), ugx(1_000_000), whole=False)
    assert (shares.sent, shares.kept) == (ugx(1_000_000), ugx(4_049_486))
    assert shares.sent + (shares.kept or ugx(0)) == ugx(5_049_486)


def test_sending_everything_includes_interest_added_since_the_request() -> None:
    shares = split(ugx(5_050_100), ugx(5_049_486), whole=True)
    assert (shares.sent, shares.kept) == (ugx(5_050_100), None)


def test_a_split_that_leaves_nothing_keeps_nothing() -> None:
    assert split(ugx(1_000_000), ugx(1_000_000), whole=False).kept is None


def test_a_split_never_sends_more_than_the_value() -> None:
    with pytest.raises(ValueError, match="more than"):
        split(ugx(1_000), ugx(1_001), whole=False)


def test_the_new_investments_run_to_the_same_maturity_date() -> None:
    assert remaining_term(MATURES, TODAY) == 56
    assert remaining_term(None, TODAY) is None
    with pytest.raises(ValueError, match="before the maturity date"):
        remaining_term(MATURES, MATURES)


def test_a_transfer_is_reviewed_with_everything_else_sent_that_day() -> None:
    threshold = ugx(10_000_000)
    assert not needs_review(ugx(10_000_000), ugx(0), threshold)
    assert needs_review(ugx(10_000_001), ugx(0), threshold)
    # Splitting one large transfer into small ones does not avoid the review.
    assert needs_review(ugx(4_000_000), ugx(7_000_000), threshold)
    assert not needs_review(ugx(3_000_000), ugx(7_000_000), threshold)


def test_with_no_threshold_for_the_currency_every_transfer_is_reviewed() -> None:
    assert needs_review(Money(50_000, Currency.USD), Money(0, Currency.USD), None)


@pytest.mark.parametrize(
    ("surname", "given", "shown"),
    [
        ("KATO", "Brian Ssali", "Brian K."),
        ("Nakato", "AMARA", "Amara N."),
        ("  okello ", " peter ", "Peter O."),
        ("ACHIENG", "", "A."),
    ],
)
def test_a_client_is_shown_to_another_by_first_name_and_initial(
    surname: str, given: str, shown: str
) -> None:
    assert display_name(surname, given) == shown
