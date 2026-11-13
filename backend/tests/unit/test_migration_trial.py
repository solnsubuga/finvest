from datetime import date

from app.modules.migration.domain.sheet import (
    CLIENT_COLUMNS,
    INVESTMENT_COLUMNS,
    Problem,
    Severity,
    Sheet,
)
from app.modules.migration.domain.trial import run_trial
from app.shared.money import Currency, Money
from tests.unit.test_migration_sheet import CLIENT, INVESTMENT, clients, investments

AS_OF = date(2026, 11, 13)
WARNING = Severity.WARNING


def client(ref: str, **changes: str) -> dict[str, str]:
    """A second, different person: every identifying cell is derived from `ref`."""
    return (
        CLIENT
        | {
            "legacy_client_ref": ref,
            "given_names": f"Person {ref}",
            "phone": f"+2567720{int(ref):05d}",
            "email": f"c{ref}@example.com",
            "id_number": f"CM{int(ref):010d}",
            "legacy_account_number": f"1{ref}",
        }
        | changes
    )


def investment(ref: str, client_ref: str, **changes: str) -> dict[str, str]:
    return INVESTMENT | {"legacy_investment_ref": ref, "legacy_client_ref": client_ref} | changes


PENDING = dict.fromkeys(INVESTMENT_COLUMNS[5:], "") | {"status": "pending_payment"}
DOLLARS = {
    "product_code": "USD-BOND",
    "principal": "600.00",
    "annual_rate_percent": "7.25",
    "tax_percent": "5",
    "maturity_date": "",
    "interest_before_tax": "4.05",
    "tax_withheld": "0.20",
}


def test_a_clean_file_has_no_problems_and_adds_up() -> None:
    report = run_trial(
        clients(client("1"), client("2", id_verified="no", id_expires_on="")),
        investments(
            investment("A", "1"),
            investment("B", "1", principal="2,500,000", interest_before_tax="20,000"),
            investment("C", "2", **DOLLARS),
            investment("D", "2", **PENDING),
        ),
        as_of=AS_OF,
    )

    assert report.errors == ()
    assert report.clean
    assert report.warnings == (
        Problem(Sheet.CLIENTS, 3, "id_verified", "id_not_verified", WARNING),
    )
    assert report.as_of == AS_OF
    summary = report.summary
    assert (summary.clients, summary.verified_identities, summary.clients_to_recheck) == (2, 1, 1)
    assert summary.clients_without_phone == 0
    assert (summary.running_investments, summary.pending_orders) == (3, 1)
    assert summary.invested == (Money(3_500_000, Currency.UGX), Money(600_00, Currency.USD))
    assert summary.interest_before_tax == (Money(31_643, Currency.UGX), Money(405, Currency.USD))


def test_two_empty_templates_are_clean_and_empty() -> None:
    report = run_trial(clients(), investments(), as_of=AS_OF)
    assert report.clean
    assert report.summary.clients == 0
    assert report.summary.invested == ()


def codes(report_problems: tuple[Problem, ...]) -> list[tuple[str, int | None, str | None, str]]:
    return [(p.sheet.value, p.row, p.column, p.code) for p in report_problems]


def test_an_investment_with_no_client_is_an_error() -> None:
    report = run_trial(
        clients(client("1")),
        investments(investment("A", "1"), investment("B", "404")),
        as_of=AS_OF,
    )
    assert codes(report.errors) == [("investments", 3, "legacy_client_ref", "client_unknown")]
    assert not report.clean
    assert report.summary.running_investments == 1


def test_a_client_row_with_an_error_does_not_orphan_its_investments() -> None:
    report = run_trial(
        clients(client("1", phone="bad")),
        investments(investment("A", "1")),
        as_of=AS_OF,
    )
    assert codes(report.errors) == [("clients", 2, "phone", "phone_invalid")]
    # The investment is held back with its client, so the totals never count money without one.
    assert report.investments == ()
    assert report.summary.running_investments == 0


def test_a_reference_used_twice_is_reported_on_every_row_that_uses_it() -> None:
    report = run_trial(
        clients(client("1"), client("2", legacy_client_ref="1")),
        investments(investment("A", "1"), investment("A", "1")),
        as_of=AS_OF,
    )
    assert codes(report.errors) == [
        ("clients", 2, "legacy_client_ref", "duplicate_reference"),
        ("clients", 3, "legacy_client_ref", "duplicate_reference"),
        ("investments", 2, "legacy_investment_ref", "duplicate_reference"),
        ("investments", 3, "legacy_investment_ref", "duplicate_reference"),
    ]
    assert report.clients == ()
    assert report.investments == ()


def test_the_same_person_entered_twice_is_caught() -> None:
    report = run_trial(
        clients(
            client("1"),
            client("2", phone=client("1")["phone"]),
            client("3", id_number=client("1")["id_number"]),
            client("4", legacy_account_number=client("1")["legacy_account_number"]),
        ),
        investments(*(investment(f"I{n}", str(n)) for n in range(1, 5))),
        as_of=AS_OF,
    )
    assert codes(report.errors) == [
        ("clients", 2, "id_number", "duplicate_id"),
        ("clients", 2, "legacy_account_number", "duplicate_account_number"),
        ("clients", 2, "phone", "duplicate_phone"),
        ("clients", 3, "phone", "duplicate_phone"),
        ("clients", 4, "id_number", "duplicate_id"),
        ("clients", 5, "legacy_account_number", "duplicate_account_number"),
    ]


def test_the_same_id_number_on_different_document_types_is_two_documents() -> None:
    report = run_trial(
        clients(client("1"), client("2", id_type="passport", id_number=client("1")["id_number"])),
        investments(investment("A", "1"), investment("B", "2")),
        as_of=AS_OF,
    )
    assert report.problems == ()


def test_a_shared_email_or_a_shared_name_and_birthday_is_only_a_warning() -> None:
    report = run_trial(
        clients(
            client("1"),
            client("2", email=client("1")["email"]),
            client("3", given_names="PERSON 1"),
        ),
        investments(*(investment(f"I{n}", str(n)) for n in range(1, 4))),
        as_of=AS_OF,
    )
    assert report.clean
    assert codes(report.warnings) == [
        ("clients", 2, "email", "duplicate_email"),
        ("clients", 2, "surname", "possible_duplicate_person"),
        ("clients", 3, "email", "duplicate_email"),
        ("clients", 4, "surname", "possible_duplicate_person"),
    ]


def test_a_client_with_nothing_invested_is_a_warning() -> None:
    report = run_trial(clients(client("1")), investments(), as_of=AS_OF)
    assert codes(report.warnings) == [("clients", 2, None, "client_without_investments")]


def test_clients_without_a_phone_are_counted_for_follow_up() -> None:
    report = run_trial(
        clients(client("1", phone="")), investments(investment("A", "1")), as_of=AS_OF
    )
    assert report.clean
    assert report.summary.clients_without_phone == 1


def test_a_refused_sheet_is_reported_once_not_once_per_investment() -> None:
    report = run_trial("wrong,columns\r\n", investments(investment("A", "1")), as_of=AS_OF)
    assert not report.clean
    assert {p.code for p in report.errors if p.sheet is Sheet.CLIENTS} == {
        "column_missing",
        "column_unknown",
    }
    assert len([p for p in report.errors if p.sheet is Sheet.CLIENTS]) == len(CLIENT_COLUMNS) + 2
    assert [p for p in report.problems if p.sheet is Sheet.INVESTMENTS] == []
    assert report.summary.running_investments == 0
