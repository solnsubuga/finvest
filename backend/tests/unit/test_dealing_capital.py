"""Finvest's own money in the buffer: the pure rules for paying in and taking out."""

from datetime import date

import pytest

from app.modules.dealing.domain.capital import (
    CAPITAL,
    PAID_IN_ENTRY,
    SOURCE_TYPE,
    TAKEN_OUT_ENTRY,
    Direction,
    MovementStatus,
    Proposal,
    Refusal,
    accounts,
    check_decision,
    check_proposal,
    check_taking_out,
    posting,
)
from app.modules.ledger.domain.entries import AccountKind
from app.shared.money import Currency, Money

TODAY = date(2026, 11, 19)


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


def paying_in(**changes: object) -> Proposal:
    values: dict[str, object] = {
        "direction": Direction.IN,
        "amount": ugx(1_000),
        "reason": "Top up",
        "bank_reference": "FT123",
        "value_date": TODAY,
    }
    values.update(changes)
    return Proposal(**values)  # type: ignore[arg-type]


def taking_out(**changes: object) -> Proposal:
    values: dict[str, object] = {
        "direction": Direction.OUT,
        "bank_reference": None,
        "value_date": None,
    }
    values.update(changes)
    return paying_in(**values)


@pytest.mark.parametrize(
    ("proposal", "refusal"),
    [
        (paying_in(), None),
        (paying_in(value_date=date(2026, 11, 1)), None),
        (paying_in(reason="  "), Refusal.REASON_REQUIRED),
        (paying_in(reason="x" * 501), Refusal.REASON_REQUIRED),
        (paying_in(amount=ugx(0)), Refusal.AMOUNT_NOT_POSITIVE),
        (paying_in(amount=ugx(-1)), Refusal.AMOUNT_NOT_POSITIVE),
        (paying_in(bank_reference=None), Refusal.REFERENCE_REQUIRED),
        (paying_in(bank_reference=" "), Refusal.REFERENCE_REQUIRED),
        (paying_in(bank_reference="x" * 101), Refusal.REFERENCE_REQUIRED),
        (paying_in(value_date=None), Refusal.VALUE_DATE_REQUIRED),
        (paying_in(value_date=date(2026, 11, 20)), Refusal.VALUE_DATE_IN_FUTURE),
        (taking_out(), None),
        (taking_out(bank_reference="FT1"), Refusal.NOT_FOR_TAKING_OUT),
        (taking_out(value_date=TODAY), Refusal.NOT_FOR_TAKING_OUT),
        (taking_out(amount=ugx(0)), Refusal.AMOUNT_NOT_POSITIVE),
    ],
)
def test_a_proposal_is_checked_before_it_is_saved(
    proposal: Proposal, refusal: Refusal | None
) -> None:
    assert check_proposal(proposal, TODAY) is refusal


@pytest.mark.parametrize(
    ("status", "decided_by", "refusal"),
    [
        (MovementStatus.PROPOSED, "grace", None),
        (MovementStatus.PROPOSED, "daniel", Refusal.SAME_PERSON),
        (MovementStatus.APPROVED, "grace", Refusal.ALREADY_DECIDED),
        (MovementStatus.DECLINED, "grace", Refusal.ALREADY_DECIDED),
    ],
)
def test_a_second_person_decides_once(
    status: MovementStatus, decided_by: str, refusal: Refusal | None
) -> None:
    assert check_decision(status, "daniel", decided_by) is refusal


@pytest.mark.parametrize(
    ("amount", "paid_in", "buffer", "refusal"),
    [
        (100, 100, 100, None),
        (100, 500, 100, None),
        (101, 100, 500, Refusal.MORE_THAN_PAID_IN),
        (101, 500, 100, Refusal.MORE_THAN_THE_BUFFER),
        (1, 500, 0, Refusal.MORE_THAN_THE_BUFFER),
        (1, 500, -10, Refusal.MORE_THAN_THE_BUFFER),
    ],
)
def test_taking_out_never_exceeds_what_was_paid_in_or_the_buffer(
    amount: int, paid_in: int, buffer: int, refusal: Refusal | None
) -> None:
    assert check_taking_out(ugx(amount), ugx(paid_in), ugx(buffer)) is refusal


@pytest.mark.parametrize(
    ("direction", "entry_type", "bank_sign", "capital_sign"),
    [
        (Direction.IN, PAID_IN_ENTRY, 1, -1),
        (Direction.OUT, TAKEN_OUT_ENTRY, -1, 1),
    ],
)
def test_the_trust_account_and_finvests_money_move_together(
    direction: Direction, entry_type: str, bank_sign: int, capital_sign: int
) -> None:
    draft = posting(
        "-Nmove", direction, ugx(250), TODAY, bank_account_id="bank", capital_account_id="cap"
    )
    assert (draft.entry_type, draft.effective_date) == (entry_type, TODAY)
    assert (draft.source_type, draft.source_id) == (SOURCE_TYPE, "-Nmove")
    # A debit is positive and a credit negative.
    amounts = {line.account_id: line.amount for line in draft.lines}
    assert amounts == {"bank": ugx(250 * bank_sign), "cap": ugx(250 * capital_sign)}


def test_finvests_money_is_equity_no_client_owns() -> None:
    spec = accounts(Currency.USD)[CAPITAL]
    assert (spec.kind, spec.owner_client_id, spec.reference) == (AccountKind.EQUITY, None, None)
