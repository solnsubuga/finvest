"""What an account code map may hold. Pure.

A code map says, for one accounting system, which of the system's accounts each Finvest ledger
code is posted to. It is changed by two people: one proposes a whole new map and a different one
approves it, and the approved map is what every later export of that system uses. A map need
not name every code. An export that meets a code the map does not name is refused, so a journal
never lands on a guessed account.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.modules.finance.domain.formats import System

MAX_CODES = 1000
MAX_REASON = 500
# Xero's name for the rate on a line with no tax, in an organisation outside the UK, Australia
# and New Zealand. The accountant can change it with the map.
DEFAULT_XERO_TAX_RATE = "Tax Exempt"

# QuickBooks names an account by its name, a sub-account as "Parent:Child". Xero uses its own
# short account codes. Sage takes the nominal code, a number only.
_ACCOUNT_RULES: dict[System, re.Pattern[str]] = {
    System.QUICKBOOKS: re.compile(r"^[^\x00-\x1f\x7f]{1,100}$"),
    System.XERO: re.compile(r"^[A-Za-z0-9._-]{1,10}$"),
    System.SAGE: re.compile(r"^[0-9]{1,8}$"),
    # Zoho Books names an account by its name, as QuickBooks does.
    System.ZOHO: re.compile(r"^[^\x00-\x1f\x7f]{1,100}$"),
}
_LEDGER_CODE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
_TAX_RATE = re.compile(r"^[^\x00-\x1f\x7f]{1,50}$")


class InvalidCodeMap(Exception):
    """The fields that were wrong, as (field path, code) pairs, without the values."""

    def __init__(self, problems: list[tuple[list[str | int], str]]) -> None:
        super().__init__("invalid code map")
        self.problems = problems


def clean(
    system: System,
    accounts: Mapping[str, str],
    tax_rate: str | None,
    known_codes: set[str],
) -> tuple[dict[str, str], str | None]:
    """The map as it will be saved, with blank accounts left out and spaces trimmed.

    Refuses a code the ledger does not have, an account the system would not accept, too many
    codes, and a tax rate on any system but Xero, which needs one.
    """
    problems: list[tuple[list[str | int], str]] = []
    cleaned: dict[str, str] = {}
    if len(accounts) > MAX_CODES:
        problems.append((["accounts"], "too_many"))
    for code, account in accounts.items():
        value = account.strip()
        if not value:
            continue
        if not _LEDGER_CODE.match(code) or code not in known_codes:
            problems.append((["accounts", code], "unknown_code"))
        elif not _ACCOUNT_RULES[system].match(value):
            problems.append((["accounts", code], f"bad_{system.value}_account"))
        else:
            cleaned[code] = value
    rate: str | None = None
    if system is System.XERO:
        rate = (tax_rate or "").strip()
        if not _TAX_RATE.match(rate):
            problems.append((["tax_rate"], "required"))
    elif tax_rate is not None and tax_rate.strip():
        problems.append((["tax_rate"], "not_for_this_system"))
    if problems:
        raise InvalidCodeMap(problems)
    return cleaned, rate


def changes(before: Mapping[str, str], after: Mapping[str, str]) -> dict[str, int]:
    """How many codes a proposal adds, changes and removes, for the audit trail and the
    approver. Never the accounts themselves, which are in the map."""
    added = sum(1 for code in after if code not in before)
    removed = sum(1 for code in before if code not in after)
    changed = sum(1 for code in after if code in before and before[code] != after[code])
    return {"added": added, "changed": changed, "removed": removed}
