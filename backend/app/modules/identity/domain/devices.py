"""A short, fixed label for the device a session was opened on. Pure.

The label is built only from known words, never copied from the request, so a crafted
User-Agent cannot put text of its own into a security email or the sessions list.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

KAMPALA = ZoneInfo("Africa/Kampala")

_APPS = (("Finvest/", "Finvest app"),)
_BROWSERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("SamsungBrowser/", "Samsung Internet"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
_SYSTEMS = (
    ("Android", "Android"),
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Windows", "Windows"),
    ("Macintosh", "Mac"),
    ("CrOS", "ChromeOS"),
    ("Linux", "Linux"),
)
UNKNOWN = "Unknown device"


def _first(text: str, known: tuple[tuple[str, str], ...]) -> str | None:
    return next((label for marker, label in known if marker in text), None)


def device_label(user_agent: str | None) -> str:
    """For example "Chrome on Android", "Finvest app on iPhone" or "Unknown device"."""
    text = (user_agent or "")[:512]
    program = _first(text, _APPS) or _first(text, _BROWSERS)
    system = _first(text, _SYSTEMS)
    if program and system:
        return f"{program} on {system}"
    return program or system or UNKNOWN


def kampala_time(moment: datetime) -> str:
    """A time as people in Uganda read it, for example "13 Nov 2026 at 21:30 (Kampala time)"."""
    local = moment.astimezone(KAMPALA)
    return f"{local.day} {local:%b %Y} at {local:%H:%M} (Kampala time)"
