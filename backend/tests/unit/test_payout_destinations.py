from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.modules.payouts.domain.destinations import (
    SAFETY_WAIT,
    BankAccount,
    Destination,
    DestinationError,
    DestinationKind,
    DestinationStatus,
    InvalidDestination,
    MobileMoneyNumber,
    MobileNetwork,
    NameMatch,
    NotPayable,
    ProofCheck,
    ProofKind,
    RejectionReason,
    approve,
    compare_names,
    confirm_code,
    payable_from,
    proof_fits,
    record_check,
    record_first_payout,
    reject,
    remove,
    statement_is_recent,
    submit_proof,
    why_not_payable,
)

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
LETTER = ProofCheck(ProofKind.BANK_LETTER, NameMatch.EXACT)


def in_review(kind: DestinationKind = DestinationKind.BANK) -> Destination:
    proof = (
        ProofKind.BANK_LETTER if kind is DestinationKind.BANK else ProofKind.NETWORK_REGISTERED_NAME
    )
    return submit_proof(confirm_code(Destination.new(kind)), proof)


CHECKER, APPROVER = "-Nabc0000000000000S1", "-Nabc0000000000000S2"


def checked(destination: Destination | None = None, check: ProofCheck = LETTER) -> Destination:
    return record_check(destination or in_review(), check, CHECKER, NOW)


def approved(at: datetime = NOW) -> Destination:
    return approve(checked(), LETTER, APPROVER, at)


# --- Mobile money numbers ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    [
        "0772123456",
        "772123456",
        "256772123456",
        "+256772123456",
        "+256 772 123 456",
        " 0772123456 ",
    ],
)
def test_a_number_is_stored_one_way_however_it_is_typed(typed: str) -> None:
    assert MobileMoneyNumber.parse(typed).msisdn == "256772123456"


@pytest.mark.parametrize(
    ("typed", "network"),
    [
        ("0762000000", MobileNetwork.MTN),
        ("0772000000", MobileNetwork.MTN),
        ("0782000000", MobileNetwork.MTN),
        ("0392000000", MobileNetwork.MTN),
        ("0702000000", MobileNetwork.AIRTEL),
        ("0742000000", MobileNetwork.AIRTEL),
        ("0752000000", MobileNetwork.AIRTEL),
        ("0202000000", MobileNetwork.AIRTEL),
    ],
)
def test_the_prefix_names_the_network(typed: str, network: MobileNetwork) -> None:
    assert MobileMoneyNumber.parse(typed).network is network


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "07721234",
        "07721234567",
        "0772-123-456",
        "0772  123456",
        "++256772123456",
        "254772123456",  # Kenya
        "٠٧٧٢١٢٣٤٥٦",
        "o772123456",
    ],
)
def test_a_number_that_is_not_ugandan_mobile_is_refused(typed: str) -> None:
    with pytest.raises(InvalidDestination) as refused:
        MobileMoneyNumber.parse(typed)
    assert refused.value.code == "phone_invalid"


@pytest.mark.parametrize("typed", ["0712000000", "0792000000", "0412000000"])
def test_a_network_we_cannot_pay_is_refused(typed: str) -> None:
    with pytest.raises(InvalidDestination) as refused:
        MobileMoneyNumber.parse(typed)
    assert refused.value.code == "phone_network_unsupported"


def test_a_refusal_never_carries_what_was_typed() -> None:
    with pytest.raises(InvalidDestination) as refused:
        MobileMoneyNumber.parse("0772-123-456")
    assert "0772" not in str(refused.value)


def test_the_masked_number_hides_the_middle() -> None:
    masked = MobileMoneyNumber.parse("0772123456").masked
    assert masked == "+256 77• ••• 456"
    assert "123" not in masked


# --- Bank accounts ----------------------------------------------------------------------------


