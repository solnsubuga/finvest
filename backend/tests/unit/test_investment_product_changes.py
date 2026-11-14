"""Who may change a product, and how: the pure rules in domain/product_changes.py."""

from dataclasses import replace
from datetime import date

import pytest

from app.modules.investments.domain.product_changes import (
    CURRENCIES_THAT_CAN_OPEN,
    Current,
    ProductAction,
    ProductChange,
    Refusal,
    check_approval,
    check_edit,
    check_open,
    check_pause,
    check_proposal,
    check_send_back,
    check_withdrawal,
    with_status,
)
from app.modules.investments.domain.products import (
    SEED_PRODUCTS,
    InterestPaid,
    Product,
    ProductKind,
    ProductStatus,
)
from app.modules.investments.domain.rates import ProposalStatus, RateVersion
from app.shared.money import Currency, Money

BILL = SEED_PRODUCTS[0]
DOLLARS = SEED_PRODUCTS[2]
DRAFT = Product(
    code="TREA-364",
    name="364 Day Treasury Bill",
    kind=ProductKind.TREASURY_BILL,
    currency=Currency.UGX,
    term_days=364,
    minimum=Money(500_000, Currency.UGX),
    interest_paid=InterestPaid.AT_MATURITY,
    status=ProductStatus.DRAFT,
)
FIRST = RateVersion(date(2026, 11, 14), 1500, 1500)


def current(product: Product = BILL, *, version: int = 3, has_rate: bool = True) -> Current:
    return Current(product=product, version=version, has_rate=has_rate)


def change(
    action: ProductAction,
    target: Product,
    *,
    by: str = "maker",
    base: int | None = 3,
    status: ProposalStatus = ProposalStatus.PROPOSED,
) -> ProductChange:
    return ProductChange(
        action=action,
        target=target,
        reason="Auction calendar",
        proposed_by=by,
        base_version=None if action is ProductAction.CREATE else base,
        first_rate=FIRST if action is ProductAction.CREATE else None,
        status=status,
    )


# New products ------------------------------------------------------------------------------------


def test_a_new_product_is_a_draft_with_a_reason_and_a_code_nobody_has() -> None:
    new = change(ProductAction.CREATE, DRAFT)
    assert check_proposal(new, None, code_taken=False) is None
    assert check_proposal(new, None, code_taken=True) is Refusal.CODE_TAKEN
    live = change(ProductAction.CREATE, with_status(DRAFT, ProductStatus.LIVE))
    assert check_proposal(live, None, code_taken=False) is Refusal.NOT_A_DRAFT
    unexplained = replace(new, reason="  ")
    assert check_proposal(unexplained, None, code_taken=False) is Refusal.REASON_REQUIRED


def test_a_new_product_is_checked_again_when_approved() -> None:
    new = change(ProductAction.CREATE, DRAFT)
    assert check_approval(new, "checker", None, code_taken=False) is None
    assert check_approval(new, "maker", None, code_taken=False) is Refusal.SAME_PERSON
    assert check_approval(new, "checker", None, code_taken=True) is Refusal.CODE_TAKEN


# New terms -------------------------------------------------------------------------------------


def test_the_name_term_and_minimum_of_a_live_product_can_change() -> None:
    target = replace(BILL, name="91 Day T-Bill", term_days=92, minimum=Money(50_000, Currency.UGX))
    assert check_edit(current(), target, "Shorter name") is None


@pytest.mark.parametrize(
    ("target", "refusal"),
    [
        (replace(BILL, code="TREA-92"), Refusal.CODE_FIXED),
        (
            replace(BILL, currency=Currency.USD, minimum=Money(100, Currency.USD)),
            Refusal.CURRENCY_FIXED,
        ),
        (replace(BILL, status=ProductStatus.PAUSED), Refusal.STATUS_FIXED),
        (replace(BILL, kind=ProductKind.FIXED_INCOME), Refusal.KIND_FIXED),
        (replace(BILL, interest_paid=InterestPaid.MONTHLY), Refusal.INTEREST_PAID_FIXED),
        (BILL, Refusal.NOTHING_CHANGES),
    ],
)
def test_some_terms_of_a_product_clients_were_offered_never_change(
    target: Product, refusal: Refusal
) -> None:
    assert check_edit(current(), target, "Because") is refusal


