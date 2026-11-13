from datetime import date, datetime

import pytest

from app.modules.ledger.domain.entries import (
    MAX_LINES,
    AccountKind,
    AccountSpec,
    EntryDraft,
    InvalidEntry,
    Line,
    Side,
    credit,
    debit,
    normal_balance,
    reversal,
    sorted_lines,
)
from app.shared.money import Currency, Money

UGX = Currency.UGX
DAY = date(2026, 11, 13)
KEY = "deposit-0001"
A = "-Nabc0000000000000A1"
B = "-Nabc0000000000000B2"
C = "-Nabc0000000000000C3"


def ugx(minor: int) -> Money:
    return Money(minor, UGX)


def draft(*lines: Line, **changes: object) -> EntryDraft:
    values: dict[str, object] = {
        "entry_type": "payment.deposit_confirmed",
        "effective_date": DAY,
        "lines": lines or (debit(A, ugx(1_000)), credit(B, ugx(1_000))),
        "idempotency_key": KEY,
    }
    values.update(changes)
    return EntryDraft(**values)  # type: ignore[arg-type]


def test_debits_are_positive_and_credits_negative() -> None:
    assert debit(A, ugx(5)).amount == ugx(5)
    assert credit(A, ugx(5)).amount == ugx(-5)
    assert debit(A, ugx(5)).side is Side.DEBIT
    assert credit(A, ugx(5)).side is Side.CREDIT


@pytest.mark.parametrize("amount", [0, -5])
def test_debit_and_credit_take_positive_amounts(amount: int) -> None:
    with pytest.raises(InvalidEntry):
        debit(A, ugx(amount))
    with pytest.raises(InvalidEntry):
        credit(A, ugx(amount))


def test_a_line_needs_money_and_an_account() -> None:
    with pytest.raises(InvalidEntry):
        Line(A, 5)  # type: ignore[arg-type]
    with pytest.raises(InvalidEntry):
        Line(A, ugx(0))
    with pytest.raises(InvalidEntry):
        Line("", ugx(5))


def test_a_balanced_entry_is_accepted() -> None:
    entry = draft(debit(A, ugx(1_000)), credit(B, ugx(700)), credit(C, ugx(300)))
    assert entry.currency is UGX


def test_lines_must_sum_to_zero() -> None:
    with pytest.raises(InvalidEntry, match="sum to zero"):
        draft(debit(A, ugx(1_000)), credit(B, ugx(999)))


def test_an_entry_needs_two_lines() -> None:
    with pytest.raises(InvalidEntry):
        EntryDraft("a.b", DAY, (debit(A, ugx(1)),), KEY)
    with pytest.raises(InvalidEntry):
        EntryDraft("a.b", DAY, (), KEY)


def test_an_entry_has_a_bounded_number_of_lines() -> None:
    accounts = [f"-Nabc000000000000{i:04d}" for i in range(MAX_LINES + 1)]
    lines = (debit(accounts[0], ugx(MAX_LINES)), *(credit(a, ugx(1)) for a in accounts[1:]))
    with pytest.raises(InvalidEntry):
        draft(*lines)


def test_an_entry_is_in_one_currency() -> None:
    with pytest.raises(InvalidEntry, match="one currency"):
        draft(debit(A, ugx(100)), credit(B, Money(100, Currency.USD)))


def test_an_account_appears_once() -> None:
    with pytest.raises(InvalidEntry, match="at most once"):
        draft(debit(A, ugx(100)), credit(A, ugx(100)))


