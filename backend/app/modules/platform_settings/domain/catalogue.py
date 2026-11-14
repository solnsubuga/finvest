"""Every platform setting, its default and its rules. Pure: no I/O.

A setting is one named value that staff change on the console's Settings screen. One person
proposes a value and a different person approves it. Until a first value is approved, the
default here is in use, so a new deployment runs exactly as the code says.

Each value is checked here when it is proposed and again when it is approved, and stored as
JSON. `parse` reads the stored JSON back into the typed value the other modules use.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from typing import Any, Literal, get_args

from app.modules.identity.domain.permissions import Permission

Key = Literal[
    "payment_channels",
    "public_holidays",
    "bank_payout_cutoff",
    "maturity_reminders",
    "staff_idle_minutes",
    "office_networks",
    "statement_layouts",
]
KEYS: tuple[Key, ...] = get_args(Key)

# The section of the Settings screen a setting sits in. Each section has its own permission, so
# a role can be given one part of Settings and not the rest.
Section = Literal["channels", "calendar", "staff_security", "statement_import"]
SECTIONS: dict[Key, Section] = {
    "payment_channels": "channels",
    "public_holidays": "calendar",
    "bank_payout_cutoff": "calendar",
    "maturity_reminders": "calendar",
    "staff_idle_minutes": "staff_security",
    "office_networks": "staff_security",
    "statement_layouts": "statement_import",
}
PERMISSIONS: dict[Section, Permission] = {
    "channels": "payment_channels.change",
    "calendar": "calendar.change",
    "staff_security": "staff_security.change",
    "statement_import": "statement_layouts.change",
}

MAX_REASON = 500


class InvalidSetting(ValueError):
    """A value that cannot be used. `problems` pairs a field path with a safe code."""

    def __init__(self, problems: list[tuple[list[str | int], str]]) -> None:
        super().__init__(", ".join(f"{'.'.join(map(str, path))}:{code}" for path, code in problems))
        self.problems = problems


def _refuse(path: list[str | int], code: str) -> InvalidSetting:
    return InvalidSetting([(path, code)])


# Payment channels ------------------------------------------------------------------------------

Channel = Literal[
    "mtn_pay_in", "airtel_pay_in", "bank_pay_in", "mtn_payout", "airtel_payout", "bank_payout"
]
CHANNELS: tuple[Channel, ...] = get_args(Channel)


@dataclass(frozen=True, slots=True)
class PaymentChannels:
    """Which ways of paying in and paying out are open. A paused one refuses new requests.

    Pausing never touches money already on its way: a payment or payout already started
    finishes, or fails, as it would have.
    """

    open: frozenset[Channel]

    def is_open(self, channel: Channel) -> bool:
        return channel in self.open


# Which channel a mobile money network uses, for paying in and for paying out.
PAY_IN_BY_NETWORK: dict[str, Channel] = {"mtn": "mtn_pay_in", "airtel": "airtel_pay_in"}
PAYOUT_BY_NETWORK: dict[str, Channel] = {"mtn": "mtn_payout", "airtel": "airtel_payout"}


def _channels(value: Any) -> PaymentChannels:
    if not isinstance(value, dict) or set(value) != set(CHANNELS):
        raise _refuse(["value"], "channels_incomplete")
    for channel in CHANNELS:
        if not isinstance(value[channel], bool):
            raise _refuse(["value", channel], "not_a_switch")
    return PaymentChannels(frozenset(c for c in CHANNELS if value[c]))


def _dump_channels(value: PaymentChannels) -> dict[str, bool]:
    return {channel: channel in value.open for channel in CHANNELS}


# Public holidays -------------------------------------------------------------------------------

MAX_HOLIDAYS = 60
MAX_HOLIDAY_NAME = 80


@dataclass(frozen=True, slots=True)
class Holiday:
    day: date
    name: str


@dataclass(frozen=True, slots=True)
class PublicHolidays:
    """Days that are not working days, as well as Saturdays and Sundays. Oldest first."""

    days: tuple[Holiday, ...]

    def is_holiday(self, day: date) -> bool:
        return any(holiday.day == day for holiday in self.days)

    def is_working_day(self, day: date) -> bool:
        return day.weekday() < 5 and not self.is_holiday(day)


def _holidays(value: Any) -> PublicHolidays:
    if not isinstance(value, list):
        raise _refuse(["value"], "not_a_list")
    if len(value) > MAX_HOLIDAYS:
        raise _refuse(["value"], "too_many")
    found: list[Holiday] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"date", "name"}:
            raise _refuse(["value", index], "invalid")
        try:
            day = date.fromisoformat(item["date"]) if isinstance(item["date"], str) else None
        except ValueError:
            day = None
        if day is None or not 2020 <= day.year <= 2100:
            raise _refuse(["value", index, "date"], "invalid")
        name = item["name"].strip() if isinstance(item["name"], str) else ""
        if not name or len(name) > MAX_HOLIDAY_NAME:
            raise _refuse(["value", index, "name"], "required")
        found.append(Holiday(day, name))
    found.sort(key=lambda holiday: holiday.day)
    for earlier, later in pairwise(found):
        if earlier.day == later.day:
            raise _refuse(["value"], "same_day_twice")
    return PublicHolidays(tuple(found))


def _dump_holidays(value: PublicHolidays) -> list[dict[str, str]]:
    return [{"date": h.day.isoformat(), "name": h.name} for h in value.days]


def _easter(year: int) -> date:
    """Western Easter Sunday (the anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _uganda_holidays(year: int) -> list[Holiday]:
    """The public holidays whose dates are known in advance.

    The two Eid holidays follow the moon and are gazetted each year, so staff add them when the
    date is announced.
    """
    easter = _easter(year)
    fixed = [
        (1, 1, "New Year's Day"),
        (1, 26, "Liberation Day"),
        (2, 16, "Archbishop Janani Luwum Day"),
        (3, 8, "International Women's Day"),
        (5, 1, "Labour Day"),
        (6, 3, "Uganda Martyrs' Day"),
        (6, 9, "National Heroes' Day"),
        (10, 9, "Independence Day"),
        (12, 25, "Christmas Day"),
        (12, 26, "Boxing Day"),
    ]
    days = [Holiday(date(year, month, day), name) for month, day, name in fixed]
    days += [
        Holiday(easter - timedelta(days=2), "Good Friday"),
        Holiday(easter + timedelta(days=1), "Easter Monday"),
    ]
    return sorted(days, key=lambda holiday: holiday.day)


# Bank payout cut-off ---------------------------------------------------------------------------

EARLIEST_CUTOFF = time(8, 0)
LATEST_CUTOFF = time(17, 0)
_CLOCK = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")


@dataclass(frozen=True, slots=True)
class BankPayoutCutoff:
    """The Kampala time after which a bank payout is paid on the next working day."""

    at: time


def _cutoff(value: Any) -> BankPayoutCutoff:
    match = _CLOCK.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise _refuse(["value"], "invalid")
    at = time(int(match[1]), int(match[2]))
    if not EARLIEST_CUTOFF <= at <= LATEST_CUTOFF:
        raise _refuse(["value"], "outside_banking_hours")
    return BankPayoutCutoff(at)


# Maturity reminders ----------------------------------------------------------------------------

MAX_REMINDERS = 3
MAX_DAYS_BEFORE = 30


@dataclass(frozen=True, slots=True)
class MaturityReminders:
    """How many days before an investment matures the client is reminded. Largest first.

    Empty means no reminders are sent.
    """

    days_before: tuple[int, ...]


def _reminders(value: Any) -> MaturityReminders:
    if not isinstance(value, list):
        raise _refuse(["value"], "not_a_list")
    if len(value) > MAX_REMINDERS:
        raise _refuse(["value"], "too_many")
    for index, days in enumerate(value):
        if type(days) is not int or not 1 <= days <= MAX_DAYS_BEFORE:
            raise _refuse(["value", index], "invalid")
    if len(set(value)) != len(value):
        raise _refuse(["value"], "same_day_twice")
    return MaturityReminders(tuple(sorted(value, reverse=True)))


# Staff idle sign-out ---------------------------------------------------------------------------

MIN_IDLE_MINUTES = 5
MAX_IDLE_MINUTES = 240


@dataclass(frozen=True, slots=True)
class StaffIdleMinutes:
    """Minutes without use after which a member of staff is signed out of the console."""

    minutes: int


def _idle(value: Any) -> StaffIdleMinutes:
    if type(value) is not int or not MIN_IDLE_MINUTES <= value <= MAX_IDLE_MINUTES:
        raise _refuse(["value"], "invalid")
    return StaffIdleMinutes(value)


# Office networks -------------------------------------------------------------------------------

MAX_NETWORKS = 20
MAX_NETWORK_LABEL = 80
# Shorter prefixes than these let in a large part of the internet, which is no rule at all.
_SHORTEST_PREFIX = {4: 8, 6: 32}

IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True, slots=True)
class OfficeNetwork:
    network: IpNetwork
    label: str


