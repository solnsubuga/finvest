"""Product types: what a product is called and how it earns. Pure: no I/O.

The owner decided on 2026-11-15 that staff may invent product types, such as Corporate bond or
Fixed deposit, each built on a return model the system already pays correctly. The model is
the code that pays a product: it never changes, and a new model still needs code. A type adds
a name clients see, a description staff see, and the tax new products of the type start with.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class ReturnModel(StrEnum):
    # A term in days and a rate before tax, paid at maturity, bought at the auction at a discount.
    TREASURY_BILL = "treasury_bill"
    # A term (or an open term) and a rate before tax, paid at maturity or monthly.
    FIXED_INCOME = "fixed_income"
    # An issue with its own ISIN, coupon and dates, bought at a price, paying six-monthly coupons.
    TREASURY_BOND = "treasury_bond"


# What each model does, for staff choosing one.
MODEL_DESCRIPTIONS: dict[ReturnModel, str] = {
    ReturnModel.TREASURY_BILL: (
        "Earns a rate before tax over a term in days, paid at maturity. Bought at the auction "
        "at a discount."
    ),
    ReturnModel.FIXED_INCOME: (
        "Earns a rate before tax over a term, or an open term, paid at maturity or monthly."
    ),
    ReturnModel.TREASURY_BOND: (
        "An issue with its own ISIN, coupon and dates, bought at a price. Pays a coupon every "
        "six months and the face value at maturity."
    ),
}

# The types the system started with, one per model. Migration 0493 seeds them.
BUILT_IN: dict[ReturnModel, str] = {
    ReturnModel.TREASURY_BILL: "TREASURY-BILL",
    ReturnModel.FIXED_INCOME: "FIXED-INCOME",
    ReturnModel.TREASURY_BOND: "TREASURY-BOND",
}


class TypeStatus(StrEnum):
    ACTIVE = "active"
    # No new products or issues of it. Those that exist carry on.
    RETIRED = "retired"


class TypeAction(StrEnum):
    CREATE = "create"
    EDIT = "edit"
    RETIRE = "retire"
    RESTORE = "restore"


class ChangeStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    SENT_BACK = "sent_back"
    WITHDRAWN = "withdrawn"


class Refusal(StrEnum):
    INVALID_CODE = "invalid_code"
    INVALID_NAME = "invalid_name"
    INVALID_DESCRIPTION = "invalid_description"
    INVALID_TAX = "invalid_tax"
    CODE_TAKEN = "code_taken"
    NAME_TAKEN = "name_taken"
    REASON_REQUIRED = "reason_required"
    NOTE_REQUIRED = "note_required"
    NOTHING_CHANGES = "nothing_changes"
    ALREADY_RETIRED = "already_retired"
    NOT_RETIRED = "not_retired"
    ALREADY_WAITING = "already_waiting"
    NOT_WAITING = "not_waiting"
    OWN_CHANGE = "own_change"
    NOT_YOURS = "not_yours"
    STALE = "stale"


CODE = re.compile(r"^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$")
MAX_CODE = 24
MAX_NAME = 60
MAX_DESCRIPTION = 300
MAX_TAX_BPS = 9999


@dataclass(frozen=True, slots=True)
class TypeTerms:
    """What a change sets: the name clients see, what staff read, and the usual tax."""

    name: str
    description: str
    default_tax_bps: int


@dataclass(frozen=True, slots=True)
class NewType:
    code: str
    model: ReturnModel
    terms: TypeTerms


def valid_text(value: str) -> bool:
    return bool(value.strip())


def check_terms(terms: TypeTerms) -> Refusal | None:
    if not terms.name or terms.name != terms.name.strip() or len(terms.name) > MAX_NAME:
        return Refusal.INVALID_NAME
    if not terms.description.strip() or len(terms.description) > MAX_DESCRIPTION:
        return Refusal.INVALID_DESCRIPTION
    if type(terms.default_tax_bps) is not int or not 0 <= terms.default_tax_bps <= MAX_TAX_BPS:
        return Refusal.INVALID_TAX
    return None


def check_new(new: NewType, *, code_taken: bool, name_taken: bool) -> Refusal | None:
    if len(new.code) > MAX_CODE or not CODE.match(new.code):
        return Refusal.INVALID_CODE
    refusal = check_terms(new.terms)
    if refusal is not None:
        return refusal
    if code_taken:
        return Refusal.CODE_TAKEN
    if name_taken:
        return Refusal.NAME_TAKEN
    return None


def check_edit(current: TypeTerms, terms: TypeTerms, *, name_taken: bool) -> Refusal | None:
    refusal = check_terms(terms)
    if refusal is not None:
        return refusal
    if terms == current:
        return Refusal.NOTHING_CHANGES
    if name_taken:
        return Refusal.NAME_TAKEN
    return None


def check_status(status: TypeStatus, action: TypeAction) -> Refusal | None:
    if action is TypeAction.RETIRE and status is TypeStatus.RETIRED:
        return Refusal.ALREADY_RETIRED
    if action is TypeAction.RESTORE and status is TypeStatus.ACTIVE:
        return Refusal.NOT_RETIRED
    return None


def status_after(action: TypeAction, status: TypeStatus) -> TypeStatus:
    if action is TypeAction.RETIRE:
        return TypeStatus.RETIRED
    if action is TypeAction.RESTORE:
        return TypeStatus.ACTIVE
    return status


def check_decision(*, status: ChangeStatus, proposed_by: str, decided_by: str) -> Refusal | None:
    if status is not ChangeStatus.PROPOSED:
        return Refusal.NOT_WAITING
    if proposed_by == decided_by:
        return Refusal.OWN_CHANGE
    return None


def check_withdrawal(*, status: ChangeStatus, proposed_by: str, by: str) -> Refusal | None:
    if status is not ChangeStatus.PROPOSED:
        return Refusal.NOT_WAITING
    if proposed_by != by:
        return Refusal.NOT_YOURS
    return None
