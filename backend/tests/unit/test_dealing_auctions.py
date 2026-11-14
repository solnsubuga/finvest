"""The auction calendar: what may be proposed and decided, each date's state, the next auction."""

from datetime import UTC, date, datetime

import pytest

from app.modules.dealing.domain.auctions import (
    Change,
    ChangeAction,
    Refusal,
    Status,
    allowed,
    cancellation_of,
    check_cancel,
    check_decision,
    check_new,
    next_auctions,
    state_of,
)

TODAY = date(2026, 11, 14)
AT = datetime(2026, 11, 10, 9, tzinfo=UTC)
ADA, BEN = "-Nstaffada", "-Nstaffben"


def change(
    change_id: str,
    day: date,
    status: Status = "approved",
    *,
    action: ChangeAction = "add",
    product: str = "TREA-91",
    cancels: str | None = None,
    by: str = ADA,
    at: datetime = AT,
) -> Change:
    decided = None if status == "proposed" else (by if status == "withdrawn" else BEN)
    return Change(
        id=change_id,
        action=action,
        product_code=product,
        auction_date=day,
        cancels=cancels,
        reason="The Bank moved it" if action == "cancel" else None,
        status=status,
        proposed_by=by,
        proposed_at=at,
        decided_by=decided,
        decided_at=None if decided is None else at,
    )


def cancel(change_id: str, of: Change, status: Status = "approved", **kwargs: object) -> Change:
    return change(
        change_id,
        of.auction_date,
        status,
        action="cancel",
        product=of.product_code,
        cancels=of.id,
        **kwargs,  # type: ignore[arg-type]
    )


DAY = date(2026, 11, 18)


def new(day: date, *changes: Change, treasury_bill: bool = True) -> Refusal | None:
    return check_new("TREA-91", day, treasury_bill=treasury_bill, today=TODAY, changes=changes)


# Proposing a date -----------------------------------------------------------------------------


def test_a_future_date_for_a_treasury_bill_may_be_proposed() -> None:
    assert new(DAY) is None


def test_today_may_be_proposed_because_bids_close_during_the_day() -> None:
    assert new(TODAY) is None


def test_a_day_already_past_is_refused() -> None:
    assert new(date(2026, 11, 13)) is Refusal.DATE_PASSED


def test_a_product_that_is_not_a_treasury_bill_has_no_auctions() -> None:
    assert new(DAY, treasury_bill=False) is Refusal.NOT_A_TREASURY_BILL


@pytest.mark.parametrize("status", ["proposed", "approved"])
def test_a_day_waiting_or_standing_is_not_proposed_again(status: Status) -> None:
    assert new(DAY, change("a", DAY, status)) is Refusal.ALREADY_ENTERED


def test_a_day_with_its_cancellation_waiting_still_stands() -> None:
    entry = change("a", DAY)
    assert new(DAY, entry, cancel("c", entry, "proposed")) is Refusal.ALREADY_ENTERED


@pytest.mark.parametrize("status", ["declined", "withdrawn"])
def test_a_day_declined_or_withdrawn_may_be_proposed_again(status: Status) -> None:
    assert new(DAY, change("a", DAY, status)) is None


def test_a_cancelled_day_may_be_proposed_again() -> None:
    entry = change("a", DAY)
    assert new(DAY, entry, cancel("c", entry)) is None


def test_another_product_on_the_same_day_does_not_stand_in_the_way() -> None:
    assert new(DAY, change("a", DAY, product="TREA-182")) is None


# States ---------------------------------------------------------------------------------------


def test_each_state_of_a_date() -> None:
    standing = change("a", DAY)
    assert state_of(change("w", DAY, "proposed"), []) == "waiting"
    assert state_of(change("d", DAY, "declined"), []) == "declined"
    assert state_of(change("x", DAY, "withdrawn"), []) == "withdrawn"
    assert state_of(standing, [standing]) == "standing"
    assert state_of(standing, [cancel("c", standing, "proposed")]) == "cancelling"
    assert state_of(standing, [cancel("c", standing)]) == "cancelled"
    assert state_of(standing, [cancel("c", standing, "declined")]) == "standing"


def test_the_live_cancellation_is_shown_before_one_declined() -> None:
    entry = change("a", DAY)
    old = cancel("c1", entry, "declined", at=datetime(2026, 11, 11, tzinfo=UTC))
    live = cancel("c2", entry, "proposed", at=datetime(2026, 11, 12, tzinfo=UTC))
    assert cancellation_of("a", [old, live]) == live
    assert cancellation_of("a", [old]) == old
    assert cancellation_of("a", []) is None


# Cancelling a date ----------------------------------------------------------------------------


def test_a_standing_date_is_cancelled_with_a_reason() -> None:
    entry = change("a", DAY)
    assert check_cancel(entry, "The Bank moved it to Thursday", [entry]) is None