@dataclass(frozen=True, slots=True)
class OfficeNetworks:
    """The networks staff may use the console from. Empty means any network.

    Staff sign in with a passkey or authenticator app wherever they are. This narrows the
    console to the office for an organisation that wants it.
    """

    networks: tuple[OfficeNetwork, ...]

    def allows(self, address: str | None) -> bool:
        if not self.networks:
            return True
        if address is None:
            return False
        try:
            caller = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(caller in entry.network for entry in self.networks)


def _networks(value: Any) -> OfficeNetworks:
    if not isinstance(value, list):
        raise _refuse(["value"], "not_a_list")
    if len(value) > MAX_NETWORKS:
        raise _refuse(["value"], "too_many")
    found: list[OfficeNetwork] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"network", "label"}:
            raise _refuse(["value", index], "invalid")
        try:
            network = ipaddress.ip_network(item["network"].strip(), strict=False)
        except (AttributeError, ValueError):
            raise _refuse(["value", index, "network"], "invalid") from None
        if network.prefixlen < _SHORTEST_PREFIX[network.version]:
            raise _refuse(["value", index, "network"], "too_wide")
        label = item["label"].strip() if isinstance(item["label"], str) else ""
        if not label or len(label) > MAX_NETWORK_LABEL:
            raise _refuse(["value", index, "label"], "required")
        if any(entry.network == network for entry in found):
            raise _refuse(["value", index, "network"], "listed_twice")
        found.append(OfficeNetwork(network, label))
    return OfficeNetworks(tuple(found))


