"""The sandbox provider behaves like a collections provider and keeps no state."""

import json
from datetime import timedelta

import pytest

from app.modules.payments.domain.deposits import Network
from app.modules.payments.domain.outcomes import (
    Accepted,
    Confirmed,
    Deferred,
    NotFound,
    Pending,
    Rejected,
    RejectionCode,
    Uncertain,
)
from app.modules.payments.domain.provider import CollectionRequest, InvalidNotice
from app.modules.payments.providers.sandbox import (
    PROMPT_TIME,
    SIGNATURE_HEADER,
    SandboxCollections,
)
from app.modules.payments.settings import PaymentsSettings
from app.modules.payments.wiring import collections_provider
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock

AMOUNT = Money(50_000, Currency.UGX)


def request(ending: str) -> CollectionRequest:
    return CollectionRequest(push_id(), Network.MTN, f"+2567721234{ending}", AMOUNT)


async def settle(provider: SandboxCollections, clock: FakeClock, ending: str) -> object:
    asked = request(ending)
    accepted = await provider.request_collection(asked)
    assert isinstance(accepted, Accepted)
    reference = accepted.provider_reference
    pending = await provider.collection_status(
        request_id=asked.request_id, provider_reference=reference
    )
    assert pending == Pending(reference)
    clock.advance(seconds=PROMPT_TIME.total_seconds())
    # A second instance, as in another process, gives the same answer.
    other = SandboxCollections(clock)
    return await other.collection_status(request_id=asked.request_id, provider_reference=reference)


async def test_most_numbers_approve_after_the_prompt(fake_clock: FakeClock) -> None:
    result = await settle(SandboxCollections(fake_clock), fake_clock, "56")
    assert isinstance(result, Confirmed)
    assert result.amount == AMOUNT


@pytest.mark.parametrize(
    ("ending", "code"),
    [
        ("01", RejectionCode.DECLINED),
        ("02", RejectionCode.INSUFFICIENT_FUNDS),
        ("03", RejectionCode.APPROVAL_TIMED_OUT),
    ],
)
async def test_some_numbers_are_refused(
    fake_clock: FakeClock, ending: str, code: RejectionCode
) -> None:
    result = await settle(SandboxCollections(fake_clock), fake_clock, ending)
    assert isinstance(result, Rejected)
    assert result.code is code


async def test_one_number_confirms_a_different_amount(fake_clock: FakeClock) -> None:
    result = await settle(SandboxCollections(fake_clock), fake_clock, "06")
    assert isinstance(result, Confirmed)
    assert result.amount != AMOUNT


async def test_one_number_gives_no_answer_and_leaves_no_record(fake_clock: FakeClock) -> None:
    provider = SandboxCollections(fake_clock)
    asked = request("04")
    assert isinstance(await provider.request_collection(asked), Uncertain)
    found = await provider.collection_status(request_id=asked.request_id, provider_reference=None)
    assert found == NotFound()


async def test_one_number_is_deferred(fake_clock: FakeClock) -> None:
    assert isinstance(
        await SandboxCollections(fake_clock).request_collection(request("05")), Deferred
    )


async def test_a_reference_for_another_request_is_not_found(fake_clock: FakeClock) -> None:
    provider = SandboxCollections(fake_clock)
    accepted = await provider.request_collection(request("56"))
    assert isinstance(accepted, Accepted)
    fake_clock.advance(minutes=1)
    for request_id, reference in [(push_id(), accepted.provider_reference), (push_id(), "junk")]:
        found = await provider.collection_status(
            request_id=request_id, provider_reference=reference
        )
        assert found == NotFound()


def test_a_callback_is_read_only_with_a_good_signature(fake_clock: FakeClock) -> None:
    provider = SandboxCollections(fake_clock, notice_secret="test-notice-secret")
    body = json.dumps({"id": "n-1", "request_id": "r-1", "reference": "sbx:x"}).encode()
    notice = provider.read_notice({SIGNATURE_HEADER.title(): provider.sign(body)}, body)
    assert (notice.notice_id, notice.request_id, notice.provider_reference) == (
        "n-1",
        "r-1",
        "sbx:x",
    )
    for headers, payload in [
        ({}, body),
        ({SIGNATURE_HEADER: "0" * 64}, body),
        ({SIGNATURE_HEADER: provider.sign(body)}, body + b" "),
        ({SIGNATURE_HEADER: provider.sign(b"[]")}, b"[]"),
        ({SIGNATURE_HEADER: provider.sign(b"{}")}, b"{}"),
        ({SIGNATURE_HEADER: provider.sign(b'{"id": 5}')}, b'{"id": 5}'),
        ({SIGNATURE_HEADER: provider.sign(b"not json")}, b"not json"),
    ]:
        with pytest.raises(InvalidNotice):
            provider.read_notice(headers, payload)


def test_callbacks_are_refused_when_no_secret_is_set(fake_clock: FakeClock) -> None:
    with pytest.raises(InvalidNotice):
        SandboxCollections(fake_clock).read_notice({SIGNATURE_HEADER: ""}, b"{}")


def test_payments_are_disabled_unless_a_provider_is_chosen(
    fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FINVEST_PAYMENTS_PROVIDER", raising=False)
    assert collections_provider(PaymentsSettings(), fake_clock) is None
    monkeypatch.setenv("FINVEST_PAYMENTS_PROVIDER", "sandbox")
    chosen = collections_provider(PaymentsSettings(), fake_clock)
    assert chosen is not None and chosen.name == "sandbox"
    assert timedelta(0) < PROMPT_TIME
