from datetime import UTC, date, datetime

from app.modules.support.domain.register import HEADER, ComplaintRow, complaints_csv


def row(**changes: object) -> ComplaintRow:
    base: dict[str, object] = {
        "reference": "C-1042",
        "received_at": datetime(2026, 11, 14, 21, 30, tzinfo=UTC),
        "received_via": "app",
        "category": "withdrawals",
        "summary": "Payout did not arrive",
        "account_number": "1001005130",
        "name": "Peter Okello",
        "amount_minor": 150_000,
        "currency": "UGX",
        "status": "closed",
        "due_on": date(2026, 11, 21),
        "overdue": False,
        "closed_at": datetime(2026, 11, 15, 8, 0, tzinfo=UTC),
        "outcome": "upheld",
        "client_accepted": True,
    }
    base.update(changes)
    return ComplaintRow(**base)  # type: ignore[arg-type]


def test_one_line_per_complaint_in_kampala_time() -> None:
    lines = complaints_csv([row()]).split("\r\n")
    assert lines[0] == ",".join(HEADER)
    assert lines[1] == (
        "C-1042,2026-11-15 00:30,app,withdrawals,Payout did not arrive,1001005130,Peter Okello,"
        "150000,UGX,closed,2026-11-21,No,2026-11-15 11:00,upheld,Yes"
    )
    assert lines[2] == ""


def test_an_open_complaint_with_no_amount_leaves_those_cells_empty() -> None:
    line = complaints_csv(
        [
            row(
                amount_minor=None,
                currency=None,
                closed_at=None,
                outcome=None,
                client_accepted=None,
                overdue=True,
                status="new",
            )
        ]
    ).split("\r\n")[1]
    assert line.endswith(",,,new,2026-11-21,Yes,,,")


def test_text_a_spreadsheet_would_run_is_written_as_text() -> None:
    line = complaints_csv([row(summary='=HYPERLINK("x")', name="+Peter")]).split("\r\n")[1]
    assert "'=HYPERLINK" in line and ",'+Peter," in line