def _dump_networks(value: OfficeNetworks) -> list[dict[str, str]]:
    return [{"network": str(entry.network), "label": entry.label} for entry in value.networks]


# Statement layouts -----------------------------------------------------------------------------

MAX_LAYOUTS = 10
MAX_COLUMN = 60
MAX_LAYOUT_LABEL = 80
# The built-in layout. A saved layout never takes its name.
STANDARD_LAYOUT = "standard"
_LAYOUT_NAME = re.compile(r"[a-z][a-z0-9_]{1,39}")
# How a bank prints a date. Offered as a choice, so nobody types a strptime pattern.
DATE_FORMATS: dict[str, str] = {
    "YYYY-MM-DD": "%Y-%m-%d",
    "DD/MM/YYYY": "%d/%m/%Y",
    "DD-MM-YYYY": "%d-%m-%Y",
    "DD.MM.YYYY": "%d.%m.%Y",
    "MM/DD/YYYY": "%m/%d/%Y",
    "DD MMM YYYY": "%d %b %Y",
    "DD-MMM-YYYY": "%d-%b-%Y",
}


@dataclass(frozen=True, slots=True)
class StatementLayout:
    """Which columns of one provider's CSV export hold what.

    Either `amount` with `direction` and the two words that mean in and out, or separate
    `money_in` and `money_out` columns. Reconciliation turns it into its own CSV format.
    """

    name: str
    label: str
    date_column: str
    date_format: str  # one of DATE_FORMATS' keys
    narrative: str
    reference: str | None
    money_in: str | None
    money_out: str | None
    amount: str | None
    direction: str | None
    in_word: str | None
    out_word: str | None

    @property
    def strptime(self) -> str:
        return DATE_FORMATS[self.date_format]


@dataclass(frozen=True, slots=True)
class StatementLayouts:
    layouts: tuple[StatementLayout, ...]

    def find(self, name: str) -> StatementLayout | None:
        return next((layout for layout in self.layouts if layout.name == name), None)


_LAYOUT_FIELDS = {
    "name",
    "label",
    "date_column",
    "date_format",
    "narrative",
    "reference",
    "money_in",
    "money_out",
    "amount",
    "direction",
    "in_word",
    "out_word",
}


def _column(item: Mapping[str, Any], field: str, path: list[str | int]) -> str | None:
    raw = item[field]
    if raw is None:
        return None
    text = raw.strip() if isinstance(raw, str) else ""
    if not text or len(text) > MAX_COLUMN:
        raise _refuse([*path, field], "invalid")
    return text


