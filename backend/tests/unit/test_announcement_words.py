"""The words of an announcement follow the same rules as an edited wording."""

import pytest

from app.modules.notifications.domain.wording import (
    MAX_ANNOUNCEMENT_MESSAGE,
    MAX_ANNOUNCEMENT_TITLE,
    InvalidWording,
    check_announcement,
)

TITLE = "Office closed on Monday"
MESSAGE = "Our Kampala office is closed on Monday. The app works as usual."


def problems(title: str, message: str) -> list[tuple[str, str]]:
    with pytest.raises(InvalidWording) as refused:
        check_announcement(title, message)
    return refused.value.problems


def test_plain_words_pass() -> None:
    check_announcement(TITLE, MESSAGE)
    check_announcement("x" * MAX_ANNOUNCEMENT_TITLE, "y" * MAX_ANNOUNCEMENT_MESSAGE)
    check_announcement(TITLE, "Read more at https://finvestug.com/help or www.finvestug.com.")


def test_the_sms_fits() -> None:
    # The SMS is "Finvest: " and the message, within the wordings' SMS limit of 300.
    assert len("Finvest: ") + MAX_ANNOUNCEMENT_MESSAGE == 300


@pytest.mark.parametrize(
    ("title", "message", "expected"),
    [
        ("", MESSAGE, [("title", "required")]),
        (TITLE, "   ", [("message", "required")]),
        ("x" * (MAX_ANNOUNCEMENT_TITLE + 1), MESSAGE, [("title", "too_long")]),
        (TITLE, "y" * (MAX_ANNOUNCEMENT_MESSAGE + 1), [("message", "too_long")]),
        (" Padded", MESSAGE, [("title", "invalid_characters")]),
        (TITLE, "Line one\nline two", [("message", "invalid_characters")]),
        (TITLE, "Go to https://example.com now", [("message", "outside_link")]),
        (TITLE, "Go to evil.example/finvest now", [("message", "outside_link")]),
        ("Rates up 2%", MESSAGE, [("title", "no_rates")]),
        (
            TITLE,
            "Hello {title}",
            [("message", "stray_brace")],
        ),
        ("Hi {name}", MESSAGE, [("title", "unknown_placeholder"), ("title", "stray_brace")]),
        (TITLE, "Karibu ✓", [("message", "invalid_characters")]),
    ],
)
def test_words_that_are_refused(title: str, message: str, expected: list[tuple[str, str]]) -> None:
    assert problems(title, message) == expected
