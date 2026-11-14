"""The platform settings catalogue: every value's rules, defaults and round trip."""

from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any

import pytest

from app.modules.identity.domain.permissions import PERMISSIONS as ALL_PERMISSIONS
from app.modules.identity.domain.permissions import permissions_for
from app.modules.platform_settings.domain.catalogue import (
    CHANNELS,
    DEFINITIONS,
    KEYS,
    PERMISSIONS,
    SECTIONS,
    BankPayoutCutoff,
    Holiday,
    InvalidSetting,
    MaturityReminders,
    OfficeNetworks,
    PaymentChannels,
    PublicHolidays,
    StatementLayouts,
    bank_payout_day,
    checked,
    default,
)

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


def problems(key: str, value: Any) -> list[tuple[list[str | int], str]]:
    with pytest.raises(InvalidSetting) as raised:
        checked(key, value)  # type: ignore[arg-type]
    return raised.value.problems


@pytest.mark.parametrize("key", KEYS)
def test_every_default_writes_and_reads_back_the_same(key: str) -> None:
    value = default(key, NOW)  # type: ignore[arg-type]
    stored = DEFINITIONS[key].dump(value)  # type: ignore[index]
    assert checked(key, stored) == (value, stored)  # type: ignore[arg-type]


def test_every_section_has_a_permission_that_exists_and_admin_holds() -> None:
    assert set(SECTIONS) == set(KEYS)
    for permission in PERMISSIONS.values():
        assert permission in ALL_PERMISSIONS
        assert permission in permissions_for("admin")
    # Staff security can lock everyone out, so it is for admins alone.
    for role in ("operations", "finance", "compliance", "support"):
        assert "staff_security.change" not in permissions_for(role)


def test_channels_are_all_open_by_default_and_must_all_be_named() -> None:
    assert default("payment_channels", NOW) == PaymentChannels(frozenset(CHANNELS))
    closed = dict.fromkeys(CHANNELS, True) | {"airtel_payout": False}
    value, _ = checked("payment_channels", closed)
    assert isinstance(value, PaymentChannels)
    assert not value.is_open("airtel_payout") and value.is_open("mtn_payout")
    assert problems("payment_channels", {"mtn_pay_in": True}) == [
        (["value"], "channels_incomplete")
    ]
    assert problems("payment_channels", closed | {"mtn_pay_in": 1}) == [
        (["value", "mtn_pay_in"], "not_a_switch")
    ]


def test_holidays_are_sorted_checked_and_count_as_non_working_days() -> None:
    value, stored = checked(
        "public_holidays",
        [
            {"date": "2026-12-25", "name": " Christmas Day "},
            {"date": "2026-10-09", "name": "Independence Day"},
        ],
    )
    assert stored == [
        {"date": "2026-10-09", "name": "Independence Day"},
        {"date": "2026-12-25", "name": "Christmas Day"},
    ]
    assert isinstance(value, PublicHolidays)
    assert not value.is_working_day(date(2026, 10, 9))  # a Friday holiday
    assert not value.is_working_day(date(2026, 10, 10))  # a Saturday
    assert value.is_working_day(date(2026, 10, 12))
    twice = [{"date": "2026-10-09", "name": "A"}, {"date": "2026-10-09", "name": "B"}]
    assert problems("public_holidays", twice) == [(["value"], "same_day_twice")]
    assert problems("public_holidays", [{"date": "2026-02-30", "name": "A"}]) == [
        (["value", 0, "date"], "invalid")
    ]
    assert problems("public_holidays", [{"date": "2026-02-03", "name": " "}]) == [
        (["value", 0, "name"], "required")
    ]
    assert problems("public_holidays", [{"date": "2026-02-03"}]) == [(["value", 0], "invalid")]


def test_the_default_holidays_include_easter_for_this_year_and_next() -> None:
    value = default("public_holidays", NOW)
    assert isinstance(value, PublicHolidays)
    named = {(h.day, h.name) for h in value.days}
    assert (date(2026, 4, 3), "Good Friday") in named
    assert (date(2026, 4, 6), "Easter Monday") in named
    assert (date(2027, 3, 26), "Good Friday") in named
    assert (date(2027, 10, 9), "Independence Day") in named
    assert {h.day.year for h in value.days} == {2026, 2027}


def test_the_cutoff_is_a_time_in_banking_hours() -> None:
    assert default("bank_payout_cutoff", NOW) == BankPayoutCutoff(time(14, 0))
    assert checked("bank_payout_cutoff", "15:30") == (BankPayoutCutoff(time(15, 30)), "15:30")
    for value in ("7:00", "25:00", "14:60", 1400, "07:59", "17:01"):
        assert problems("bank_payout_cutoff", value)[0][0] == ["value"]


def test_reminders_are_up_to_three_distinct_days_largest_first() -> None:
    assert default("maturity_reminders", NOW) == MaturityReminders((7, 1))
    assert checked("maturity_reminders", [1, 14, 3]) == (MaturityReminders((14, 3, 1)), [14, 3, 1])
    assert checked("maturity_reminders", []) == (MaturityReminders(()), [])
    assert problems("maturity_reminders", [1, 1]) == [(["value"], "same_day_twice")]
    assert problems("maturity_reminders", [0]) == [(["value", 0], "invalid")]
    assert problems("maturity_reminders", [31]) == [(["value", 0], "invalid")]
    assert problems("maturity_reminders", [True]) == [(["value", 0], "invalid")]
    assert problems("maturity_reminders", [1, 2, 3, 4]) == [(["value"], "too_many")]


