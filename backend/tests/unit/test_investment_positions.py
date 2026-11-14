"""The positions file: running investments as CSV, exact figures from integers."""

import csv
import io
from datetime import date

import pytest

from app.modules.investments.domain.positions import HEADER, PositionRow, percent, positions_csv
from app.shared.money import Currency, Money


def row(**changes: object) -> PositionRow:
    values: dict[str, object] = {
        "investment_id": "Pq31wXb7AAAAAAAAAAAA",
        "account_number": "1001004827",
        "product": "91 Day Treasury Bill",
        "invested": Money(1_000_000, Currency.UGX),
        "annual_rate_bps": 1250,
        "tax_bps": 1500,
        "start_date": date(2026, 11, 3),
        "maturity_date": date(2027, 2, 2),
        "term_days": 91,
        "accrued_through": date(2026, 11, 13),
        "interest_gross": Money(3_425, Currency.UGX),
        "tax": Money(514, Currency.UGX),
        "at_maturity": "roll_over",
        "interest_goal": None,
    }
    values.update(changes)
    return PositionRow(**values)  # type: ignore[arg-type]


def parsed(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_a_position_is_one_row_under_the_header() -> None:
    header, line = parsed(positions_csv([row()]))
    assert tuple(header) == HEADER
    assert line == [
        "Pq31wXb7AAAAAAAAAAAA",
        "1001004827",
        "91 Day Treasury Bill",
        "UGX",
        "1000000",
        "12.50",
        "15.00",
        "2026-11-03",
        "2027-02-02",
        "91",
        "2026-11-13",
        "3425",
        "514",
        "2911",
        "1002911",
        "roll_over",
        "",
    ]


def test_cents_and_missing_values() -> None:
    usd = Currency.USD
    [_, line] = parsed(
        positions_csv(
            [
                row(
                    invested=Money(123_456, usd),
                    interest_gross=Money(101, usd),
                    tax=Money(15, usd),
                    account_number=None,
                    start_date=None,
                    maturity_date=None,
                    term_days=None,
                    accrued_through=None,
                    at_maturity=None,
                )
            ]
        )
    )
    assert line[1] == "" and line[7:11] == ["", "", "", ""]
    assert line[4] == "1234.56" and line[11:15] == ["1.01", "0.15", "0.86", "1235.42"]


def test_text_a_spreadsheet_would_run_is_escaped() -> None:
    [_, line] = parsed(positions_csv([row(product="=HYPERLINK(1)", interest_goal="+Car")]))
    assert line[2] == "'=HYPERLINK(1)" and line[16] == "'+Car"


@pytest.mark.parametrize(
    ("bps", "shown"), [(0, "0.00"), (5, "0.05"), (975, "9.75"), (1250, "12.50")]
)
def test_rates_are_exact_percentages(bps: int, shown: str) -> None:
    assert percent(bps) == shown


def test_a_negative_rate_is_refused() -> None:
    with pytest.raises(ValueError):
        percent(-1)


def test_no_positions_is_just_the_header() -> None:
    assert parsed(positions_csv([])) == [list(HEADER)]
