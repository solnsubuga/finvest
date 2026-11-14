"""The daily journal in each accounting system's import format, and the code maps behind it."""

import csv
import io
from datetime import date

import pytest

from app.modules.finance.api import journal_filename
from app.modules.finance.domain.code_maps import InvalidCodeMap, changes, clean
from app.modules.finance.domain.formats import (
    QUICKBOOKS_HEADER,
    SAGE_HEADER,
    XERO_HEADER,
    ZOHO_HEADER,
    CodeMap,
    CodesNotMapped,
    Format,
    System,
    check_can_render,
    render,
    system_of,
)
from app.modules.finance.domain.journal import ExportRefused, JournalLine, journal_csv
from app.shared.money import Currency, Money

UGX = Currency.UGX
USD = Currency.USD
DAY = date(2026, 11, 12)
NEXT = date(2026, 11, 13)


def jline(day: date, code: str, name: str, debits: int, credits: int) -> JournalLine:
    return JournalLine(day, code, name, "asset", Money(debits, UGX), Money(credits, UGX))


LINES = [
    jline(DAY, "bank.trust", "Trust bank account", 250_000, 40_000),
    jline(DAY, "client.awaiting_start", "Paid in, awaiting start", 0, 250_000),
    jline(DAY, "payouts.in_flight", "Payouts on the way", 40_000, 0),
    # Debited and credited the same: nothing to post.
    jline(DAY, "investments.active", "Invested", 7_000, 7_000),
    jline(NEXT, "bank.trust", "Trust bank account", 0, 1_500),
    jline(NEXT, "dealing.bill_income", "Bill income", 1_500, 0),
]
CODES = {line.code for line in LINES}


def code_map(system: System, accounts: dict[str, str], tax_rate: str | None = None) -> CodeMap:
    return CodeMap(system, accounts, tax_rate)


QUICKBOOKS = code_map(
    System.QUICKBOOKS,
    {
        "bank.trust": "Trust account",
        "client.awaiting_start": "Client funds:Awaiting start",
        "payouts.in_flight": "Client funds:Payouts",
        "investments.active": "Client funds:Invested",
        "dealing.bill_income": "=Bill income",
    },
)
XERO = code_map(
    System.XERO,
    {
        "bank.trust": "090",
        "client.awaiting_start": "820",
        "payouts.in_flight": "821",
        "investments.active": "822",
        "dealing.bill_income": "200",
    },
    "Tax Exempt",
)
SAGE = code_map(
    System.SAGE,
    {
        "bank.trust": "1210",
        "client.awaiting_start": "2110",
        "payouts.in_flight": "2111",
        "investments.active": "2112",
        "dealing.bill_income": "4000",
    },
)


def rows(content: str) -> list[str]:
    split = content.split("\r\n")
    assert split[-1] == ""
    return split[:-1]


def test_quickbooks_has_one_entry_per_day_with_debits_and_credits_net_by_account() -> None:
    found = rows(render(Format.QUICKBOOKS, UGX, LINES, QUICKBOOKS))
    assert found[0] == ",".join(QUICKBOOKS_HEADER)
    assert found[1:] == [
        "FINVEST-UGX-20261112,12/11/2026,Trust account,210000,,Trust bank account (bank.trust)",
        "FINVEST-UGX-20261112,12/11/2026,Client funds:Awaiting start,,250000,"
        '"Paid in, awaiting start (client.awaiting_start)"',
        "FINVEST-UGX-20261112,12/11/2026,Client funds:Payouts,40000,,"
        "Payouts on the way (payouts.in_flight)",
        "FINVEST-UGX-20261113,13/11/2026,Trust account,,1500,Trust bank account (bank.trust)",
        # An account that would run as a formula in a spreadsheet is quoted.
        "FINVEST-UGX-20261113,13/11/2026,'=Bill income,1500,,Bill income (dealing.bill_income)",
    ]


def test_xero_writes_a_debit_positive_and_a_credit_negative_with_the_maps_tax_rate() -> None:
    found = rows(render(Format.XERO, UGX, LINES, XERO))
    assert found[0] == ",".join(XERO_HEADER)
    assert found[1:] == [
        "FINVEST-UGX-20261112,12/11/2026,Trust bank account (bank.trust),090,Tax Exempt,210000"
        + ",,,,",
        'FINVEST-UGX-20261112,12/11/2026,"Paid in, awaiting start (client.awaiting_start)",'
        "820,Tax Exempt,-250000,,,,",
        "FINVEST-UGX-20261112,12/11/2026,Payouts on the way (payouts.in_flight),821,Tax Exempt,"
        "40000,,,,",
        "FINVEST-UGX-20261113,13/11/2026,Trust bank account (bank.trust),090,Tax Exempt,-1500"
        + ",,,,",
        "FINVEST-UGX-20261113,13/11/2026,Bill income (dealing.bill_income),200,Tax Exempt,1500"
        + ",,,,",
    ]


