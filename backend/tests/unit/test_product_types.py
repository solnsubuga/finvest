"""The pure rules of product types: what a new type, an edit and a decision must be."""

import pytest

from app.modules.product_types.domain.types import (
    BUILT_IN,
    MODEL_DESCRIPTIONS,
    ChangeStatus,
    NewType,
    Refusal,
    ReturnModel,
    TypeAction,
    TypeStatus,
    TypeTerms,
    check_decision,
    check_edit,
    check_new,
    check_status,
    check_withdrawal,
    status_after,
)

TERMS = TypeTerms("Corporate bond", "Issued by a listed company.", 1500)


def new(code: str = "CORP-BOND", terms: TypeTerms = TERMS) -> NewType:
    return NewType(code, ReturnModel.TREASURY_BOND, terms)


def test_every_model_has_a_built_in_type_and_a_description() -> None:
    assert set(BUILT_IN) == set(ReturnModel) == set(MODEL_DESCRIPTIONS)


def test_a_new_type_is_accepted() -> None:
    assert check_new(new(), code_taken=False, name_taken=False) is None


@pytest.mark.parametrize(
    ("code", "terms", "refusal"),
    [
        ("corp-bond", TERMS, Refusal.INVALID_CODE),
        ("CORP--BOND", TERMS, Refusal.INVALID_CODE),
        ("C" * 25, TERMS, Refusal.INVALID_CODE),
        ("CORP", TypeTerms("", "x", 0), Refusal.INVALID_NAME),
        ("CORP", TypeTerms(" Padded", "x", 0), Refusal.INVALID_NAME),
        ("CORP", TypeTerms("N" * 61, "x", 0), Refusal.INVALID_NAME),
        ("CORP", TypeTerms("Name", "  ", 0), Refusal.INVALID_DESCRIPTION),
        ("CORP", TypeTerms("Name", "d" * 301, 0), Refusal.INVALID_DESCRIPTION),
        ("CORP", TypeTerms("Name", "x", -1), Refusal.INVALID_TAX),
        ("CORP", TypeTerms("Name", "x", 10_000), Refusal.INVALID_TAX),
    ],
)
def test_a_new_type_names_what_is_wrong(code: str, terms: TypeTerms, refusal: Refusal) -> None:
    assert check_new(new(code, terms), code_taken=False, name_taken=False) is refusal


def test_a_code_or_name_already_used_is_refused() -> None:
    assert check_new(new(), code_taken=True, name_taken=False) is Refusal.CODE_TAKEN
    assert check_new(new(), code_taken=False, name_taken=True) is Refusal.NAME_TAKEN


def test_an_edit_must_change_something() -> None:
    assert check_edit(TERMS, TERMS, name_taken=False) is Refusal.NOTHING_CHANGES
    renamed = TypeTerms("Company bond", TERMS.description, TERMS.default_tax_bps)
    assert check_edit(TERMS, renamed, name_taken=False) is None
    assert check_edit(TERMS, renamed, name_taken=True) is Refusal.NAME_TAKEN


def test_retiring_and_restoring_move_the_status_once() -> None:
    assert check_status(TypeStatus.ACTIVE, TypeAction.RETIRE) is None
    assert check_status(TypeStatus.RETIRED, TypeAction.RETIRE) is Refusal.ALREADY_RETIRED
    assert check_status(TypeStatus.ACTIVE, TypeAction.RESTORE) is Refusal.NOT_RETIRED
    assert status_after(TypeAction.RETIRE, TypeStatus.ACTIVE) is TypeStatus.RETIRED
    assert status_after(TypeAction.RESTORE, TypeStatus.RETIRED) is TypeStatus.ACTIVE
    assert status_after(TypeAction.EDIT, TypeStatus.RETIRED) is TypeStatus.RETIRED


def test_a_second_person_decides_and_only_the_proposer_withdraws() -> None:
    waiting = ChangeStatus.PROPOSED
    assert check_decision(status=waiting, proposed_by="a", decided_by="b") is None
    assert check_decision(status=waiting, proposed_by="a", decided_by="a") is Refusal.OWN_CHANGE
    assert (
        check_decision(status=ChangeStatus.APPROVED, proposed_by="a", decided_by="b")
        is Refusal.NOT_WAITING
    )
    assert check_withdrawal(status=waiting, proposed_by="a", by="a") is None
    assert check_withdrawal(status=waiting, proposed_by="a", by="b") is Refusal.NOT_YOURS
