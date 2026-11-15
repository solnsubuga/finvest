"""The pure rules of a card payment: the charge, the limits and how answers move a deposit."""

import pytest

from app.modules.payments.domain.cards import (
    CardDetails,
    CardQuote,
    CardTerms,
    OrderOpened,
    after_card_send,
    after_card_status,
    card_charge,
    check_limits,
    quote,
)
from app.modules.payments.domain.deposits import DepositStatus as S
from app.modules.payments.domain.deposits import InvalidDeposit
from app.modules.payments.domain.outcomes import (
    Confirmed,
    Deferred,
    InvalidOutcome,
    NotFound,
    Pending,
    Rejected,
    RejectionCode,
    Unavailable,
    Uncertain,
    UncertainReason,
)
from app.modules.platform_settings.domain.catalogue import (
    CHANNELS,
    InvalidSetting,
    checked,
)
from app.shared.money import Currency, Money


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


TERMS = CardTerms(350, ugx(5_000_000), ugx(10_000_000))


@pytest.mark.parametrize(
    ("amount", "basis_points", "charge"),
    [
        (1_000_000, 350, 35_000),
        (1, 350, 1),  # always rounded up to a whole shilling
        (100, 350, 4),
        (999_999, 350, 35_000),
        (1_000_000, 0, 0),
        (123_457, 1000, 12_346),
    ],
)
def test_the_card_charge_is_whole_shillings_rounded_up(
    amount: int, basis_points: int, charge: int
) -> None:
    assert card_charge(ugx(amount), basis_points) == ugx(charge)


def test_a_charge_rate_out_of_range_is_refused() -> None:
    with pytest.raises(ValueError):
        card_charge(ugx(100), 1001)
    with pytest.raises(ValueError):
        card_charge(ugx(100), -1)


def test_a_quote_is_only_for_a_positive_shilling_amount() -> None:
    assert quote(ugx(1_000_000), TERMS).total == ugx(1_035_000)
    with pytest.raises(InvalidDeposit, match="unsupported_currency"):
        quote(Money(100, Currency.USD), TERMS)
    with pytest.raises(InvalidDeposit, match="invalid_amount"):
        quote(ugx(0), TERMS)


def test_the_limits_count_the_total_and_the_last_30_days() -> None:
    at_limit = CardQuote(ugx(4_830_918), ugx(169_082))
    check_limits(at_limit, TERMS, paid_in_30_days=ugx(5_000_000))
    with pytest.raises(InvalidDeposit, match="above_card_payment_limit"):
        check_limits(CardQuote(ugx(4_900_000), ugx(171_500)), TERMS, paid_in_30_days=ugx(0))
    with pytest.raises(InvalidDeposit, match="above_card_30_day_limit"):
        check_limits(at_limit, TERMS, paid_in_30_days=ugx(5_000_001))


PAGE = "https://pay.pesapal.com/iframe/PesapalIframe3/Index?OrderTrackingId=abc"


def test_an_opened_order_waits_for_the_payer_and_a_lost_answer_fails() -> None:
    opened = after_card_send(OrderOpened("abc-123", PAGE), last_attempt=False)
    assert opened.status is S.AWAITING_APPROVAL and opened.provider_reference == "abc-123"
    refused = after_card_send(Rejected(RejectionCode.OTHER), last_attempt=False)
    assert refused.status is S.FAILED and refused.code == "other"
    assert after_card_send(Deferred(), last_attempt=False).status is S.CREATED
    assert after_card_send(Deferred(), last_attempt=True).code == "provider_unavailable"
    # Nobody received the page, so nobody can pay: failing is safe, and it is never resent.
    lost = after_card_send(Uncertain(UncertainReason.TIMED_OUT), last_attempt=False)
    assert lost.status is S.FAILED and lost.code == "provider_unavailable"