def _layout(item: Any, index: int) -> StatementLayout:
    path: list[str | int] = ["value", index]
    if not isinstance(item, dict) or set(item) != _LAYOUT_FIELDS:
        raise _refuse(path, "invalid")
    name = item["name"]
    if not isinstance(name, str) or _LAYOUT_NAME.fullmatch(name) is None:
        raise _refuse([*path, "name"], "invalid")
    if name == STANDARD_LAYOUT:
        raise _refuse([*path, "name"], "built_in")
    label = item["label"].strip() if isinstance(item["label"], str) else ""
    if not label or len(label) > MAX_LAYOUT_LABEL:
        raise _refuse([*path, "label"], "required")
    if item["date_format"] not in DATE_FORMATS:
        raise _refuse([*path, "date_format"], "invalid")
    columns = {field: _column(item, field, path) for field in _LAYOUT_FIELDS - {"name", "label"}}
    for required in ("date_column", "narrative"):
        if columns[required] is None:
            raise _refuse([*path, required], "required")
    split = columns["money_in"] is not None and columns["money_out"] is not None
    single = all(columns[f] is not None for f in ("amount", "direction", "in_word", "out_word"))
    if split == single or (
        split
        and any(columns[f] is not None for f in ("amount", "direction", "in_word", "out_word"))
    ):
        raise _refuse(path, "money_columns")
    if single and (columns["in_word"] or "").casefold() == (columns["out_word"] or "").casefold():
        raise _refuse([*path, "out_word"], "same_as_in_word")
    headers = [
        columns[f]
        for f in ("date_column", "narrative", "reference", "money_in", "money_out", "amount")
        if columns[f] is not None
    ] + ([columns["direction"]] if single else [])
    if len({header.casefold() for header in headers if header}) != len(headers):
        raise _refuse(path, "column_used_twice")
    return StatementLayout(
        name=name,
        label=label,
        date_column=columns["date_column"] or "",
        date_format=item["date_format"],
        narrative=columns["narrative"] or "",
        reference=columns["reference"],
        money_in=columns["money_in"],
        money_out=columns["money_out"],
        amount=columns["amount"],
        direction=columns["direction"],
        in_word=columns["in_word"],
        out_word=columns["out_word"],
    )


def _layouts(value: Any) -> StatementLayouts:
    if not isinstance(value, list):
        raise _refuse(["value"], "not_a_list")
    if len(value) > MAX_LAYOUTS:
        raise _refuse(["value"], "too_many")
    found = [_layout(item, index) for index, item in enumerate(value)]
    if len({layout.name for layout in found}) != len(found):
        raise _refuse(["value"], "same_name_twice")
    return StatementLayouts(tuple(found))


def _dump_layouts(value: StatementLayouts) -> list[dict[str, str | None]]:
    return [
        {
            "name": layout.name,
            "label": layout.label,
            "date_column": layout.date_column,
            "date_format": layout.date_format,
            "narrative": layout.narrative,
            "reference": layout.reference,
            "money_in": layout.money_in,
            "money_out": layout.money_out,
            "amount": layout.amount,
            "direction": layout.direction,
            "in_word": layout.in_word,
            "out_word": layout.out_word,
        }
        for layout in value.layouts
    ]


# The catalogue ---------------------------------------------------------------------------------

Value = (
    PaymentChannels
    | PublicHolidays
    | BankPayoutCutoff
    | MaturityReminders
    | StaffIdleMinutes
    | OfficeNetworks
    | StatementLayouts
)


@dataclass(frozen=True, slots=True)
class Definition:
    key: Key
    # Reads JSON into the typed value, or raises InvalidSetting.
    parse: Callable[[Any], Value]
    # Writes the typed value as JSON. `parse(dump(v)) == v` for every value.
    dump: Callable[[Any], Any]
    # The value in use until a first one is approved, given the year it is now.
    default: Callable[[int], Value]


DEFINITIONS: dict[Key, Definition] = {
    "payment_channels": Definition(
        "payment_channels",
        _channels,
        _dump_channels,
        lambda _year: PaymentChannels(frozenset(CHANNELS)),
    ),
    "public_holidays": Definition(
        "public_holidays",
        _holidays,
        _dump_holidays,
        lambda year: PublicHolidays(tuple(_uganda_holidays(year) + _uganda_holidays(year + 1))),
    ),
    "bank_payout_cutoff": Definition(
        "bank_payout_cutoff",
        _cutoff,
        lambda value: value.at.strftime("%H:%M"),
        lambda _year: BankPayoutCutoff(time(14, 0)),
    ),
    "maturity_reminders": Definition(
        "maturity_reminders",
        _reminders,
        lambda value: list(value.days_before),
        lambda _year: MaturityReminders((7, 1)),
    ),
    "staff_idle_minutes": Definition(
        "staff_idle_minutes",
        _idle,
        lambda value: value.minutes,
        # The same as the deploy setting's own default (`staff_session_idle_minutes`).
        lambda _year: StaffIdleMinutes(30),
    ),
    "office_networks": Definition(
        "office_networks",
        _networks,
        _dump_networks,
        lambda _year: OfficeNetworks(()),
    ),
    "statement_layouts": Definition(
        "statement_layouts",
        _layouts,
        _dump_layouts,
        lambda _year: StatementLayouts(()),
    ),
}


def checked(key: Key, value: Any) -> tuple[Value, Any]:
    """The value read and written back in its tidy form, or InvalidSetting."""
    definition = DEFINITIONS[key]
    parsed = definition.parse(value)
    return parsed, definition.dump(parsed)


def default(key: Key, now: datetime) -> Value:
    return DEFINITIONS[key].default(now.year)
