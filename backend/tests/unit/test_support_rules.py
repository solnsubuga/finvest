"""Help chat and complaint rules. No database."""

from datetime import date

import pytest

from app.modules.support.domain.chat import (
    MAX_MESSAGE,
    InvalidMessage,
    check_key,
    clean_message,
    first_name,
)
from app.modules.support.domain.complaints import (
    STATUSES,
    Closure,
    InvalidComplaint,
    NewComplaint,
    add_working_days,
    can_move,
    is_overdue,
    reply_due,
    working_days_between,
)
from app.shared.money import Currency, Money


def test_a_message_is_trimmed_and_its_line_endings_made_the_same() -> None:
    assert clean_message("  Hello\r\nthere\r ") == "Hello\nthere"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("", "required"),
        (" \n ", "required"),
        ("x" * (MAX_MESSAGE + 1), "too_long"),
        ("a\x00b", "invalid_characters"),
    ],
)
def test_an_empty_too_long_or_unprintable_message_is_refused(body: str, code: str) -> None:
    with pytest.raises(InvalidMessage) as raised:
        clean_message(body)
    assert (raised.value.field, raised.value.code) == ("body", code)


def test_the_longest_message_is_accepted() -> None:
    assert len(clean_message("x" * MAX_MESSAGE)) == MAX_MESSAGE


@pytest.mark.parametrize("key", ["short", "", "has space in it", "x" * 65, "semi;colon-key"])
def test_a_message_key_is_an_opaque_token(key: str) -> None:
    with pytest.raises(InvalidMessage):
        check_key(key)
    assert check_key("0b7e9a52-51f1-4c7b-9d0e-6a1f1b8e2c11")


def test_a_client_sees_a_first_name_only() -> None:
    assert first_name("Grace Auma") == "Grace"
    assert first_name("  Daniel   Okello Mugisha ") == "Daniel"
    assert first_name(" ") == "Finvest"


def test_a_reply_is_due_five_working_days_after_it_is_received() -> None:
    assert reply_due(date(2026, 11, 9)) == date(2026, 11, 16)  # Monday to Monday
    assert reply_due(date(2026, 11, 13)) == date(2026, 11, 20)  # Friday to Friday
    assert reply_due(date(2026, 11, 14)) == date(2026, 11, 20)  # Saturday counts from Monday
    assert reply_due(date(2026, 12, 30)) == date(2027, 1, 6)
    assert add_working_days(date(2026, 11, 13), 0) == date(2026, 11, 13)
    assert add_working_days(date(2026, 11, 13), 1) == date(2026, 11, 16)
    with pytest.raises(ValueError, match="forwards"):
        add_working_days(date(2026, 11, 13), -1)


def test_working_days_between_skips_weekends() -> None:
    assert working_days_between(date(2026, 11, 13), date(2026, 11, 13)) == 0
    assert working_days_between(date(2026, 11, 13), date(2026, 11, 16)) == 1
    assert working_days_between(date(2026, 11, 9), date(2026, 11, 20)) == 9
    assert working_days_between(date(2026, 11, 16), date(2026, 11, 13)) == 0
    for start in (date(2026, 11, day) for day in range(9, 16)):
        assert working_days_between(start, reply_due(start)) == 5


def test_a_complaint_is_overdue_the_day_after_its_deadline_until_it_is_closed() -> None:
    due = date(2026, 11, 20)
    assert not is_overdue("investigating", due, due)
    assert is_overdue("investigating", due, date(2026, 11, 21))
    assert is_overdue("waiting_for_client", due, date(2026, 11, 21))
    assert not is_overdue("closed", due, date(2026, 12, 21))


def test_the_moves_a_complaint_can_make() -> None:
    allowed = {(a, b) for a in STATUSES for b in STATUSES if can_move(a, b)}
    assert allowed == {
        ("new", "investigating"),
        ("new", "waiting_for_client"),
        ("new", "escalated"),
        ("new", "closed"),
        ("investigating", "waiting_for_client"),
        ("investigating", "escalated"),
        ("investigating", "closed"),
        ("waiting_for_client", "investigating"),
        ("waiting_for_client", "escalated"),
        ("waiting_for_client", "closed"),
        ("escalated", "investigating"),
        ("escalated", "waiting_for_client"),
        ("escalated", "closed"),
    }


def test_a_new_complaint_is_cleaned_and_checked() -> None:
    new = NewComplaint(
        "payments", "  Payout   did not arrive ", " It has been a week.\r\n", "phone"
    )
    assert (new.summary, new.details) == ("Payout did not arrive", "It has been a week.")
    assert NewComplaint("fees", "Fee", "Too high", "app", Money(5_000, Currency.UGX)).amount

    def code(**changed: object) -> tuple[str, str]:
        values: dict[str, object] = {
            "category": "payments",
            "summary": "Payout",
            "details": "Late",
            "received_via": "app",
            **changed,
        }
        with pytest.raises(InvalidComplaint) as raised:
            NewComplaint(**values)  # type: ignore[arg-type]
        return raised.value.field, raised.value.code

    assert code(category="weather") == ("category", "unknown")
    assert code(received_via="fax") == ("received_via", "unknown")
    assert code(summary=" ") == ("summary", "required")
    assert code(summary="x" * 121) == ("summary", "too_long")
    assert code(details="") == ("details", "required")
    assert code(details="x" * 4001) == ("details", "too_long")
    assert code(amount=Money(0, Currency.UGX)) == ("amount", "must_be_positive")


def test_closing_needs_an_outcome_what_was_done_and_whether_the_client_accepted() -> None:
    assert Closure("upheld", " Paid again. ", True).resolution == "Paid again."
    for arguments, field in (
        (("maybe", "Done", True), "outcome"),
        (("upheld", "  ", True), "resolution"),
        (("upheld", "Done", None), "client_accepted"),
        (("upheld", "Done", 1), "client_accepted"),
    ):
        with pytest.raises(InvalidComplaint) as raised:
            Closure(*arguments)
        assert raised.value.field == field