def test_sage_fills_both_sides_and_keeps_journals_off_the_vat_return() -> None:
    found = rows(render(Format.SAGE, UGX, LINES, SAGE))
    assert found[0] == ",".join(SAGE_HEADER)
    assert found[1] == (
        "FINVEST-UGX-20261112,12/11/2026,Finvest daily journal 2026-11-12,1210,"
        "Trust bank account (bank.trust),No,210000,0"
    )
    assert found[2].endswith(',2110,"Paid in, awaiting start (client.awaiting_start)",No,0,250000')
    assert len(found) == 6


ZOHO = code_map(System.ZOHO, dict(QUICKBOOKS.accounts))


def test_zoho_books_has_one_draft_journal_per_day_by_journal_number() -> None:
    found = rows(render(Format.ZOHO, UGX, LINES, ZOHO))
    assert found[0] == ",".join(ZOHO_HEADER)
    assert found[1] == (
        "12/11/2026,FINVEST-UGX-20261112,FINVEST-UGX-20261112,Finvest daily journal 2026-11-12,"
        "Draft,UGX,Trust account,Trust bank account (bank.trust),210000,"
    )
    assert found[2].endswith(
        ',Client funds:Awaiting start,"Paid in, awaiting start (client.awaiting_start)",,250000'
    )
    assert len(found) == 6


@pytest.mark.parametrize("format_", [Format.QUICKBOOKS, Format.XERO, Format.SAGE, Format.ZOHO])
def test_every_days_lines_still_balance_in_every_format(format_: Format) -> None:
    maps = {Format.QUICKBOOKS: QUICKBOOKS, Format.XERO: XERO, Format.SAGE: SAGE, Format.ZOHO: ZOHO}
    found = list(csv.DictReader(io.StringIO(render(format_, UGX, LINES, maps[format_]))))
    totals: dict[str, int] = {}
    for cells in found:
        if format_ is Format.XERO:
            reference, amount = cells["*Narration"], int(cells["*Amount"])
        elif format_ is Format.ZOHO:
            reference = cells["Journal Number"]
            amount = int(cells["Debit"] or 0) - int(cells["Credit"] or 0)
        elif format_ is Format.QUICKBOOKS:
            reference = cells["Journal No."]
            amount = int(cells["Debits"] or 0) - int(cells["Credits"] or 0)
        else:
            reference, amount = cells["Reference"], int(cells["Debit"]) - int(cells["Credit"])
        totals[reference] = totals.get(reference, 0) + amount
    assert totals == {"FINVEST-UGX-20261112": 0, "FINVEST-UGX-20261113": 0}


def test_dollar_cents_are_kept_where_a_format_could_hold_them() -> None:
    lines = [
        JournalLine(DAY, "bank.trust", "Trust", "asset", Money(12_345, USD), Money(0, USD)),
        JournalLine(DAY, "client.x", "Client", "liability", Money(0, USD), Money(12_345, USD)),
    ]
    # The systems take shillings only; the generic CSV keeps the cents.
    assert "123.45" in render(Format.CSV, USD, lines, None)
    for format_, system in ((Format.QUICKBOOKS, System.QUICKBOOKS), (Format.XERO, System.XERO)):
        with pytest.raises(ExportRefused) as refused:
            render(format_, USD, lines, code_map(system, {"bank.trust": "1", "client.x": "2"}))
        assert refused.value.code == "currency_not_supported"


def test_a_code_the_map_does_not_name_refuses_the_export_and_is_named() -> None:
    partial = code_map(System.XERO, {"bank.trust": "090"}, "Tax Exempt")
    with pytest.raises(CodesNotMapped) as refused:
        check_can_render(Format.XERO, UGX, LINES, partial)
    assert refused.value.code == "codes_not_mapped"
    assert refused.value.codes == sorted(CODES - {"bank.trust"})
    # No map at all, or another system's map, names nothing.
    with pytest.raises(CodesNotMapped):
        render(Format.XERO, UGX, LINES, None)
    with pytest.raises(CodesNotMapped):
        render(Format.XERO, UGX, LINES, SAGE)
    # Even a code whose day nets to nothing must be mapped: the map is checked, not guessed.
    without_invested = {k: v for k, v in SAGE.accounts.items() if k != "investments.active"}
    with pytest.raises(CodesNotMapped) as refused:
        render(Format.SAGE, UGX, LINES, code_map(System.SAGE, without_invested))
    assert refused.value.codes == ["investments.active"]


