import pytest

from app.modules.identity.domain.devices import UNKNOWN, device_label

CHROME_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Mobile Safari/537.36"
)
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"
)
EDGE_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
)
FIREFOX_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.6; rv:132.0) Gecko/20100101 Firefox/132.0"


@pytest.mark.parametrize(
    ("agent", "label"),
    [
        (CHROME_ANDROID, "Chrome on Android"),
        (SAFARI_IPHONE, "Safari on iPhone"),
        (EDGE_WINDOWS, "Edge on Windows"),
        (FIREFOX_MAC, "Firefox on Mac"),
        ("Finvest/1.2 (iPhone; iOS 18.1)", "Finvest app on iPhone"),
        ("Finvest/1.2", "Finvest app"),
        ("curl/8.4.0", UNKNOWN),
        ("", UNKNOWN),
        (None, UNKNOWN),
    ],
)
def test_a_session_gets_a_label_from_known_words(agent: str | None, label: str) -> None:
    assert device_label(agent) == label


def test_nothing_from_the_request_is_copied_into_the_label() -> None:
    crafted = "Chrome/1 <a href=https://evil.example>Click here</a> Android"
    assert device_label(crafted) == "Chrome on Android"
    assert len(device_label("x" * 10_000)) <= 64
