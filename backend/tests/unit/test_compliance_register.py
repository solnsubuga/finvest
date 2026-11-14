"""The compliance register file and the amount a monitoring case is about."""

from datetime import UTC, datetime

from app.modules.screening.domain.monitoring import case_amount_minor
from app.modules.screening.domain.register import RegisterRow, register_csv


def test_a_case_amount_is_the_payment_or_the_total_of_split_deposits() -> None:
    assert case_amount_minor({"deposit_id": "d", "amount_minor": 25_000_000}) == 25_000_000
    assert case_amount_minor({"total_minor": 21_000_000, "deposit_ids": ["a", "b"]}) == 21_000_000
    assert case_amount_minor({}) is None
    assert case_amount_minor({"amount_minor": "25000000"}) is None
    assert case_amount_minor({"amount_minor": True}) is None


def test_the_register_has_one_row_per_case_in_kampala_time_and_no_formulas() -> None:
    rows = [
        RegisterRow(
            case_id="-Ncase1",
            opened_at=datetime(2026, 11, 13, 9, 20, tzinfo=UTC),
            source="monitoring",
            code="large_deposit",
            amount_minor=25_000_000,
            account_number="1001004827",
            name="=NAKATO Amara",
            status="closed",
            outcome="report_to_fia",
            closed_at=datetime(2026, 11, 14, 6, 0, tzinfo=UTC),
        ),
        RegisterRow(
            case_id="-Ncase2",
            opened_at=datetime(2026, 11, 12, 7, 40, tzinfo=UTC),
            source="screening",
            code="possible_match",
            amount_minor=None,
            account_number=None,
            name=None,
            status="open",
            outcome=None,
            closed_at=None,
        ),
    ]
    lines = register_csv(rows).split("\r\n")
    assert lines[0] == (
        "Case ID,Opened,Source,Raised by,Amount (UGX),Client account number,Client name,"
        "Status,Outcome,Closed"
    )
    assert lines[1] == (
        "-Ncase1,2026-11-13 12:20,monitoring,large_deposit,25000000,1001004827,'=NAKATO Amara,"
        "closed,report_to_fia,2026-11-14 09:00"
    )
    assert lines[2] == "-Ncase2,2026-11-12 10:40,screening,possible_match,,,,open,,"
    assert lines[3] == ""