@pytest.mark.parametrize("typed", ["9030012345678", "9030 0123 45678", "9030-0123-45678"])
def test_a_bank_account_number_is_stored_as_digits(typed: str) -> None:
    account = BankAccount.parse("stanbic", typed)
    assert account.account_number == "9030012345678"
    assert account.masked == "•••• 5678"


@pytest.mark.parametrize(
    "typed", ["", "12345", "1" * 21, "9030 0123  45678", "9030A12345678", "-9030012345678", "1.5e9"]
)
def test_a_bad_bank_account_number_is_refused(typed: str) -> None:
    with pytest.raises(InvalidDestination) as refused:
        BankAccount.parse("stanbic", typed)
    assert refused.value.code == "bank_account_number_invalid"


@pytest.mark.parametrize("bank_code", ["", "Stanbic", "stanbic bank", "1bank", "s", "x" * 33])
def test_a_bank_code_is_a_short_lowercase_name(bank_code: str) -> None:
    with pytest.raises(InvalidDestination) as refused:
        BankAccount(bank_code, "9030012345678")
    assert refused.value.code == "bank_invalid"


# --- The name lock ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("id_name", "proof_name"),
    [
        ("Nakato Sarah", "Nakato Sarah"),
        ("Nakato Sarah", "SARAH NAKATO"),
        ("Nakato Sarah", "  sarah   nakato "),
        ("Nakato, Sarah", "Sarah Nakato."),
        ("Zoë Kyomuhendo", "Zoe Kyomuhendo"),
        ("O'Brien Mary", "Mary OBrien"),
        ("O\N{RIGHT SINGLE QUOTATION MARK}Brien Mary", "Mary O'Brien"),
        ("Nakato Sarah Grace", "Grace Nakato Sarah"),
    ],
)
def test_the_same_names_in_any_order_match_exactly(id_name: str, proof_name: str) -> None:
    assert compare_names(id_name, proof_name) is NameMatch.EXACT


@pytest.mark.parametrize(
    ("id_name", "proof_name"),
    [
        ("Nakato Sarah Grace", "Sarah Nakato"),
        ("Sarah Nakato", "Nakato Sarah Grace"),
        ("NAKATO, Sarah-Grace", "grace sarah nakato mary"),
    ],
)
def test_a_missing_name_is_for_a_person_to_judge(id_name: str, proof_name: str) -> None:
    assert compare_names(id_name, proof_name) is NameMatch.PARTIAL


@pytest.mark.parametrize(
    ("id_name", "proof_name"),
    [
        ("Nakato Sarah", "Nakato Peter"),
        ("Nakato Sarah", "Nakato"),
        ("Nakato Sarah Grace", "Nakato Sarah Mary"),
        ("Nakato Sarah", "S Nakato"),
        ("Nakato Sarah", ""),
        ("", ""),
        ("Nakato Sarah", "..."),
        ("Nakato Sarah", "Nakatos Arah"),
        # A name spelt differently is a different name, however close.
        ("Mohammed Ssali", "Muhammad Ssali"),
        ("Ssali Mohammed Kato", "Kato Muhammed Ssali"),
        ("Catherine Namusoke", "Cathrine Namusoke"),
        ("Nakato Sarah", "Nakatto Sarah"),
        ("Nakato Sarah", "Nakate Sarah"),
        ("Namara Sarah", "Namata Sarah"),
        ("Nakato Sarah", "Nakato Sara"),
        ("Mohammed", "Muhammad"),
        ("Nakato Babirye", "Nakato Nabirye"),
        ("Mohammed Ssali Kato", "Muhammad Ssali"),
    ],
)
def test_other_names_are_different(id_name: str, proof_name: str) -> None:
    assert compare_names(id_name, proof_name) is NameMatch.DIFFERENT


def test_the_comparison_is_symmetric() -> None:
    names = ["Nakato Sarah", "Sarah Nakato Grace", "Nakato Peter", "Nakato", ""]
    for one in names:
        for other in names:
            assert compare_names(one, other) is compare_names(other, one)


