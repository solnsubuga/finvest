from datetime import UTC, date, datetime

import pytest

from app.modules.audit.domain.events import (
    MAX_DETAIL_KEYS,
    MAX_LIST_ITEMS,
    MAX_REASON_LENGTH,
    MAX_STRING_LENGTH,
    REDACTED,
    AuditActor,
    AuditEvent,
    UnsafeAuditEvent,
    build_record,
    redact_details,
    redact_text,
    sensitive_key,
)

STAFF = "-Nstaff0000000000001"
CLIENT = "-Nclient000000000001"
RECORD = "-Nrecord000000000001"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def event(**changes: object) -> AuditEvent:
    values: dict[str, object] = {
        "category": "change",
        "action": "rates.rate_changed",
        "resource_type": "product",
        "actor": AuditActor.staff(STAFF),
    }
    values.update(changes)
    return AuditEvent(**values)  # type: ignore[arg-type]


def test_people_are_identified_and_others_are_not() -> None:
    assert AuditActor.client(CLIENT).id == CLIENT
    assert AuditActor.staff(STAFF).kind == "staff"
    for actor in (AuditActor.system(), AuditActor.provider(), AuditActor.anonymous()):
        assert actor.id is None
    for kind, actor_id in (
        ("client", None),
        ("staff", None),
        ("system", STAFF),
        ("provider", STAFF),
        ("anonymous", CLIENT),
        ("robot", None),
        ("staff", "+256700000000"),
        ("client", "jane@example.com"),
    ):
        with pytest.raises(UnsafeAuditEvent):
            AuditActor(kind, actor_id)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"category": "gossip"},
        {"outcome": "maybe"},
        {"action": "ratechanged"},
        {"action": "Rates.Changed"},
        {"action": "rates." + "x" * 100},
        {"resource_type": "Product"},
        {"resource_id": "jane@example.com"},
        {"resource_id": "+256 700 123456"},
        {"resource_id": "0700123456"},
        {"resource_id": "has space"},
        {"correlation_id": "has space"},
        {"reason": "   "},
        {"actor": "staff"},
        {"details": ["not", "a", "mapping"]},
        {"category": "approval"},
        {"category": "operator_control"},
    ],
)
def test_unsafe_structure_is_refused(changes: dict[str, object]) -> None:
    with pytest.raises(UnsafeAuditEvent):
        event(**changes)


def test_decisions_need_a_reason() -> None:
    approved = event(category="approval", action="rates.change_approved", reason="Matches memo 12")
    assert build_record(approved, record_id=RECORD, occurred_at=NOW).reason == "Matches memo 12"
    stopped = event(
        category="operator_control", action="jobs.job_requeued", reason="  Provider was down  "
    )
    assert build_record(stopped, record_id=RECORD, occurred_at=NOW).reason == "Provider was down"


def test_a_record_needs_an_aware_time_and_a_push_id() -> None:
    with pytest.raises(UnsafeAuditEvent):
        build_record(event(), record_id=RECORD, occurred_at=datetime(2026, 11, 13))
    with pytest.raises(UnsafeAuditEvent):
        build_record(event(), record_id="1", occurred_at=NOW)


def test_reasons_are_redacted_and_bounded() -> None:
    typed = "Client called from 0700 123 456 about jane@example.com. " + "x" * 600
    reason = build_record(event(reason=typed), record_id=RECORD, occurred_at=NOW).reason
    assert reason is not None
    assert "0700" not in reason and "jane@" not in reason
    assert "[redacted-number]" in reason and "[redacted-email]" in reason
    assert len(reason) == MAX_REASON_LENGTH


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "new_password",
        "otp",
        "sms_code",
        "error_code",
        "pin",
        "phone",
        "mobile_number",
        "email",
        "api_key",
        "apikey",
        "client_secret",
        "session_token",
        "account_number",
        "payout_account_number",
        "nin",
        "full_name",
        "date_of_birth",
        "passkey",
    ],
)
def test_sensitive_keys_are_masked(key: str) -> None:
    assert sensitive_key(key)
    assert redact_details({key: "anything"}) == {key: REDACTED}


@pytest.mark.parametrize("key", ["pinned_version", "old_rate_bps", "changed_fields", "status"])
def test_ordinary_keys_are_kept(key: str) -> None:
    assert not sensitive_key(key)


def test_values_are_scrubbed_inside_strings() -> None:
    assert redact_text("call +256 700 123456 now") == "call [redacted-number] now"
    assert redact_text("account 1001004827") == "account [redacted-number]"
    assert redact_text("write to jane.doe@example.com") == "write to [redacted-email]"
    assert redact_text("Bearer abcdefgh12345678") == "[redacted-credential]"
    assert redact_text("password=hunter2 next") == "[redacted-credential] next"
    assert "s3cret" not in redact_text("postgres://user:s3cret@db/finvest")


def test_dates_ids_and_short_numbers_survive() -> None:
    assert redact_text("effective 2026-12-01") == "effective 2026-12-01"
    assert redact_text("at 2026-11-13T12:00:00Z") == "at 2026-11-13T12:00:00Z"
    assert redact_text(f"holding {RECORD}") == f"holding {RECORD}"
    assert redact_text("91 days at 1250 bps") == "91 days at 1250 bps"


def test_integers_are_kept_so_a_changed_limit_can_be_read() -> None:
    details = redact_details({"old_limit_minor": 5_000_000, "new_limit_minor": 10_000_000})
    assert details == {"old_limit_minor": 5_000_000, "new_limit_minor": 10_000_000}
    assert redact_details({"huge": 2**70}) == {"huge": REDACTED}


def test_floats_and_unknown_objects_are_never_stored() -> None:
    assert redact_details({"rate": 12.5, "thing": object(), "raw": b"bytes"}) == {
        "rate": REDACTED,
        "thing": REDACTED,
        "raw": REDACTED,
    }


def test_details_are_json_values_with_bounded_shape() -> None:
    details = redact_details(
        {
            "approved": True,
            "nothing": None,
            "effective_date": date(2026, 12, 1),
            "decided_at": NOW,
            "changed_fields": ("rate_bps", "minimum_minor"),
            "tags": {"b", "a"},
            "nested": {"level": {"deeper": {"too": "deep"}}},
            "long": "x" * 1_000,
            "many": list(range(100)),
        }
    )
    assert details["approved"] is True and details["nothing"] is None
    assert details["effective_date"] == "2026-12-01"
    assert details["decided_at"] == "2026-11-13T12:00:00+00:00"
    assert details["changed_fields"] == ["rate_bps", "minimum_minor"]
    assert details["tags"] == ["a", "b"]
    assert details["nested"] == {"level": {"deeper": REDACTED}}
    assert isinstance(details["long"], str) and len(details["long"]) == MAX_STRING_LENGTH
    assert isinstance(details["many"], list) and len(details["many"]) == MAX_LIST_ITEMS


def test_unsafe_and_surplus_keys_are_dropped() -> None:
    details = redact_details({"Jane Doe": 1, "ok": 2})
    assert details == {"dropped_keys": True, "ok": 2}
    many = redact_details({f"key_{index}": index for index in range(MAX_DETAIL_KEYS + 5)})
    assert many["truncated"] is True
    assert len(many) == MAX_DETAIL_KEYS + 1


def test_sensitive_keys_are_masked_at_every_depth() -> None:
    details = redact_details({"before": {"phone": "0700123456", "status": "active"}})
    assert details == {"before": {"phone": REDACTED, "status": "active"}}