def test_a_draft_can_still_change_its_kind_and_when_interest_is_paid() -> None:
    target = replace(DRAFT, kind=ProductKind.FIXED_INCOME, interest_paid=InterestPaid.MONTHLY)
    assert check_edit(current(DRAFT), target, "Bond, not bill") is None


def test_new_terms_need_a_reason() -> None:
    assert check_edit(current(), replace(BILL, name="Bill"), "") is Refusal.REASON_REQUIRED


# Pausing and opening ---------------------------------------------------------------------------


def test_only_a_live_product_pauses() -> None:
    assert check_pause(current(), "Auction cancelled") is None
    paused = with_status(BILL, ProductStatus.PAUSED)
    assert check_pause(current(paused), "Again") is Refusal.NOT_LIVE
    assert check_pause(current(DRAFT), "Never opened") is Refusal.NOT_LIVE


def test_a_paused_or_draft_product_opens_once_it_has_a_rate() -> None:
    paused = with_status(BILL, ProductStatus.PAUSED)
    assert check_open(current(paused), "Auctions resumed") is None
    assert check_open(current(DRAFT), "Launch") is None
    assert check_open(current(DRAFT, has_rate=False), "Launch") is Refusal.NO_RATE
    assert check_open(current(), "Already") is Refusal.ALREADY_LIVE


def test_the_dollar_product_cannot_open_until_dollars_can_be_paid_in_and_out() -> None:
    assert Currency.USD not in CURRENCIES_THAT_CAN_OPEN
    assert DOLLARS.status is ProductStatus.PAUSED
    assert check_open(current(DOLLARS), "Launch dollars") is Refusal.CURRENCY_NOT_OPEN


def test_a_pause_or_an_opening_changes_the_status_and_nothing_else() -> None:
    sneaky = change(ProductAction.PAUSE, replace(BILL, status=ProductStatus.PAUSED, name="X"))
    assert check_proposal(sneaky, current(), code_taken=False) is Refusal.PRODUCT_CHANGED
    pause = change(ProductAction.PAUSE, with_status(BILL, ProductStatus.PAUSED))
    assert check_proposal(pause, current(), code_taken=False) is None


# Deciding --------------------------------------------------------------------------------------


def test_nobody_approves_their_own_change() -> None:
    edit = change(ProductAction.EDIT, replace(BILL, name="Bill"))
    assert check_approval(edit, "maker", current(), code_taken=False) is Refusal.SAME_PERSON
    assert check_approval(edit, "checker", current(), code_taken=False) is None


def test_a_change_overtaken_by_another_is_refused() -> None:
    edit = change(ProductAction.EDIT, replace(BILL, name="Bill"), base=2)
    assert check_approval(edit, "checker", current(), code_taken=False) is Refusal.PRODUCT_CHANGED


def test_an_opening_is_refused_if_the_rate_is_not_there_when_approved() -> None:
    paused = with_status(BILL, ProductStatus.PAUSED)
    opening = change(ProductAction.OPEN, BILL)
    assert check_approval(opening, "checker", current(paused), code_taken=False) is None
    assert (
        check_approval(opening, "checker", current(paused, has_rate=False), code_taken=False)
        is Refusal.NO_RATE
    )


def test_a_decided_change_is_final() -> None:
    for status in (ProposalStatus.APPROVED, ProposalStatus.SENT_BACK, ProposalStatus.WITHDRAWN):
        done = change(ProductAction.EDIT, replace(BILL, name="Bill"), status=status)
        assert check_approval(done, "checker", current(), code_taken=False) is (
            Refusal.ALREADY_DECIDED
        )
        assert check_send_back(done, "checker", "No") is Refusal.ALREADY_DECIDED
        assert check_withdrawal(done, "maker") is Refusal.ALREADY_DECIDED


def test_sending_back_needs_a_second_person_and_a_note() -> None:
    edit = change(ProductAction.EDIT, replace(BILL, name="Bill"))
    assert check_send_back(edit, "checker", "Keep the old name") is None
    assert check_send_back(edit, "checker", " ") is Refusal.REASON_REQUIRED
    assert check_send_back(edit, "maker", "Mine") is Refusal.SAME_PERSON


def test_only_the_proposer_withdraws() -> None:
    edit = change(ProductAction.EDIT, replace(BILL, name="Bill"))
    assert check_withdrawal(edit, "maker") is None
    assert check_withdrawal(edit, "checker") is Refusal.NOT_THE_PROPOSER