# --- Proof ------------------------------------------------------------------------------------


def test_each_kind_of_destination_takes_its_own_proof() -> None:
    accepted = {
        (kind, proof) for kind in DestinationKind for proof in ProofKind if proof_fits(kind, proof)
    }
    assert accepted == {
        (DestinationKind.BANK, ProofKind.BANK_STATEMENT),
        (DestinationKind.BANK, ProofKind.BANK_LETTER),
        (DestinationKind.BANK, ProofKind.BANK_CARD),
        (DestinationKind.MOBILE_MONEY, ProofKind.NETWORK_REGISTERED_NAME),
    }


@pytest.mark.parametrize(
    ("statement_date", "today", "recent"),
    [
        (date(2026, 11, 13), date(2026, 11, 13), True),
        (date(2026, 8, 13), date(2026, 11, 13), True),
        (date(2026, 8, 12), date(2026, 11, 13), False),
        (date(2026, 11, 14), date(2026, 11, 13), False),  # dated ahead
        (date(2025, 11, 20), date(2026, 2, 10), True),  # across a year end
        (date(2025, 11, 9), date(2026, 2, 10), False),
        (date(2026, 2, 28), date(2026, 5, 31), True),  # no 31 February
        (date(2026, 2, 27), date(2026, 5, 31), False),
        (date(2024, 2, 29), date(2024, 5, 31), True),  # leap year
        (date(2024, 2, 28), date(2024, 5, 31), False),
        (date(2025, 12, 31), date(2026, 3, 31), True),
        (date(2025, 12, 30), date(2026, 3, 31), False),
    ],
)
def test_a_statement_counts_for_three_calendar_months(
    statement_date: date, today: date, recent: bool
) -> None:
    assert statement_is_recent(statement_date, today) is recent


# --- The life of a destination ----------------------------------------------------------------


def test_a_new_destination_walks_every_step_before_it_is_approved() -> None:
    destination = Destination.new(DestinationKind.BANK)
    assert destination.status is DestinationStatus.AWAITING_CODE
    destination = confirm_code(destination)
    assert destination.status is DestinationStatus.AWAITING_PROOF
    destination = submit_proof(destination, ProofKind.BANK_CARD)
    assert destination.status is DestinationStatus.IN_REVIEW
    card = ProofCheck(ProofKind.BANK_CARD, NameMatch.EXACT)
    destination = record_check(destination, card, CHECKER, NOW)
    assert (destination.status, destination.checked_by) == (DestinationStatus.IN_REVIEW, CHECKER)
    destination = approve(destination, card, APPROVER, NOW)
    assert destination.status is DestinationStatus.APPROVED
    assert destination.approved_at == NOW


def test_approval_takes_a_check_and_a_second_person() -> None:
    with pytest.raises(DestinationError) as unchecked:
        approve(in_review(), LETTER, APPROVER, NOW)
    assert unchecked.value.code == "destination_not_checked"
    with pytest.raises(DestinationError) as same:
        approve(checked(), LETTER, CHECKER, NOW)
    assert same.value.code == "checked_by_you"
    # A check made again by someone else replaces the first, and its maker cannot approve it.
    again = record_check(checked(), LETTER, APPROVER, NOW)
    with pytest.raises(DestinationError):
        approve(again, LETTER, APPROVER, NOW)
    assert approve(again, LETTER, CHECKER, NOW).status is DestinationStatus.APPROVED


def test_new_proof_after_a_refusal_needs_a_new_check() -> None:
    refused = reject(checked(), RejectionReason.PROOF_UNREADABLE, APPROVER)
    assert refused.checked_by == APPROVER
    again = submit_proof(refused, ProofKind.BANK_LETTER)
    assert again.checked_by is None
    with pytest.raises(DestinationError) as unchecked:
        approve(again, LETTER, CHECKER, NOW)
    assert unchecked.value.code == "destination_not_checked"


