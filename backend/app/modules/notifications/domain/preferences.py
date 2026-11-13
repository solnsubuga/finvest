"""Which channels carry which kind of alert. Pure: no I/O.

Every notification is kept in the app's notification centre whatever the client chooses. Push,
SMS and email are chosen per kind of alert. Security alerts are the exception: they always go
out by at least one channel that reaches the client outside the app, because the point of one
is to warn a client that someone else may be in their account.
"""

from collections.abc import Mapping

from app.modules.notifications.domain.events import Category, Channel

CHANNELS: tuple[Channel, ...] = (Channel.PUSH, Channel.SMS, Channel.EMAIL)

# What a client gets before they choose anything. SMS is on only where a delay costs the
# client money or safety, because each text is paid for.
DEFAULTS: dict[Category, frozenset[Channel]] = {
    Category.MONEY: frozenset({Channel.PUSH, Channel.SMS}),
    Category.INVESTMENTS: frozenset({Channel.PUSH}),
    Category.EARNINGS: frozenset(),
    Category.ACCOUNT: frozenset({Channel.PUSH, Channel.EMAIL}),
    Category.SECURITY: frozenset({Channel.PUSH, Channel.SMS, Channel.EMAIL}),
    Category.SUPPORT: frozenset({Channel.PUSH}),
    Category.NEWS: frozenset({Channel.PUSH}),
}

# Kinds of alert that can never be silenced, and the channels that count as reaching the
# client outside the app for them.
ALWAYS_ON: frozenset[Category] = frozenset({Category.SECURITY})
OUTSIDE_THE_APP: frozenset[Channel] = frozenset({Channel.SMS, Channel.EMAIL})


class InvalidPreference(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def check(category: Category, channels: frozenset[Channel]) -> frozenset[Channel]:
    """The channels as they may be saved, or `InvalidPreference` saying why not."""
    if category in ALWAYS_ON and not channels & OUTSIDE_THE_APP:
        raise InvalidPreference("security_needs_sms_or_email")
    return channels


def effective(
    saved: Mapping[Category, frozenset[Channel]],
) -> dict[Category, frozenset[Channel]]:
    """Every kind of alert with its channels: the client's choice, or the default."""
    return {category: saved.get(category, DEFAULTS[category]) for category in Category}


def can_change(category: Category, channel: Channel, channels: frozenset[Channel]) -> bool:
    """Whether the app should let the client switch `channel` off for `category`."""
    if channel not in channels:
        return True
    return not (category in ALWAYS_ON and not (channels - {channel}) & OUTSIDE_THE_APP)