@pytest.mark.parametrize(
    "page",
    ["http://pay.pesapal.com/x", "javascript:alert(1)", "https://", "https://a b", "x" * 2000],
)
def test_a_page_must_be_an_https_address(page: str) -> None:
    with pytest.raises(InvalidOutcome):
        OrderOpened("abc", page)


def test_only_exactly_the_total_confirms_and_the_investment_gets_its_amount() -> None:
    total, amount = ugx(1_035_000), ugx(1_000_000)
    paid = after_card_status(
        S.AWAITING_APPROVAL, Confirmed("t", total), expected=total, credited=amount
    )
    assert paid is not None and paid.status is S.CONFIRMED and paid.post == amount
    short = after_card_status(
        S.AWAITING_APPROVAL, Confirmed("t", amount), expected=total, credited=amount
    )
    assert short is not None and short.status is S.UNCERTAIN and short.code == "amount_mismatch"
    assert (
        after_card_status(S.UNCERTAIN, Confirmed("t", amount), expected=total, credited=amount)
        is None
    )


@pytest.mark.parametrize(
    ("current", "reading", "status"),
    [
        (S.AWAITING_APPROVAL, Pending("t"), None),
        (S.AWAITING_APPROVAL, Unavailable(), None),
        (S.AWAITING_APPROVAL, Rejected(RejectionCode.DECLINED, "t"), S.FAILED),
        (S.AWAITING_APPROVAL, NotFound(), S.UNCERTAIN),
        (S.UNCERTAIN, NotFound(), None),
        (S.UNCERTAIN, Rejected(RejectionCode.DECLINED, "t"), S.FAILED),
        (S.CONFIRMED, Rejected(RejectionCode.DECLINED, "t"), None),
        (S.FAILED, Confirmed("t", Money(1, Currency.UGX)), None),
        (S.CREATED, Pending("t"), None),
    ],
)
def test_a_status_read_moves_only_an_open_card_deposit(
    current: S, reading: object, status: S | None
) -> None:
    move = after_card_status(
        current,
        reading,  # type: ignore[arg-type]
        expected=ugx(1),
        credited=ugx(1),
    )
    assert (move.status if move else None) is status


def test_card_details_never_hold_more_than_brand_and_last_four() -> None:
    assert CardDetails("Visa", "0010", "PSP123").last_four == "0010"
    with pytest.raises(InvalidOutcome):
        CardDetails("Visa", "4761730010", None)
    with pytest.raises(InvalidOutcome):
        CardDetails("<script>", None, None)


def test_a_stored_channel_value_from_before_cards_reads_cards_as_paused() -> None:
    before = {channel: True for channel in CHANNELS if channel != "card_pay_in"}
    parsed, tidy = checked("payment_channels", before)
    assert not parsed.is_open("card_pay_in")  # type: ignore[union-attr]
    assert parsed.is_open("mtn_pay_in")  # type: ignore[union-attr]
    assert tidy["card_pay_in"] is False
    with pytest.raises(InvalidSetting):
        checked("payment_channels", {"mtn_pay_in": True})


@pytest.mark.parametrize(
    "value",
    [
        {"charge_basis_points": 1001, "max_payment_minor": 1, "max_30_days_minor": 1},
        {"charge_basis_points": 0, "max_payment_minor": 0, "max_30_days_minor": 1},
        {"charge_basis_points": 0, "max_payment_minor": 2, "max_30_days_minor": 1},
        {"charge_basis_points": 1.5, "max_payment_minor": 1, "max_30_days_minor": 1},
        {"charge_basis_points": 0, "max_payment_minor": 1},
    ],
)
def test_card_settings_out_of_range_are_refused(value: dict[str, object]) -> None:
    with pytest.raises(InvalidSetting):
        checked("card_payments", value)


def test_card_settings_round_trip() -> None:
    value = {"charge_basis_points": 0, "max_payment_minor": 5, "max_30_days_minor": 9}
    assert checked("card_payments", value)[1] == value