def test_no_step_can_be_skipped() -> None:
    new = Destination.new(DestinationKind.BANK)
    confirmed = confirm_code(new)
    for skipped in (new, confirmed, approved(), remove(new)):
        with pytest.raises(DestinationError):
            record_check(skipped, LETTER, CHECKER, NOW)
        with pytest.raises(DestinationError):
            approve(skipped, LETTER, APPROVER, NOW)
    for not_ready in (new, in_review(), approved(), remove(new)):
        with pytest.raises(DestinationError):
            submit_proof(not_ready, ProofKind.BANK_LETTER)
    for not_new in (confirmed, in_review(), approved(), remove(new)):
        with pytest.raises(DestinationError):
            confirm_code(not_new)
    for not_reviewable in (new, confirmed, approved(), remove(new)):
        with pytest.raises(DestinationError):
            reject(not_reviewable, RejectionReason.PROOF_UNREADABLE, CHECKER)


def test_a_different_name_can_never_be_approved() -> None:
    different = ProofCheck(ProofKind.BANK_LETTER, NameMatch.DIFFERENT)
    with pytest.raises(DestinationError) as refused:
        checked(check=different)
    assert refused.value.code == "name_different"
    # Nor by a second person, whatever the first recorded.
    with pytest.raises(DestinationError) as approved_anyway:
        approve(checked(), different, APPROVER, NOW)
    assert approved_anyway.value.code == "name_different"


def test_a_partial_name_can_be_approved_by_a_person() -> None:
    check = ProofCheck(ProofKind.BANK_LETTER, NameMatch.PARTIAL)
    assert approve(checked(check=check), check, APPROVER, NOW).status is (
        DestinationStatus.APPROVED
    )


def test_a_statement_needs_a_recent_date() -> None:
    def check(statement_date: date | None) -> ProofCheck:
        return ProofCheck(ProofKind.BANK_STATEMENT, NameMatch.EXACT, statement_date)

    statement = submit_proof(
        confirm_code(Destination.new(DestinationKind.BANK)), ProofKind.BANK_STATEMENT
    )
    with pytest.raises(DestinationError) as missing:
        checked(statement, check(None))
    assert missing.value.code == "statement_date_required"
    with pytest.raises(DestinationError) as old:
        checked(statement, check(date(2026, 8, 12)))
    assert old.value.code == "statement_too_old"
    recent = check(date(2026, 8, 13))
    assert approve(checked(statement, recent), recent, APPROVER, NOW).status is (
        DestinationStatus.APPROVED
    )
    # Approved a day later, the same statement has grown too old.
    with pytest.raises(DestinationError) as stale:
        approve(checked(statement, recent), recent, APPROVER, NOW + timedelta(days=1))
    assert stale.value.code == "statement_too_old"


def test_a_bank_account_is_not_approved_on_mobile_money_proof() -> None:
    check = ProofCheck(ProofKind.NETWORK_REGISTERED_NAME, NameMatch.EXACT)
    with pytest.raises(DestinationError) as refused:
        checked(check=check)
    assert refused.value.code == "proof_kind_not_accepted"
    with pytest.raises(DestinationError):
        submit_proof(
            confirm_code(Destination.new(DestinationKind.MOBILE_MONEY)), ProofKind.BANK_CARD
        )


def test_a_rejected_destination_can_be_proved_again() -> None:
    rejected = reject(in_review(), RejectionReason.PROOF_UNREADABLE, CHECKER)
    assert rejected.status is DestinationStatus.REJECTED
    assert rejected.rejection_reason is RejectionReason.PROOF_UNREADABLE
    again = submit_proof(rejected, ProofKind.BANK_STATEMENT)
    assert again.status is DestinationStatus.IN_REVIEW
    assert again.rejection_reason is None


