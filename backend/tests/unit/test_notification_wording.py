"""The event catalogue, the wording rules and the channel rules. No database."""

from datetime import date

import pytest

from app.modules.notifications.domain.events import EVENTS, Category, Channel, UnknownEvent, event
from app.modules.notifications.domain.preferences import (
    DEFAULTS as CHANNEL_DEFAULTS,
)
from app.modules.notifications.domain.preferences import (
    InvalidPreference,
    can_change,
    effective,
)
from app.modules.notifications.domain.preferences import (
    check as check_channels,
)
from app.modules.notifications.domain.wording import (
    DEFAULTS,
    EDITABLE,
    LINK_REMOVED,
    MAX_BODY,
    MAX_RENDERED_BODY,
    MAX_RENDERED_SMS,
    MAX_RENDERED_TITLE,
    MAX_SMS,
    MAX_TITLE,
    MAX_VALUE,
    InvalidVariables,
    InvalidWording,
    Wording,
    check,
    format_value,
    render,
    sample_values,
)
from app.shared.money import Currency, Money


def problems(event_name: str, **changed: str) -> list[tuple[str, str]]:
    base = DEFAULTS[event_name]
    wording = Wording(
        changed.get("title", base.title),
        changed.get("body", base.body),
        changed.get("sms", base.sms),
    )
    with pytest.raises(InvalidWording) as raised:
        check(event_name, wording)
    return raised.value.problems


def test_every_event_has_words_and_every_default_passes_the_rules_it_sets_for_staff() -> None:
    assert set(DEFAULTS) == set(EVENTS)
    for name in EDITABLE:
        check(name, DEFAULTS[name])


@pytest.mark.parametrize("name", sorted(EVENTS))
def test_every_default_renders_with_its_own_variables_and_leaves_no_placeholder(name: str) -> None:
    spec = EVENTS[name]
    rendered = render(spec, DEFAULTS[name], sample_values(spec))
    for part in (rendered.title, rendered.body, rendered.sms):
        assert part and "{" not in part and "}" not in part
    assert len(rendered.sms) <= MAX_RENDERED_SMS


def test_an_unknown_event_is_refused() -> None:
    with pytest.raises(UnknownEvent):
        event("free_money")


def test_money_dates_and_counts_are_written_one_way() -> None:
    assert format_value(Money(1_000_000, Currency.UGX)) == "UGX 1,000,000"
    assert format_value(Money(574, Currency.USD)) == "$5.74"
    assert format_value(Money(100_000, Currency.USD)) == "$1,000.00"
    assert format_value(Money(-2_416, Currency.UGX)) == "-UGX 2,416"
    assert format_value(date(2026, 11, 1)) == "1 Nov 2026"
    assert format_value(1200) == "1,200"
    assert format_value("  MTN   Mobile Money ") == "MTN Mobile Money"


@pytest.mark.parametrize("value", [1.5, True, None, "", " ", b"1"])
def test_a_value_that_is_not_text_a_whole_number_money_or_a_date_is_refused(value: object) -> None:
    with pytest.raises(InvalidVariables):
        format_value(value)  # type: ignore[arg-type]


def test_the_variables_must_be_exactly_the_events() -> None:
    spec = EVENTS["payment_confirmed"]
    amount = Money(5_000, Currency.UGX)
    with pytest.raises(InvalidVariables):
        render(spec, DEFAULTS[spec.name], {"amount": amount})
    with pytest.raises(InvalidVariables):
        render(spec, DEFAULTS[spec.name], {"amount": amount, "method": "MTN", "extra": "x"})


def test_a_value_cannot_smuggle_in_a_placeholder_or_be_filled_twice() -> None:
    spec = EVENTS["announcement"]
    rendered = render(
        spec,
        Wording("{title}", "{message} {title}", "{message}"),
        {"title": "Hello", "message": "{title} amount"},
    )
    assert rendered.body == "(title) amount Hello"


def test_a_long_value_is_cut_not_refused() -> None:
    assert format_value("x" * (MAX_VALUE + 1)) == "x" * (MAX_VALUE - 3) + "..."
    assert format_value("x" * MAX_VALUE) == "x" * MAX_VALUE


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "phone. Verify at http://evil.example/finvest",
            f"phone. Verify at {LINK_REMOVED}",
        ),
        ("phone, see www.evil.example", f"phone, see {LINK_REMOVED}"),
        ("phone evil.example/finvest?x=1 now", f"phone {LINK_REMOVED} now"),
        ("https://finvestug.com@evil.io/login", LINK_REMOVED),
        ("HTTPS://FINVESTUG.COM.EVIL.IO", LINK_REMOVED),
        ("see https://app.finvestug.com/help", "see https://app.finvestug.com/help"),
        ("MTN Mobile Money", "MTN Mobile Money"),
        ("Stanbic account ending 4821", "Stanbic account ending 4821"),
        ("Windows 10.0 laptop", "Windows 10.0 laptop"),
    ],
)
def test_a_text_value_cannot_carry_a_link_out_of_finvest(value: str, expected: str) -> None:
    assert format_value(value) == expected


def test_a_security_message_cannot_be_written_by_whoever_names_the_device() -> None:
    spec = EVENTS["new_sign_in"]
    rendered = render(
        spec, DEFAULTS[spec.name], {"device": "phone. Verify at http://evil.example/finvest"}
    )
    for part in (rendered.title, rendered.body, rendered.sms):
        assert "evil" not in part and "http" not in part