def test_idle_minutes_stay_between_five_and_two_hundred_and_forty() -> None:
    assert checked("staff_idle_minutes", 15)[1] == 15
    for value in (4, 241, 15.0, "15", True):
        assert problems("staff_idle_minutes", value) == [(["value"], "invalid")]


def test_office_networks_are_tidied_and_an_empty_list_lets_anyone_in() -> None:
    empty, _ = checked("office_networks", [])
    assert isinstance(empty, OfficeNetworks) and empty.allows(None) and empty.allows("8.8.8.8")
    value, stored = checked(
        "office_networks",
        [
            {"network": "196.43.130.7/24", "label": "Head office"},
            {"network": "41.210.140.5", "label": " Branch "},
            {"network": "2c0f:fe38::/48", "label": "Office IPv6"},
        ],
    )
    assert stored == [
        {"network": "196.43.130.0/24", "label": "Head office"},
        {"network": "41.210.140.5/32", "label": "Branch"},
        {"network": "2c0f:fe38::/48", "label": "Office IPv6"},
    ]
    assert isinstance(value, OfficeNetworks)
    assert value.allows("196.43.130.200") and value.allows("41.210.140.5")
    assert value.allows("2c0f:fe38:0:1::9")
    assert not value.allows("41.210.140.6")
    assert not value.allows(None) and not value.allows("not an address")
    assert problems("office_networks", [{"network": "10.0.0.0/7", "label": "A"}]) == [
        (["value", 0, "network"], "too_wide")
    ]
    assert problems("office_networks", [{"network": "300.1.1.1", "label": "A"}]) == [
        (["value", 0, "network"], "invalid")
    ]
    twice = [{"network": "10.1.0.0/16", "label": "A"}, {"network": "10.1.2.3/16", "label": "B"}]
    assert problems("office_networks", twice) == [(["value", 1, "network"], "listed_twice")]


LAYOUT: dict[str, str | None] = {
    "name": "stanbic",
    "label": "Stanbic statement export",
    "date_column": "Txn Date",
    "date_format": "DD/MM/YYYY",
    "narrative": "Description",
    "reference": "Reference",
    "money_in": "Credit",
    "money_out": "Debit",
    "amount": None,
    "direction": None,
    "in_word": None,
    "out_word": None,
}


def test_a_statement_layout_names_its_money_columns_one_way_only() -> None:
    value, stored = checked("statement_layouts", [LAYOUT])
    assert isinstance(value, StatementLayouts) and stored == [LAYOUT]
    layout = value.find("stanbic")
    assert layout is not None and layout.strptime == "%d/%m/%Y"
    assert value.find("standard") is None

    single = LAYOUT | {
        "money_in": None,
        "money_out": None,
        "amount": "Amount",
        "direction": "Type",
        "in_word": "CR",
        "out_word": "DR",
    }
    assert checked("statement_layouts", [single])[1] == [single]
    both = single | {"money_in": "Credit", "money_out": "Debit"}
    assert problems("statement_layouts", [both]) == [(["value", 0], "money_columns")]
    neither = LAYOUT | {"money_in": None}
    assert problems("statement_layouts", [neither]) == [(["value", 0], "money_columns")]
    assert problems("statement_layouts", [single | {"out_word": "cr"}]) == [
        (["value", 0, "out_word"], "same_as_in_word")
    ]
    assert problems("statement_layouts", [LAYOUT | {"reference": "credit"}]) == [
        (["value", 0], "column_used_twice")
    ]
    assert problems("statement_layouts", [LAYOUT | {"name": "standard"}]) == [
        (["value", 0, "name"], "built_in")
    ]
    assert problems("statement_layouts", [LAYOUT | {"date_format": "%d/%m/%Y"}]) == [
        (["value", 0, "date_format"], "invalid")
    ]
    assert problems("statement_layouts", [LAYOUT, LAYOUT]) == [(["value"], "same_name_twice")]


def test_a_bank_payout_asked_for_after_the_cutoff_or_on_a_holiday_goes_the_next_working_day() -> (
    None
):
    eat = timezone(timedelta(hours=3))
    cutoff = BankPayoutCutoff(time(14, 0))
    holidays = PublicHolidays((Holiday(date(2026, 10, 9), "Independence Day"),))
    # Wednesday before and after the cut-off. 13:59 in Kampala is 10:59 UTC.
    assert bank_payout_day(datetime(2026, 10, 7, 10, 59, tzinfo=UTC), cutoff, holidays) == date(
        2026, 10, 7
    )
    assert bank_payout_day(datetime(2026, 10, 7, 14, 0, tzinfo=eat), cutoff, holidays) == date(
        2026, 10, 8
    )
    # Thursday after the cut-off: Friday is a holiday, so Monday.
    assert bank_payout_day(datetime(2026, 10, 8, 15, 0, tzinfo=eat), cutoff, holidays) == date(
        2026, 10, 12
    )
    # A Saturday morning goes on Monday.
    assert bank_payout_day(datetime(2026, 10, 10, 9, 0, tzinfo=eat), cutoff, holidays) == date(
        2026, 10, 12
    )
    with pytest.raises(ValueError):
        bank_payout_day(datetime(2026, 10, 7, 9, 0), cutoff, holidays)
