"""The daily journal in each accounting system's own import format. Pure.

Finvest's generic CSV (see `journal.py`) names Finvest's own account codes and gives each code's
debits and credits for the day. The other formats are the journal import files of QuickBooks
Online, Xero, Sage Accounting and Zoho Books, and they name the accounting system's accounts
instead, through the account code map for that system (see `code_maps.py`). Each one:

* holds one journal per day, with the reference `FINVEST-<currency>-<YYYYMMDD>`, so the system
  can refuse a day it was already given,
* writes each Finvest code's movement for the day as one line, net: on the debit side when the
  code was debited more than credited, on the credit side otherwise, and leaves out a code whose
  debits and credits that day came to the same, since the systems refuse a line of nothing,
* writes dates day first, `14/11/2026`, as the systems do for an organisation in Uganda,
* writes amounts in the major unit with exactly the currency's decimals, from integers.

The systems keep their books in one base currency and take a journal in another only with an
exchange rate Finvest does not have, so these formats are for shilling journals. A dollar
journal is downloaded as the generic CSV.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.modules.finance.domain.journal import (
    ExportRefused,
    JournalLine,
    journal_csv,
    journal_reference,
    safe_cell,
)
from app.shared.money import Currency, Money


class Format(StrEnum):
    CSV = "csv"
    QUICKBOOKS = "quickbooks"
    XERO = "xero"
    SAGE = "sage"
    ZOHO = "zoho"


class System(StrEnum):
    """An accounting system with its own account codes, and so its own code map."""

    QUICKBOOKS = "quickbooks"
    XERO = "xero"
    SAGE = "sage"
    ZOHO = "zoho"


SYSTEM_NAMES: dict[System, str] = {
    System.QUICKBOOKS: "QuickBooks Online",
    System.XERO: "Xero",
    System.SAGE: "Sage Accounting",
    System.ZOHO: "Zoho Books",
}
# The one currency the accounting formats are written in.
BOOKS_CURRENCY = Currency.UGX


def system_of(format_: Format) -> System | None:
    """The system whose code map the format needs, or None for Finvest's generic CSV."""
    return None if format_ is Format.CSV else System(format_.value)


@dataclass(frozen=True, slots=True)
class CodeMap:
    """How Finvest's account codes are named in one accounting system."""

    system: System
    # Finvest code to the system's account: a QuickBooks account name, a Xero account code or
    # a Sage nominal code.
    accounts: Mapping[str, str]
    # Xero asks for a tax rate on every journal line. None for the other systems.
    tax_rate: str | None = None


class CodesNotMapped(ExportRefused):
    """Codes in the journal that the system's code map does not name yet."""

    def __init__(self, codes: list[str]) -> None:
        super().__init__("codes_not_mapped")
        self.codes = codes


def check_can_render(
    format_: Format, currency: Currency, lines: list[JournalLine], code_map: CodeMap | None
) -> None:
    """Refuse a journal the format cannot hold: a currency other than shillings, or a code the
    system's map does not name. The generic CSV holds anything."""
    system = system_of(format_)
    if system is None:
        return
    if currency is not BOOKS_CURRENCY:
        raise ExportRefused("currency_not_supported")
    named = code_map.accounts if code_map is not None and code_map.system is system else {}
    missing = sorted({line.code for line in lines if line.code not in named})
    if missing:
        raise CodesNotMapped(missing)


def render(
    format_: Format, currency: Currency, lines: list[JournalLine], code_map: CodeMap | None
) -> str:
    """The journal file. Call `check_can_render` first; this raises the same refusals."""
    check_can_render(format_, currency, lines, code_map)
    if format_ is Format.CSV:
        return journal_csv(currency, lines)
    if code_map is None:  # check_can_render refused this already
        raise CodesNotMapped(sorted({line.code for line in lines}))
    writer = _WRITERS[format_]
    return writer(currency, [line for line in lines if _net(line) != 0], code_map)


# The systems' files ----------------------------------------------------------------------


def _net(line: JournalLine) -> int:
    """Debits less credits: above zero is a debit, below zero a credit."""
    return line.debits.minor - line.credits.minor


