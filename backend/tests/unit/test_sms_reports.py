"""Reading Africa's Talking's delivery reports, and the token in their address."""

import pytest
from pydantic import SecretStr, ValidationError

from app.modules.notifications.domain.sms_reports import (
    UnreadableReport,
    moves_forward,
    read_report,
)
from app.modules.notifications.settings import SmsCallbackSettings

FORM = "application/x-www-form-urlencoded"
MESSAGE_ID = "ATXid_0f1e2d3c4b5a69788796a5b4c3d2e1f0"


def form(**fields: str) -> bytes:
    from urllib.parse import urlencode

    return urlencode(fields).encode()


def test_a_delivered_report_keeps_the_id_and_drops_the_phone_number() -> None:
    body = form(
        id=MESSAGE_ID,
        status="Success",
        phoneNumber="+256772123456",
        networkCode="64110",
        retryCount="0",
    )
    report = read_report(body, FORM)
    assert report is not None
    assert (report.provider_message_id, report.status, report.delivery, report.reason) == (
        MESSAGE_ID,
        "Success",
        "delivered",
        None,
    )
    assert "+256772123456" not in repr(report)


@pytest.mark.parametrize(
    ("status", "delivery"),
    [
        ("Sent", "accepted"),
        ("Submitted", "accepted"),
        ("Buffered", "delayed"),
        ("Success", "delivered"),
        ("Failed", "failed"),
        ("Rejected", "failed"),
        ("Expired", "failed"),
    ],
)
def test_each_status_means_a_delivery_state(status: str, delivery: str) -> None:
    report = read_report(form(id=MESSAGE_ID, status=status), FORM)
    assert report is not None and report.delivery == delivery


def test_a_failure_keeps_africas_talking_reason() -> None:
    report = read_report(
        form(id=MESSAGE_ID, status="Failed", failureReason="UserInBlacklist"), FORM
    )
    assert report is not None and report.reason == "UserInBlacklist"


def test_a_reason_of_another_shape_is_not_stored() -> None:
    report = read_report(
        form(id=MESSAGE_ID, status="Failed", failureReason="call +256772123456"), FORM
    )
    assert report is not None and report.reason == "Unknown"


def test_a_delivered_report_never_carries_a_reason() -> None:
    report = read_report(
        form(id=MESSAGE_ID, status="Success", failureReason="DeliveryFailure"), FORM
    )
    assert report is not None and report.reason is None


def test_a_status_we_do_not_act_on_is_dropped() -> None:
    assert read_report(form(id=MESSAGE_ID, status="SomethingNew"), FORM) is None


def test_a_json_report_is_read_too() -> None:
    report = read_report(
        b'{"id": "ATXid_1", "status": "Failed", "failureReason": "DeliveryFailure"}',
        "application/json; charset=utf-8",
    )
    assert report is not None and (report.delivery, report.reason) == ("failed", "DeliveryFailure")


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (form(status="Success"), FORM),
        (form(id="ATXid 1; drop", status="Success"), FORM),
        (form(id=MESSAGE_ID), FORM),
        (b"id=ATXid_1&id=ATXid_2&status=Success", FORM),
        (b"not json", "application/json"),
        (b"[1]", "application/json"),
        (b"\xff\xfe", FORM),
    ],
)
def test_a_report_that_cannot_be_read_is_refused(body: bytes, content_type: str) -> None:
    with pytest.raises(UnreadableReport):
        read_report(body, content_type)


def test_a_failure_can_still_turn_into_a_delivery_but_not_back() -> None:
    assert moves_forward(None, "accepted")
    assert moves_forward("accepted", "delayed")
    assert moves_forward("delayed", "failed")
    assert moves_forward("failed", "delivered")
    assert not moves_forward("delivered", "failed")
    assert not moves_forward("delivered", "accepted")
    assert not moves_forward("failed", "failed")


def test_the_callback_token_is_off_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINVEST_SMS_AFRICASTALKING_CALLBACK_TOKEN", raising=False)
    assert SmsCallbackSettings().callback_token is None
    monkeypatch.setenv("FINVEST_SMS_AFRICASTALKING_CALLBACK_TOKEN", "")
    assert SmsCallbackSettings().callback_token is None


@pytest.mark.parametrize("token", ["short", "x" * 129, "a" * 31 + "/", "a" * 40 + "?x=1"])
def test_a_callback_token_must_be_long_and_plain(token: str) -> None:
    with pytest.raises(ValidationError, match="32 to 128"):
        SmsCallbackSettings(callback_token=SecretStr(token))


def test_a_good_callback_token_is_kept_secret() -> None:
    token = "Ab3_" + "x" * 40
    settings = SmsCallbackSettings(callback_token=SecretStr(token))
    assert token not in repr(settings)
