"""The rules for a bank transfer receipt and for matching it to a statement line."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.modules.payments.domain.bank_transfers import (
    MATCH_WINDOW,
    MAX_RECEIPT_AGE,
    MAX_RECEIPT_BYTES,
    InvalidReceipt,
    LineFacts,
    ReceiptDetails,
    ReceiptFile,
    check_receipt_bytes,
    check_receipt_details,
    check_receipt_file,
    match_problem,
    match_window,
)
from app.modules.payments.domain.deposits import TRANSITIONS, DepositStatus, Method, Network
from app.shared.money import Currency, Money

TODAY = date(2026, 11, 20)
UGX = Currency.UGX


def ugx(minor: int) -> Money:
    return Money(minor, UGX)


FILE = ReceiptFile("application/pdf", 2048, "a" * 64)
DETAILS = ReceiptDetails(ugx(1_000_000), date(2026, 11, 18), "Stanbic Bank", "FT2632100042")


@pytest.mark.parametrize("content_type", ["application/pdf", "image/jpeg", "image/png"])
def test_a_photo_or_a_pdf_is_accepted(content_type: str) -> None:
    assert check_receipt_file(replace(FILE, content_type=content_type))


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"content_type": "image/gif"}, "unsupported_content_type"),
        ({"content_type": "application/pdf; x"}, "unsupported_content_type"),
        ({"byte_size": 0}, "invalid_size"),
        ({"byte_size": MAX_RECEIPT_BYTES + 1}, "invalid_size"),
        ({"byte_size": True}, "invalid_size"),
        ({"sha256": "A" * 64}, "invalid_digest"),
        ({"sha256": "a" * 63}, "invalid_digest"),
    ],
)
def test_a_bad_file_is_refused(change: dict[str, object], code: str) -> None:
    with pytest.raises(InvalidReceipt) as error:
        check_receipt_file(replace(FILE, **change))  # type: ignore[arg-type]
    assert (error.value.field, error.value.code) == ("file", code)


def test_the_largest_file_is_accepted() -> None:
    assert check_receipt_file(replace(FILE, byte_size=MAX_RECEIPT_BYTES))


def test_details_are_tidied() -> None:
    tidied = check_receipt_details(
        replace(DETAILS, bank_name="  Stanbic \t Bank ", transaction_number=" FT 26/321-0042 "),
        today=TODAY,
    )
    assert tidied.bank_name == "Stanbic Bank"
    assert tidied.transaction_number == "FT 26/321-0042"


@pytest.mark.parametrize("sent_on", [TODAY, TODAY - MAX_RECEIPT_AGE])
def test_a_transfer_from_today_back_to_the_age_limit_is_accepted(sent_on: date) -> None:
    assert check_receipt_details(replace(DETAILS, sent_on=sent_on), today=TODAY)


@pytest.mark.parametrize(
    ("change", "field", "code"),
    [
        ({"amount": Money(1000, Currency.USD)}, "amount", "unsupported_currency"),
        ({"amount": ugx(0)}, "amount", "invalid_amount"),
        ({"amount": ugx(-5)}, "amount", "invalid_amount"),
        ({"sent_on": TODAY + timedelta(days=1)}, "sent_on", "in_the_future"),
        ({"sent_on": TODAY - MAX_RECEIPT_AGE - timedelta(days=1)}, "sent_on", "too_old"),
        ({"bank_name": "B"}, "bank_name", "invalid_bank_name"),
        ({"bank_name": "B" * 81}, "bank_name", "invalid_bank_name"),
        ({"transaction_number": ""}, "transaction_number", "invalid_transaction_number"),
        ({"transaction_number": "-FT1"}, "transaction_number", "invalid_transaction_number"),
        ({"transaction_number": "FT<1>"}, "transaction_number", "invalid_transaction_number"),
        ({"transaction_number": "F" * 65}, "transaction_number", "invalid_transaction_number"),
    ],
)
def test_bad_details_are_refused(change: dict[str, object], field: str, code: str) -> None:
    with pytest.raises(InvalidReceipt) as error:
        check_receipt_details(replace(DETAILS, **change), today=TODAY)  # type: ignore[arg-type]
    assert (error.value.field, error.value.code) == (field, code)


SENT = date(2026, 11, 18)


@pytest.mark.parametrize("offset", [0, 1, MATCH_WINDOW.days])
def test_a_line_in_for_the_amount_within_the_window_matches(offset: int) -> None:
    line = LineFacts(True, ugx(1_000_000), SENT + timedelta(days=offset))
    assert match_problem(owed=ugx(1_000_000), sent_on=SENT, line=line) is None


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        (LineFacts(False, ugx(1_000_000), SENT), "line_is_money_out"),
        (LineFacts(True, ugx(999_999), SENT), "line_amount_differs"),
        (LineFacts(True, ugx(1_000_001), SENT), "line_amount_differs"),
        (LineFacts(True, Money(1_000_000, Currency.USD), SENT), "line_amount_differs"),
        (LineFacts(True, ugx(1_000_000), SENT - timedelta(days=1)), "line_outside_window"),
        (
            LineFacts(True, ugx(1_000_000), SENT + MATCH_WINDOW + timedelta(days=1)),
            "line_outside_window",
        ),
    ],
)
def test_a_line_that_cannot_be_the_transfer_is_refused(line: LineFacts, problem: str) -> None:
    assert match_problem(owed=ugx(1_000_000), sent_on=SENT, line=line) == problem


def test_the_window_runs_from_the_day_sent() -> None:
    assert match_window(SENT) == (SENT, SENT + MATCH_WINDOW)


def test_a_transfer_in_review_ends_confirmed_or_failed_and_nothing_else_reaches_review() -> None:
    assert TRANSITIONS[DepositStatus.IN_REVIEW] == {DepositStatus.CONFIRMED, DepositStatus.FAILED}
    assert all(DepositStatus.IN_REVIEW not in moves for moves in TRANSITIONS.values())


def test_only_mobile_money_has_a_network() -> None:
    assert Method.MTN.network is Network.MTN
    assert Method.AIRTEL.network is Network.AIRTEL
    assert Method.BANK_TRANSFER.network is None


@pytest.mark.parametrize(
    ("content_type", "data"),
    [
        ("application/pdf", b"%PDF-1.7\n..."),
        ("image/jpeg", b"\xff\xd8\xff\xe0\x00\x10JFIF"),
        ("image/png", b"\x89PNG\r\n\x1a\n\x00\x00"),
    ],
)
def test_a_receipt_that_begins_like_its_type_passes(content_type: str, data: bytes) -> None:
    check_receipt_bytes(content_type, data)


@pytest.mark.parametrize(
    ("content_type", "data"),
    [
        ("image/png", b"%PDF-1.7\n..."),
        ("application/pdf", b"<html><script>"),
        ("image/jpeg", b""),
        ("text/html", b"%PDF-1.7"),
    ],
)
def test_a_receipt_whose_bytes_are_not_its_type_is_refused(content_type: str, data: bytes) -> None:
    with pytest.raises(InvalidReceipt) as refused:
        check_receipt_bytes(content_type, data)
    assert (refused.value.field, refused.value.code) == ("file", "content_does_not_match_type")