def _day(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def _description(line: JournalLine) -> str:
    return safe_cell(f"{line.name} ({line.code})")


def _sides(currency: Currency, line: JournalLine) -> tuple[Money, Money]:
    net = _net(line)
    return Money(max(net, 0), currency), Money(max(-net, 0), currency)


QUICKBOOKS_HEADER = (
    "Journal No.",
    "Journal Date",
    "Account Name",
    "Debits",
    "Credits",
    "Description",
)


def _quickbooks(currency: Currency, lines: list[JournalLine], code_map: CodeMap) -> str:
    """QuickBooks Online's journal entry import. Lines with the same Journal No. are one entry.
    An empty side is left blank, as QuickBooks' own sample file does."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(QUICKBOOKS_HEADER)
    for line in lines:
        debit, credit = _sides(currency, line)
        writer.writerow(
            (
                journal_reference(currency, line.effective_date),
                _day(line.effective_date),
                safe_cell(code_map.accounts[line.code]),
                debit.to_major() if debit.minor else "",
                credit.to_major() if credit.minor else "",
                _description(line),
            )
        )
    return out.getvalue()


XERO_HEADER = (
    "*Narration",
    "*Date",
    "Description",
    "*AccountCode",
    "*TaxRate",
    "*Amount",
    "TrackingName1",
    "TrackingOption1",
    "TrackingName2",
    "TrackingOption2",
)


def _xero(currency: Currency, lines: list[JournalLine], code_map: CodeMap) -> str:
    """Xero's manual journal import template. One amount per line: a debit is positive and a
    credit negative. Lines with the same narration and date are one journal."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(XERO_HEADER)
    tax_rate = safe_cell(code_map.tax_rate or "")
    for line in lines:
        writer.writerow(
            (
                journal_reference(currency, line.effective_date),
                _day(line.effective_date),
                _description(line),
                safe_cell(code_map.accounts[line.code]),
                tax_rate,
                Money(_net(line), currency).to_major(),
                "",
                "",
                "",
                "",
            )
        )
    return out.getvalue()


SAGE_HEADER = (
    "Reference",
    "Date",
    "Description",
    "Nominal Code",
    "Details",
    "Include on VAT/Tax Return?",
    "Debit",
    "Credit",
)


def _sage(currency: Currency, lines: list[JournalLine], code_map: CodeMap) -> str:
    """Sage Accounting's journal import. A new journal starts where the Reference changes, and
    Debit and Credit are both required, so an empty side is 0. A movement between Finvest's
    accounts is never on a VAT return."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(SAGE_HEADER)
    for line in lines:
        debit, credit = _sides(currency, line)
        writer.writerow(
            (
                journal_reference(currency, line.effective_date),
                _day(line.effective_date),
                safe_cell(f"Finvest daily journal {line.effective_date.isoformat()}"),
                safe_cell(code_map.accounts[line.code]),
                _description(line),
                "No",
                debit.to_major(),
                credit.to_major(),
            )
        )
    return out.getvalue()


ZOHO_HEADER = (
    "Journal Date",
    "Journal Number",
    "Reference Number",
    "Notes",
    "Status",
    "Currency",
    "Account",
    "Description",
    "Debit",
    "Credit",
)


def _zoho(currency: Currency, lines: list[JournalLine], code_map: CodeMap) -> str:
    """Zoho Books' manual journal import, whose columns are matched on its Map Fields step.
    Lines with the same Journal Number are one journal. Each journal comes in as a draft, so the
    accountant publishes it in Zoho Books after looking it over."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(ZOHO_HEADER)
    for line in lines:
        debit, credit = _sides(currency, line)
        reference = journal_reference(currency, line.effective_date)
        writer.writerow(
            (
                _day(line.effective_date),
                reference,
                reference,
                safe_cell(f"Finvest daily journal {line.effective_date.isoformat()}"),
                "Draft",
                currency.value,
                safe_cell(code_map.accounts[line.code]),
                _description(line),
                debit.to_major() if debit.minor else "",
                credit.to_major() if credit.minor else "",
            )
        )
    return out.getvalue()


_WRITERS: dict[Format, Callable[[Currency, list[JournalLine], CodeMap], str]] = {
    Format.QUICKBOOKS: _quickbooks,
    Format.XERO: _xero,
    Format.SAGE: _sage,
    Format.ZOHO: _zoho,
}
