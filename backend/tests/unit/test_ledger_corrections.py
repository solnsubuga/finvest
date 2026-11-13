from datetime import UTC, date, datetime

import pytest

from app.modules.ledger.domain.corrections import (
    MAX_REASON_LENGTH,
    build_proposal,
    clean_reason,
    reversal_lines,
    staff_id,
)
from app.modules.ledger.domain.entries import InvalidEntry, Line, credit, debit
from app.shared.actors import SYSTEM, Actor
from app.shared.money import Currency, Money

MAKER = Actor("staff", "-Nstaff0000000000001")
ENTRY = "-Nentry0000000000001"
PROPOSAL = "-Nprop00000000000001"
A = "-Nabc0000000000000A1"
B = "-Nabc0000000000000B2"
DAY = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def ugx(minor: int) -> Money:
    return Money(minor, Currency.UGX)


def proposal(**changes: object):  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "proposal_id": PROPOSAL,
        "proposer": MAKER,
        "corrects_entry_id": ENTRY,
        "entry_type": "payment.deposit_correction",
        "effective_date": DAY,
        "lines": (debit(A, ugx(500)), credit(B, ugx(500))),
        "reason": "Deposit credited to the wrong client",
        "proposed_at": NOW,
    }
    values.update(changes)
    return build_proposal(**values)  # type: ignore[arg-type]


def test_the_approved_entry_comes_only_from_the_proposal() -> None:
    draft = proposal().draft()
    assert draft.entry_type == "payment.deposit_correction"
    assert draft.effective_date == DAY
    assert draft.lines == (debit(A, ugx(500)), credit(B, ugx(500)))
    assert (draft.source_type, draft.source_id) == ("ledger.correction", PROPOSAL)
    assert draft.idempotency_key == f"ledger.correction:{PROPOSAL}"
    assert draft.reverses_entry_id is None


def test_a_reversal_proposal_reverses_the_entry_it_corrects() -> None:
    original = (debit(A, ugx(500)), credit(B, ugx(500)))
    mirrored = reversal_lines(original)
    assert mirrored == (Line(A, ugx(-500)), Line(B, ugx(500)))
    draft = proposal(lines=mirrored, reverses=True).draft()
    assert draft.reverses_entry_id == ENTRY


@pytest.mark.parametrize(
    "changes",
    [
        {"lines": (debit(A, ugx(500)), credit(B, ugx(499)))},
        {"lines": (debit(A, ugx(500)),)},
        {"entry_type": "Deposit Correction"},
        {"reason": "   "},
        {"reason": None},
        {"proposer": Actor("client", "-Nclient000000000001")},
        {"proposer": SYSTEM},
        {"corrects_entry_id": "not an id"},
    ],
)
def test_a_bad_proposal_is_refused_before_it_is_stored(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidEntry):
        proposal(**changes)


def test_only_staff_make_and_decide_corrections() -> None:
    assert staff_id(MAKER) == MAKER.id
    for actor in (SYSTEM, Actor("client", "-Nclient000000000001")):
        with pytest.raises(InvalidEntry):
            staff_id(actor)


def test_reasons_are_trimmed_redacted_and_bounded() -> None:
    assert clean_reason("  Wrong client  ") == "Wrong client"
    cleaned = clean_reason("Client rang from 0700 123 456 " + "x" * 600)
    assert "0700" not in cleaned
    assert len(cleaned) == MAX_REASON_LENGTH
    assert proposal(reason="Sent by jane@example.com").reason == "Sent by [redacted-email]"