@pytest.mark.parametrize(
    "changes",
    [
        {"entry_type": "Deposit Confirmed"},
        {"entry_type": ""},
        {"entry_type": "x" * 101},
        {"idempotency_key": "short"},
        {"idempotency_key": "has spaces in it"},
        {"idempotency_key": "k" * 129},
        {"effective_date": datetime(2026, 11, 13)},
        {"effective_date": "2026-11-13"},
        {"source_type": "payment"},
        {"source_id": "abc"},
        {"source_type": "Payment", "source_id": "abc"},
        {"source_type": "payment", "source_id": "+256 700 000000"},
        {"reverses_entry_id": "not an id"},
        {"lines": [debit(A, ugx(1)), credit(B, ugx(1))]},
    ],
)
def test_malformed_entries_are_refused(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidEntry):
        draft(**changes)


def test_the_digest_ignores_line_order_and_the_key() -> None:
    first = draft(debit(A, ugx(10)), credit(B, ugx(4)), credit(C, ugx(6)))
    second = draft(
        credit(C, ugx(6)), debit(A, ugx(10)), credit(B, ugx(4)), idempotency_key="another-key"
    )
    assert first.digest() == second.digest()
    assert len(first.digest()) == 64


@pytest.mark.parametrize(
    "changes",
    [
        {"lines": (debit(A, ugx(11)), credit(B, ugx(11)))},
        {"lines": (debit(A, ugx(10)), credit(C, ugx(10)))},
        {"lines": (credit(A, ugx(10)), debit(B, ugx(10)))},
        {"lines": (debit(A, Money(10, Currency.USD)), credit(B, Money(10, Currency.USD)))},
        {"entry_type": "payment.payout_sent"},
        {"effective_date": date(2026, 11, 14)},
        {"source_type": "payment", "source_id": "p1"},
        {"reverses_entry_id": C},
    ],
)
def test_the_digest_changes_with_anything_that_matters(changes: dict[str, object]) -> None:
    base = draft(debit(A, ugx(10)), credit(B, ugx(10)))
    assert draft(**{"lines": base.lines, **changes}).digest() != base.digest()


def test_a_reversal_mirrors_the_original() -> None:
    original = draft(debit(A, ugx(1_000)), credit(B, ugx(700)), credit(C, ugx(300)))
    undo = reversal(C, original.lines, effective_date=DAY, idempotency_key="reverse-0001")
    assert undo.reverses_entry_id == C
    assert undo.entry_type == "ledger.reversal"
    assert (undo.source_type, undo.source_id) == ("ledger.entry", C)
    assert [(line.account_id, line.amount.minor) for line in undo.lines] == [
        (A, -1_000),
        (B, 700),
        (C, 300),
    ]


def test_lines_post_in_account_order() -> None:
    lines = (credit(C, ugx(1)), debit(A, ugx(2)), credit(B, ugx(1)))
    assert [line.account_id for line in sorted_lines(lines)] == [A, B, C]


@pytest.mark.parametrize(
    ("kind", "side"),
    [
        (AccountKind.ASSET, Side.DEBIT),
        (AccountKind.EXPENSE, Side.DEBIT),
        (AccountKind.LIABILITY, Side.CREDIT),
        (AccountKind.EQUITY, Side.CREDIT),
        (AccountKind.INCOME, Side.CREDIT),
    ],
)
def test_normal_sides(kind: AccountKind, side: Side) -> None:
    assert kind.normal_side is side
    assert normal_balance(kind, 500) == (500 if side is Side.DEBIT else -500)


def test_account_specs_are_validated() -> None:
    spec = AccountSpec("collections.iotec", AccountKind.ASSET, UGX, "ioTec collections")
    assert spec.owner_client_id is None
    for changes in (
        {"code": "Collections"},
        {"code": "collections..iotec"},
        {"name": "  "},
        {"name": "n" * 121},
        {"owner_client_id": "+256700000000"},
        {"reference": "has space"},
        {"kind": "asset"},
        {"currency": "UGX"},
    ):
        values = {
            "code": "collections.iotec",
            "kind": AccountKind.ASSET,
            "currency": UGX,
            "name": "ioTec collections",
            **changes,
        }
        with pytest.raises(InvalidEntry):
            AccountSpec(**values)  # type: ignore[arg-type]