def test_a_rejection_for_suspected_fraud_cannot_be_proved_again() -> None:
    rejected = reject(in_review(), RejectionReason.SUSPECTED_FRAUD, CHECKER)
    with pytest.raises(DestinationError) as refused:
        submit_proof(rejected, ProofKind.BANK_STATEMENT)
    assert refused.value.code == "destination_rejected_final"
    assert remove(rejected).status is DestinationStatus.REMOVED
    for reason in RejectionReason:
        if reason is not RejectionReason.SUSPECTED_FRAUD:
            again = submit_proof(reject(in_review(), reason, CHECKER), ProofKind.BANK_LETTER)
            assert again.status is DestinationStatus.IN_REVIEW


def test_a_removed_destination_stays_removed() -> None:
    removed = remove(approved())
    assert removed.status is DestinationStatus.REMOVED
    with pytest.raises(DestinationError):
        remove(removed)
    assert why_not_payable(removed, NOW + timedelta(days=30)) is NotPayable.NOT_APPROVED


def test_times_must_carry_a_zone() -> None:
    naive = datetime(2026, 11, 13, 9, 0)
    with pytest.raises(ValueError):
        record_check(in_review(), LETTER, CHECKER, naive)
    with pytest.raises(ValueError):
        approve(checked(), LETTER, APPROVER, naive)
    with pytest.raises(ValueError):
        why_not_payable(approved(), naive)


# --- The safety wait --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status", [s for s in DestinationStatus if s is not DestinationStatus.APPROVED]
)
def test_only_an_approved_destination_is_payable(status: DestinationStatus) -> None:
    destination = replace(approved(), status=status)
    assert payable_from(destination) is None
    assert why_not_payable(destination, NOW + timedelta(days=30)) is NotPayable.NOT_APPROVED


def test_the_wait_is_24_hours_from_approval_to_the_second() -> None:
    destination = approved(NOW)
    assert payable_from(destination) == NOW + SAFETY_WAIT == NOW + timedelta(hours=24)
    just_before = NOW + timedelta(hours=24) - timedelta(microseconds=1)
    assert why_not_payable(destination, NOW) is NotPayable.SAFETY_WAIT
    assert why_not_payable(destination, just_before) is NotPayable.SAFETY_WAIT
    assert why_not_payable(destination, NOW + timedelta(hours=24)) is None


def test_the_wait_does_not_depend_on_the_time_zone_of_the_clock() -> None:
    from app.shared.business_day import KAMPALA

    destination = approved(NOW)
    assert why_not_payable(destination, (NOW + SAFETY_WAIT).astimezone(KAMPALA)) is None


def test_a_restricted_session_pays_only_destinations_already_paid_before_it() -> None:
    ready = NOW + timedelta(days=10)
    restricted_since = ready - timedelta(hours=1)
    never_paid = approved(NOW)
    paid_before = record_first_payout(never_paid, NOW + timedelta(days=2))
    paid_during = record_first_payout(never_paid, restricted_since)

    assert why_not_payable(never_paid, ready) is None
    assert (
        why_not_payable(never_paid, ready, restricted_since=restricted_since)
        is NotPayable.SESSION_RESTRICTED
    )
    assert (
        why_not_payable(paid_during, ready, restricted_since=restricted_since)
        is NotPayable.SESSION_RESTRICTED
    )
    assert why_not_payable(paid_before, ready, restricted_since=restricted_since) is None


def test_the_safety_wait_is_reported_before_the_session_restriction() -> None:
    destination = approved(NOW)
    assert (
        why_not_payable(destination, NOW + timedelta(hours=1), restricted_since=NOW)
        is NotPayable.SAFETY_WAIT
    )


def test_the_first_payout_is_recorded_once() -> None:
    first = NOW + timedelta(days=2)
    destination = record_first_payout(approved(NOW), first)
    assert record_first_payout(destination, first + timedelta(days=5)).first_paid_at == first
    with pytest.raises(DestinationError):
        record_first_payout(in_review(), first)
