"""Africa's Talking first, ioTec only when Africa's Talking certainly did not take the message."""

import pytest

from app.infrastructure.sms import (
    SmsDeferred,
    SmsFailure,
    SmsMessage,
    SmsRejected,
    SmsResult,
    SmsUncertain,
)
from app.infrastructure.sms.fallback import FallbackSms

MESSAGE = SmsMessage(to="+256772123456", text="Your Finvest code is 123456", idempotency_key="k-1")


class Fake:
    def __init__(self, name: str, failure: SmsFailure | None = None) -> None:
        self.name = name
        self.failure = failure
        self.sent: list[SmsMessage] = []

    async def send(self, message: SmsMessage) -> SmsResult:
        self.sent.append(message)
        if self.failure is not None:
            raise self.failure
        return SmsResult(provider=self.name, provider_message_id=f"{self.name}-1")


def pair(
    first: SmsFailure | None = None, second: SmsFailure | None = None
) -> tuple[FallbackSms, Fake, Fake]:
    primary, secondary = Fake("africastalking", first), Fake("iotec", second)
    return FallbackSms(primary, secondary), primary, secondary


async def test_the_first_provider_sends_when_it_can() -> None:
    sms, primary, secondary = pair()
    assert (await sms.send(MESSAGE)).provider == "africastalking"
    assert (len(primary.sent), len(secondary.sent)) == (1, 0)


@pytest.mark.parametrize(
    "failure",
    [
        SmsDeferred("down"),
        SmsDeferred("not configured", code="sms_not_configured"),
        SmsRejected("refused credentials", code="sms_auth_failed"),
        SmsRejected("no balance", code="sms_provider_balance"),
    ],
)
async def test_the_fallback_sends_when_the_first_did_not_take_it(failure: SmsFailure) -> None:
    sms, _, secondary = pair(first=failure)
    result = await sms.send(MESSAGE)
    assert (result.provider, result.provider_message_id) == ("iotec", "iotec-1")
    assert secondary.sent == [MESSAGE]


async def test_an_uncertain_first_send_is_never_sent_again_elsewhere() -> None:
    # The person may already have the message. A second copy could be a second code.
    sms, _, secondary = pair(first=SmsUncertain("lost"))
    with pytest.raises(SmsUncertain):
        await sms.send(MESSAGE)
    assert secondary.sent == []


@pytest.mark.parametrize("first", [SmsDeferred("down"), SmsRejected("refused")])
async def test_an_uncertain_fallback_stays_uncertain(first: SmsFailure) -> None:
    sms, _, _ = pair(first=first, second=SmsUncertain("lost"))
    with pytest.raises(SmsUncertain):
        await sms.send(MESSAGE)


@pytest.mark.parametrize("first", [SmsDeferred("down"), SmsRejected("refused")])
async def test_a_fallback_that_is_down_too_means_try_again_later(first: SmsFailure) -> None:
    sms, _, _ = pair(first=first, second=SmsDeferred("also down"))
    with pytest.raises(SmsDeferred):
        await sms.send(MESSAGE)


async def test_a_first_provider_that_is_down_is_tried_again_when_the_fallback_refuses() -> None:
    # ioTec reaches only Ugandan numbers. Africa's Talking being down for a moment must not
    # turn that refusal into a final failure.
    down = SmsDeferred("down")
    sms, _, _ = pair(first=down, second=SmsRejected("unsupported", code="sms_unsupported_number"))
    with pytest.raises(SmsDeferred) as failed:
        await sms.send(MESSAGE)
    assert failed.value is down


async def test_when_both_refuse_the_first_reason_is_kept() -> None:
    first = SmsRejected("blocked", code="sms_blocked_by_recipient")
    sms, _, _ = pair(first=first, second=SmsRejected("refused"))
    with pytest.raises(SmsRejected) as failed:
        await sms.send(MESSAGE)
    assert failed.value.code == "sms_blocked_by_recipient"


def test_a_provider_cannot_fall_back_to_itself() -> None:
    with pytest.raises(ValueError, match="differ"):
        FallbackSms(Fake("iotec"), Fake("iotec"))