def test_the_generic_csv_needs_no_map_and_is_unchanged() -> None:
    assert render(Format.CSV, UGX, LINES, None) == journal_csv(UGX, LINES)
    assert system_of(Format.CSV) is None
    assert system_of(Format.SAGE) is System.SAGE
    check_can_render(Format.CSV, USD, LINES, None)


def test_an_empty_journal_is_only_a_header() -> None:
    for format_, header, cmap in (
        (Format.QUICKBOOKS, QUICKBOOKS_HEADER, QUICKBOOKS),
        (Format.XERO, XERO_HEADER, XERO),
        (Format.SAGE, SAGE_HEADER, SAGE),
        (Format.ZOHO, ZOHO_HEADER, ZOHO),
    ):
        assert rows(render(format_, UGX, [], cmap)) == [",".join(header)]


def test_file_names_say_the_system() -> None:
    assert journal_filename(UGX, Format.CSV, DAY, DAY) == "finvest-journal-ugx-2026-11-12.csv"
    assert (
        journal_filename(UGX, Format.XERO, DAY, NEXT)
        == "finvest-journal-xero-ugx-2026-11-12-to-2026-11-13.csv"
    )


# The code maps ---------------------------------------------------------------------------

KNOWN = {"bank.trust", "client.awaiting_start", "fees.provider.iotec"}


def problems(
    system: System, accounts: dict[str, str], tax_rate: str | None = None
) -> list[tuple[list[str | int], str]]:
    with pytest.raises(InvalidCodeMap) as refused:
        clean(system, accounts, tax_rate, KNOWN)
    return refused.value.problems


def test_a_map_is_trimmed_and_a_blank_account_leaves_a_code_unmapped() -> None:
    cleaned, rate = clean(
        System.QUICKBOOKS,
        {"bank.trust": "  Trust account ", "client.awaiting_start": "   "},
        None,
        KNOWN,
    )
    assert cleaned == {"bank.trust": "Trust account"}
    assert rate is None
    assert clean(System.XERO, {"bank.trust": "090"}, " Tax Exempt ", KNOWN) == (
        {"bank.trust": "090"},
        "Tax Exempt",
    )


def test_each_system_accepts_only_its_own_kind_of_account() -> None:
    assert problems(System.SAGE, {"bank.trust": "1210A"}) == [
        (["accounts", "bank.trust"], "bad_sage_account")
    ]
    assert problems(System.XERO, {"bank.trust": "has space"}, "Tax Exempt") == [
        (["accounts", "bank.trust"], "bad_xero_account")
    ]
    assert problems(System.XERO, {"bank.trust": "12345678901"}, "Tax Exempt") == [
        (["accounts", "bank.trust"], "bad_xero_account")
    ]
    assert problems(System.QUICKBOOKS, {"bank.trust": "Trust\naccount"}) == [
        (["accounts", "bank.trust"], "bad_quickbooks_account")
    ]
    clean(System.QUICKBOOKS, {"bank.trust": "Client funds:Trust (Stanbic)"}, None, KNOWN)


def test_a_code_the_ledger_does_not_have_is_refused() -> None:
    assert problems(System.SAGE, {"bank.trusts": "1210", "Bank.Trust": "1211"}) == [
        (["accounts", "bank.trusts"], "unknown_code"),
        (["accounts", "Bank.Trust"], "unknown_code"),
    ]


def test_xero_needs_a_tax_rate_and_the_others_take_none() -> None:
    assert problems(System.XERO, {"bank.trust": "090"}, None) == [(["tax_rate"], "required")]
    assert problems(System.XERO, {"bank.trust": "090"}, "  ") == [(["tax_rate"], "required")]
    assert problems(System.SAGE, {"bank.trust": "1210"}, "No VAT") == [
        (["tax_rate"], "not_for_this_system")
    ]


def test_the_audit_trail_counts_what_a_proposal_changes() -> None:
    before = {"a.one": "1", "a.two": "2", "a.three": "3"}
    after = {"a.one": "1", "a.two": "20", "a.four": "4"}
    assert changes(before, after) == {"added": 1, "changed": 1, "removed": 1}
    assert changes({}, after) == {"added": 3, "changed": 0, "removed": 0}
