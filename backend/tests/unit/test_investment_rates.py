from dataclasses import replace
from datetime import date

import pytest

from app.modules.investments.domain.products import SEED_PRODUCTS
from app.modules.investments.domain.rates import (
    SEED_RATES,
    InvalidRate,
    ProposalStatus,
    RateProposal,
    RateVersion,
    Refusal,
    check_approval,
    check_proposal,
    check_send_back,
    check_withdrawal,
    rate_in_force,
)

TODAY = date(2026, 11, 13)
LIVE = RateVersion(date(2026, 11, 2), annual_rate_bps=1375, tax_bps=1500)
LOWER = RateVersion(date(2026, 11, 16), annual_rate_bps=1350, tax_bps=1500)
PROPOSAL = RateProposal(LOWER, proposed_by="daniel", reason="Auction on 11 Nov cleared lower")


def test_every_seeded_product_has_its_original_rate() -> None:
    assert {product.code for product in SEED_PRODUCTS} == set(SEED_RATES)
    assert SEED_RATES == {
        "TREA-91": (1250, 1500),
        "TREA-182": (1375, 1500),
        "USD-BOND": (725, 500),
    }


@pytest.mark.parametrize(
    ("rate", "tax", "shown"),
    [
        (1250, 1500, "10.63"),  # 10.625 rounds to the nearest
        (1375, 1500, "11.69"),  # 11.6875
        (725, 500, "6.89"),  # 6.8875
        (1350, 1500, "11.48"),  # 11.475
        (1000, 0, "10.00"),
        (1, 9999, "0.00"),
        (5000, 0, "50.00"),
    ],
)
def test_clients_see_the_yearly_rate_after_tax(rate: int, tax: int, shown: str) -> None:
    assert RateVersion(TODAY, rate, tax).client_rate_percent == shown


@pytest.mark.parametrize(("rate", "tax"), [(0, 1500), (5001, 0), (1250, 10_000), (12.5, 1500)])
def test_a_rate_out_of_range_is_refused(rate: int, tax: int) -> None:
    with pytest.raises(InvalidRate):
        RateVersion(TODAY, rate, tax)


def test_the_rate_in_force_is_the_latest_one_that_has_started() -> None:
    versions = [LOWER, LIVE]
    assert rate_in_force(versions, date(2026, 11, 1)) is None
    assert rate_in_force(versions, date(2026, 11, 2)) is LIVE
    assert rate_in_force(versions, date(2026, 11, 15)) is LIVE
    assert rate_in_force(versions, date(2026, 11, 16)) is LOWER
    assert rate_in_force(versions, date(2030, 1, 1)) is LOWER
    assert rate_in_force([], TODAY) is None


def test_two_rates_cannot_start_on_the_same_day() -> None:
    with pytest.raises(InvalidRate):
        rate_in_force([LIVE, replace(LIVE, annual_rate_bps=1400)], TODAY)


def test_a_proposal_needs_a_reason_a_future_date_and_a_real_change() -> None:
    reason = "Auction cleared lower"
    assert check_proposal(LOWER, reason, [LIVE], TODAY) is None
    assert check_proposal(LOWER, "  ", [LIVE], TODAY) is Refusal.REASON_REQUIRED
    assert check_proposal(LOWER, "x" * 501, [LIVE], TODAY) is Refusal.REASON_REQUIRED
    today_rate = replace(LOWER, effective_from=TODAY)
    assert check_proposal(today_rate, reason, [LIVE], TODAY) is Refusal.EFFECTIVE_DATE_NOT_IN_FUTURE
    same = replace(LIVE, effective_from=date(2026, 11, 16))
    assert check_proposal(same, reason, [LIVE], TODAY) is Refusal.NOTHING_CHANGES
    assert check_proposal(LOWER, reason, [LIVE, LOWER], TODAY) is Refusal.DATE_ALREADY_TAKEN


def test_the_first_rate_for_a_product_can_be_proposed() -> None:
    assert check_proposal(LOWER, "New product", [], TODAY) is None


def test_a_tax_change_alone_is_a_change() -> None:
    new_tax = RateVersion(date(2026, 11, 16), annual_rate_bps=1375, tax_bps=2000)
    assert check_proposal(new_tax, "Finance Act", [LIVE], TODAY) is None


def test_a_second_person_approves() -> None:
    assert check_approval(PROPOSAL, "grace", [LIVE], TODAY) is None
    assert check_approval(PROPOSAL, "daniel", [LIVE], TODAY) is Refusal.SAME_PERSON


def test_an_approval_that_comes_too_late_is_refused() -> None:
    on_the_day = date(2026, 11, 16)
    assert check_approval(PROPOSAL, "grace", [LIVE], on_the_day) is (
        Refusal.EFFECTIVE_DATE_NOT_IN_FUTURE
    )


def test_an_approval_is_checked_against_rates_approved_in_the_meantime() -> None:
    other = RateVersion(date(2026, 11, 16), annual_rate_bps=1300, tax_bps=1500)
    assert check_approval(PROPOSAL, "grace", [LIVE, other], TODAY) is Refusal.DATE_ALREADY_TAKEN
    earlier = RateVersion(date(2026, 11, 14), annual_rate_bps=1350, tax_bps=1500)
    assert check_approval(PROPOSAL, "grace", [LIVE, earlier], TODAY) is Refusal.NOTHING_CHANGES


@pytest.mark.parametrize(
    "status", [ProposalStatus.APPROVED, ProposalStatus.SENT_BACK, ProposalStatus.WITHDRAWN]
)
def test_a_decided_proposal_cannot_be_decided_again(status: ProposalStatus) -> None:
    decided = replace(PROPOSAL, status=status)
    assert check_approval(decided, "grace", [LIVE], TODAY) is Refusal.ALREADY_DECIDED
    assert check_send_back(decided, "grace", "Check the auction") is Refusal.ALREADY_DECIDED
    assert check_withdrawal(decided, "daniel") is Refusal.ALREADY_DECIDED


def test_sending_back_needs_a_second_person_and_a_note() -> None:
    assert check_send_back(PROPOSAL, "grace", "Check the auction result") is None
    assert check_send_back(PROPOSAL, "daniel", "Changed my mind") is Refusal.SAME_PERSON
    assert check_send_back(PROPOSAL, "grace", "") is Refusal.REASON_REQUIRED


def test_only_the_proposer_withdraws() -> None:
    assert check_withdrawal(PROPOSAL, "daniel") is None
    assert check_withdrawal(PROPOSAL, "grace") is Refusal.NOT_THE_PROPOSER