@pytest.mark.parametrize("name", sorted(EVENTS))
def test_the_longest_values_through_the_longest_wording_still_fit_what_is_stored(
    name: str,
) -> None:
    spec = EVENTS[name]
    slots = "".join(f"{{{variable}}}" for variable in spec.variables)

    def longest(limit: int) -> str:
        return slots + "x" * (limit - len(slots))

    wording = Wording(longest(MAX_TITLE), longest(MAX_BODY), longest(MAX_SMS))
    if name in EDITABLE:
        check(name, wording)
    rendered = render(spec, wording, dict.fromkeys(spec.variables, "y" * 5000))
    assert len(rendered.title) <= MAX_RENDERED_TITLE
    assert len(rendered.body) <= MAX_RENDERED_BODY
    assert len(rendered.sms) <= MAX_RENDERED_SMS


def test_a_long_text_message_is_cut_to_what_one_send_accepts() -> None:
    spec = EVENTS["announcement"]
    wording = Wording("{title}", "{message}", "{message} {message}")
    rendered = render(spec, wording, {"title": "News", "message": "word " * 48})
    assert len(rendered.sms) == MAX_RENDERED_SMS
    assert rendered.sms.endswith("...")


def test_a_placeholder_is_used_once_in_each_text() -> None:
    assert problems("payout_sent", body="{destination} " * 20 + "done") == [
        ("body", "repeated_placeholder")
    ]
    assert problems("payout_sent", title="{amount} {amount}") == [("title", "repeated_placeholder")]


def test_an_edit_may_use_only_the_events_own_placeholders() -> None:
    assert problems("payment_confirmed", body="We got {amount} for {product}.") == [
        ("body", "unknown_placeholder")
    ]
    assert problems("payment_confirmed", body="We got {amount!r}") == [("body", "stray_brace")]
    assert problems("payment_confirmed", title="{") == [("title", "stray_brace")]


def test_an_edit_cannot_be_blank_too_long_or_padded() -> None:
    assert problems("kyc_approved", title=" ") == [("title", "required")]
    assert problems("kyc_approved", title="x" * 61) == [("title", "too_long")]
    assert problems("kyc_approved", body="x" * 301) == [("body", "too_long")]
    assert problems("kyc_approved", sms="x" * 301) == [("sms", "too_long")]
    assert problems("kyc_approved", body="Approved.\nNow invest.") == [
        ("body", "invalid_characters")
    ]
    assert problems("kyc_approved", body=" Approved.") == [("body", "invalid_characters")]


@pytest.mark.parametrize(
    "text",
    [
        "Confirm your PIN at https://finvest-ug.com/login",
        "Confirm at www.finvestug.com.evil.io",
        "Go to https://finvestug.com@evil.io/login",
        "Reply at evil.example/finvest",
        "Visit bit.ly/abc to claim",
        "See HTTP://EXAMPLE.ORG",
    ],
)
def test_an_edit_can_only_link_to_finvests_own_site(text: str) -> None:
    assert ("body", "outside_link") in problems("kyc_approved", body=text)


def test_finvests_own_site_is_allowed() -> None:
    wording = Wording(
        "Approved", "Read more at https://finvestug.com/help", "See app.finvestug.com"
    )
    assert check("kyc_approved", wording) is wording


def test_an_edit_never_shows_a_rate() -> None:
    assert problems("interest_earned", body="After 15% tax.") == [("body", "no_rates")]


def test_a_text_message_stays_in_the_basic_alphabet() -> None:
    assert problems("kyc_approved", sms="Approved ✓") == [("sms", "invalid_characters")]


def test_an_announcement_has_no_wording_to_edit() -> None:
    assert "announcement" not in EDITABLE
    with pytest.raises(InvalidWording) as raised:
        check("announcement", DEFAULTS["announcement"])
    assert raised.value.problems == [("event", "not_editable")]


def test_a_client_who_chose_nothing_gets_the_defaults_for_every_kind_of_alert() -> None:
    assert effective({}) == CHANNEL_DEFAULTS
    assert set(CHANNEL_DEFAULTS) == set(Category)
    chosen = effective({Category.MONEY: frozenset()})
    assert chosen[Category.MONEY] == frozenset()
    assert chosen[Category.ACCOUNT] == CHANNEL_DEFAULTS[Category.ACCOUNT]


def test_the_defaults_pass_the_rules() -> None:
    for category, channels in CHANNEL_DEFAULTS.items():
        assert check_channels(category, channels) == channels


def test_a_security_alert_always_keeps_sms_or_email() -> None:
    for channels in (frozenset[Channel](), frozenset({Channel.PUSH})):
        with pytest.raises(InvalidPreference) as raised:
            check_channels(Category.SECURITY, channels)
        assert raised.value.code == "security_needs_sms_or_email"
    assert check_channels(Category.SECURITY, frozenset({Channel.EMAIL}))
    assert check_channels(Category.MONEY, frozenset()) == frozenset()


def test_the_app_knows_which_switch_cannot_be_turned_off() -> None:
    only_sms = frozenset({Channel.PUSH, Channel.SMS})
    assert not can_change(Category.SECURITY, Channel.SMS, only_sms)
    assert can_change(Category.SECURITY, Channel.PUSH, only_sms)
    assert can_change(Category.SECURITY, Channel.EMAIL, only_sms)
    both = frozenset({Channel.SMS, Channel.EMAIL})
    assert can_change(Category.SECURITY, Channel.SMS, both)
    assert can_change(Category.MONEY, Channel.SMS, frozenset({Channel.SMS}))


@pytest.mark.parametrize("name", ["investment_withdrawal_requested", "investment_withdrawal_paid"])
def test_early_withdrawal_notices_carry_no_amount(name: str) -> None:
    # Push and email use the in-app words, and SMS has its own, so no channel shows the amount.
    spec = EVENTS[name]
    values = sample_values(spec)
    rendered = render(spec, DEFAULTS[name], values)
    amount = format_value(values["amount"])
    for part in (rendered.title, rendered.body, rendered.sms):
        assert amount not in part