@pytest.mark.parametrize("reason", ["", "   ", "\n"])
def test_a_cancellation_needs_a_reason(reason: str) -> None:
    entry = change("a", DAY)
    assert check_cancel(entry, reason, [entry]) is Refusal.REASON_REQUIRED


def test_one_cancellation_waits_at_a_time() -> None:
    entry = change("a", DAY)
    waiting = cancel("c", entry, "proposed")
    assert check_cancel(entry, "Again", [entry, waiting]) is Refusal.CANCELLATION_WAITING


@pytest.mark.parametrize("status", ["proposed", "declined", "withdrawn"])
def test_only_an_approved_date_is_cancelled(status: Status) -> None:
    entry = change("a", DAY, status)
    assert check_cancel(entry, "No", [entry]) is Refusal.NOT_STANDING


def test_a_cancelled_date_is_not_cancelled_again() -> None:
    entry = change("a", DAY)
    assert check_cancel(entry, "Again", [entry, cancel("c", entry)]) is Refusal.NOT_STANDING


def test_a_cancellation_is_not_itself_cancelled() -> None:
    entry = change("a", DAY)
    gone = cancel("c", entry)
    assert check_cancel(gone, "No", [entry, gone]) is Refusal.NOT_STANDING


# Deciding a change ----------------------------------------------------------------------------


def test_someone_else_approves_or_declines_with_a_note() -> None:
    waiting = change("a", DAY, "proposed")
    assert check_decision(waiting, "approve", BEN, TODAY) is None
    assert check_decision(waiting, "decline", BEN, TODAY, note="Wrong day") is None
    assert check_decision(waiting, "decline", BEN, TODAY, note="  ") is Refusal.NOTE_REQUIRED


def test_the_proposer_only_withdraws() -> None:
    waiting = change("a", DAY, "proposed")
    assert check_decision(waiting, "withdraw", ADA, TODAY) is None
    assert check_decision(waiting, "approve", ADA, TODAY) is Refusal.OWN_CHANGE
    assert check_decision(waiting, "decline", ADA, TODAY, note="No") is Refusal.OWN_CHANGE
    assert check_decision(waiting, "withdraw", BEN, TODAY) is Refusal.NOT_YOURS


@pytest.mark.parametrize("status", ["approved", "declined", "withdrawn"])
def test_a_decided_change_is_not_decided_again(status: Status) -> None:
    decided = change("a", DAY, status)
    assert check_decision(decided, "approve", BEN, TODAY) is Refusal.NOT_WAITING


def test_a_date_that_passed_while_it_waited_is_only_declined_or_withdrawn() -> None:
    late = change("a", date(2026, 11, 13), "proposed")
    assert check_decision(late, "approve", BEN, TODAY) is Refusal.DATE_PASSED
    assert check_decision(late, "decline", BEN, TODAY, note="Passed") is None
    assert allowed(late, [late], BEN, TODAY) == ["decline"]


def test_a_cancellation_of_a_date_that_passed_may_still_be_approved() -> None:
    entry = change("a", date(2026, 11, 13))
    waiting = cancel("c", entry, "proposed")
    assert check_decision(waiting, "approve", BEN, TODAY) is None


def test_the_steps_each_person_is_offered() -> None:
    waiting = change("w", DAY, "proposed")
    standing = change("s", DAY)
    past = change("p", date(2026, 11, 13))
    assert allowed(waiting, [waiting], ADA, TODAY) == ["withdraw"]
    assert allowed(waiting, [waiting], BEN, TODAY) == ["approve", "decline"]
    assert allowed(standing, [standing], ADA, TODAY) == ["cancel"]
    assert allowed(standing, [standing, cancel("c", standing, "proposed")], ADA, TODAY) == []
    assert allowed(past, [past], ADA, TODAY) == []
    assert allowed(change("d", DAY, "declined"), [], ADA, TODAY) == []


# The next auction -----------------------------------------------------------------------------


def test_the_next_auction_is_the_earliest_standing_date_from_today() -> None:
    moved = change("m", date(2026, 11, 16))
    waiting_cancel = change("wc", date(2026, 11, 17), product="TREA-182")
    changes = [
        change("a", date(2026, 12, 2)),
        change("b", DAY),
        change("old", date(2026, 11, 4)),
        moved,
        cancel("c", moved),
        change("w", date(2026, 11, 15), "proposed"),
        change("d", date(2026, 11, 15), "declined"),
        waiting_cancel,
        cancel("c2", waiting_cancel, "proposed"),
    ]
    assert next_auctions(changes, TODAY) == {"TREA-91": DAY, "TREA-182": date(2026, 11, 17)}


def test_a_product_with_only_past_waiting_or_cancelled_dates_has_no_next_auction() -> None:
    gone = change("g", DAY)
    changes = [
        change("old", date(2026, 11, 4)),
        change("w", DAY, "proposed"),
        gone,
        cancel("c", gone),
    ]
    assert next_auctions(changes, TODAY) == {}
