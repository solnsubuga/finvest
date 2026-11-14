"""Looking up the name a network holds for a payout number: the rules, the shared ioTec Verify
transport and the phone lookup adapter, against a fake ioTec."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.infrastructure.iotec_verify import IotecVerifyClient, IotecVerifySettings, Sent
from app.modules.payouts.domain.destinations import NameMatch, RejectionReason
from app.modules.payouts.domain.name_lookups import (
    MAX_LOOKUPS,
    STALE_AFTER,
    Accepted,
    CheckAgain,
    Found,
    LookupDeferred,
    LookupStatus,
    NameCheck,
    NoAnswer,
    NoName,
    NotReceived,
    Pending,
    Refuse,
    ToStaff,
    Unanswerable,
    decide,
    name_check,
    why_not_again,
)
from app.modules.payouts.providers.iotec_names import IotecRegisteredNames
from app.modules.payouts.wiring import PayoutNameSettings

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
ID_NAME = "NAKATO Sarah Amara"
REQUEST_ID = "5e83b187-801e-410e-b76e-f491928547e0"
KEY = "IO-not-a-real-key"


# --- What an answer does ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("registered", "expected"),
    [
        ("NAKATO SARAH AMARA", ToStaff(NameMatch.EXACT)),
        ("Sarah Amara Nakato", ToStaff(NameMatch.EXACT)),
        ("NAKATO SARAH", ToStaff(NameMatch.PARTIAL)),  # a name left out: staff decide
        ("NAKATE SARAH AMARA", Refuse(RejectionReason.NAME_DIFFERENT)),  # spelt differently
        ("OKELLO PETER", Refuse(RejectionReason.NAME_DIFFERENT)),
        ("NAKATO", Refuse(RejectionReason.NAME_DIFFERENT)),  # one name is never enough
    ],
)
def test_a_name_from_the_network_is_compared_with_the_id(
    registered: str, expected: ToStaff | Refuse
) -> None:
    assert decide(ID_NAME, Found(registered)) == expected


def test_a_number_with_no_name_is_refused() -> None:
    assert decide(ID_NAME, NoName()) == Refuse(RejectionReason.NETWORK_NAME_UNAVAILABLE)


def test_a_lookup_is_asked_again_only_once_the_last_got_no_answer() -> None:
    fresh = NOW - timedelta(minutes=5)
    stale = NOW - STALE_AFTER
    assert why_not_again(None, None, 0, NOW) is None
    assert why_not_again(LookupStatus.QUEUED, fresh, 1, NOW) is CheckAgain.IN_PROGRESS
    assert why_not_again(LookupStatus.SENT, fresh, 1, NOW) is CheckAgain.IN_PROGRESS
    # A job that stopped leaves its lookup unanswered for good.
    assert why_not_again(LookupStatus.QUEUED, stale, 1, NOW) is None
    assert why_not_again(LookupStatus.SENT, stale, 1, NOW) is None
    assert why_not_again(LookupStatus.UNANSWERED, fresh, 1, NOW) is None
    # An answer is never asked for again.
    assert why_not_again(LookupStatus.FOUND, stale, 1, NOW) is CheckAgain.NOT_WAITING
    assert why_not_again(LookupStatus.NO_NAME, stale, 1, NOW) is CheckAgain.NOT_WAITING
    # Each costs a fee, so there is a limit.
    assert why_not_again(LookupStatus.UNANSWERED, fresh, MAX_LOOKUPS, NOW) is CheckAgain.TOO_MANY


def test_the_client_sees_where_the_lookup_stands() -> None:
    fresh = NOW - timedelta(minutes=5)
    assert name_check(None, None, 0, NOW) is NameCheck.NOT_ANSWERED
    assert name_check(LookupStatus.SENT, fresh, 1, NOW) is NameCheck.CHECKING
    assert name_check(LookupStatus.UNANSWERED, fresh, 1, NOW) is NameCheck.NOT_ANSWERED
    assert name_check(LookupStatus.UNANSWERED, fresh, MAX_LOOKUPS, NOW) is NameCheck.LIMIT_REACHED
    assert name_check(LookupStatus.FOUND, fresh, 1, NOW) is NameCheck.WAITING_FOR_ID
    # Asked again once the ID was approved: the comparison is under way.
    assert name_check(LookupStatus.FOUND, fresh, 1, NOW, deciding=True) is NameCheck.CHECKING


# --- A fake ioTec -------------------------------------------------------------------------


Handler = Callable[[httpx.Request], httpx.Response]


def fake(handler: Handler, *, sent: list[httpx.Request] | None = None) -> IotecRegisteredNames:
    def record(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.append(request)
        return handler(request)

    client = IotecVerifyClient(
        client_id="test-client",
        api_key=SecretStr(KEY),
        http=httpx.AsyncClient(transport=httpx.MockTransport(record)),
        retry_pause=0,
    )
    return IotecRegisteredNames(client)


def answering(status: int, body: object = None) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handler


def raising(error: type[httpx.TransportError]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error("failed", request=request)

    return handler


async def test_a_lookup_is_sent_in_the_background_with_our_reference() -> None:
    sent: list[httpx.Request] = []
    names = fake(answering(200, {"id": REQUEST_ID.upper(), "status": "Pending"}), sent=sent)
    assert await names.send("256772123456", "-Nlookup00000000001") == Accepted(REQUEST_ID)
    (request,) = sent
    assert request.method == "POST"
    assert str(request.url) == "https://verify-api.iotec.io/api/verify-phone-number"
    assert request.headers["Client-Id"] == "test-client"
    assert request.headers["X-Api-Key"] == KEY
    assert json.loads(request.content) == {
        "phoneNumber": "0772123456",
        "referenceId": "-Nlookup00000000001",
        "isBlocking": False,
    }


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        (answering(401), NotReceived()),
        (answering(403), NotReceived()),
        (answering(429), NotReceived()),
        (raising(httpx.ConnectError), NotReceived()),
        (raising(httpx.ConnectTimeout), NotReceived()),
        (answering(400, {"statusCode": 400}), Unanswerable("iotec_refused_400")),
        (answering(500), Unanswerable("iotec_uncertain")),
        (answering(408), Unanswerable("iotec_uncertain")),
        (raising(httpx.ReadTimeout), Unanswerable("iotec_uncertain")),
        (raising(httpx.RemoteProtocolError), Unanswerable("iotec_uncertain")),
        # Accepted with nothing to read it back by: it may be held, so never sent again.
        (answering(200, {"status": "Pending"}), Unanswerable("iotec_uncertain")),
        (answering(200, {"id": "not-a-request-id"}), Unanswerable("iotec_uncertain")),
    ],
)
async def test_a_send_is_sorted_by_whether_it_may_have_arrived(
    handler: Handler, expected: object
) -> None:
    assert await fake(handler).send("256772123456", "-Nlookup00000000001") == expected


async def test_a_send_that_never_arrived_is_tried_once_more_at_once() -> None:
    sent: list[httpx.Request] = []
    replies = iter([httpx.Response(429), httpx.Response(200, json={"id": REQUEST_ID})])
    names = fake(lambda request: next(replies), sent=sent)
    assert await names.send("256772123456", "-Nlookup00000000001") == Accepted(REQUEST_ID)
    assert len(sent) == 2


async def test_a_send_that_may_have_arrived_is_never_tried_again() -> None:
    sent: list[httpx.Request] = []
    await fake(raising(httpx.ReadTimeout), sent=sent).send("256772123456", "-Nlookup1")
    assert len(sent) == 1


def reading(**answer: object) -> Handler:
    return answering(200, {"id": REQUEST_ID, **answer})


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"status": "Pending"}, Pending()),
        ({"status": "Processing"}, Pending()),
        (
            {
                "status": "Completed",
                "verificationStatus": "Success",
                "registeredName": " JOHN DOE ",
            },
            Found("JOHN DOE"),
        ),
        ({"status": "Completed", "verificationStatus": "NotFound"}, NoName()),
        ({"status": "Completed", "verificationStatus": "Barred"}, NoName()),
        # Never a name from an answer that is not clearly one.
        ({"status": "Completed", "verificationStatus": "Success"}, NoAnswer("iotec_no_result")),
        (
            {"status": "Completed", "verificationStatus": "Success", "registeredName": "  "},
            NoAnswer("iotec_no_result"),
        ),
        (
            {"status": "Completed", "verificationStatus": "Success", "registeredName": 7},
            NoAnswer("iotec_no_result"),
        ),
        ({"status": "Completed", "verificationStatus": "Failed"}, NoAnswer("iotec_no_result")),
        ({"status": "Completed", "verificationStatus": "Pending"}, NoAnswer("iotec_no_result")),
        ({"status": "Completed", "verificationStatus": "Unheard"}, NoAnswer("iotec_no_result")),
        ({"status": "Completed"}, NoAnswer("iotec_no_result")),
        (
            {"status": "Failed", "verificationStatus": "Success", "registeredName": "JOHN DOE"},
            NoAnswer("iotec_request_ended"),
        ),
        ({"status": "Expired"}, NoAnswer("iotec_request_ended")),
        ({"status": "Cancelled"}, NoAnswer("iotec_request_ended")),
        ({}, NoAnswer("iotec_request_ended")),
    ],
)
async def test_an_answer_is_read_as_a_name_only_when_it_clearly_is_one(
    answer: dict[str, object], expected: object
) -> None:
    sent: list[httpx.Request] = []
    assert await fake(reading(**answer), sent=sent).read(REQUEST_ID) == expected
    assert str(sent[0].url) == f"https://verify-api.iotec.io/api/verify-phone-number/{REQUEST_ID}"


@pytest.mark.parametrize(
    "handler",
    [
        answering(500),
        answering(404),
        answering(200, ["not", "an", "object"]),
        raising(httpx.ConnectError),
        raising(httpx.ReadTimeout),
    ],
)
async def test_a_read_that_cannot_be_answered_is_tried_again_later(handler: Handler) -> None:
    with pytest.raises(LookupDeferred) as deferred:
        await fake(handler).read(REQUEST_ID)
    # Never what ioTec sent: its answers name the client.
    assert "JOHN" not in str(deferred.value)


async def test_an_answer_never_reaches_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="JOHN DOE 0772123456")

    with pytest.raises(LookupDeferred) as deferred:
        await fake(handler).read(REQUEST_ID)
    assert "JOHN" not in str(deferred.value) and "0772" not in str(deferred.value)
    assert deferred.value.__cause__ is None


async def test_only_an_ioTec_request_id_is_read() -> None:
    with pytest.raises(ValueError):
        await fake(reading(status="Pending")).read("../../admin")


# --- Settings -----------------------------------------------------------------------------


def test_the_key_never_shows_in_a_repr_or_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = IotecVerifyClient(client_id="test-client", api_key=SecretStr(KEY))
    assert KEY not in repr(client)
    monkeypatch.setenv("FINVEST_IOTEC_VERIFY_API_KEY", KEY)
    monkeypatch.setenv("FINVEST_IOTEC_VERIFY_BASE_URL", "http://verify-api.iotec.io")
    with pytest.raises(ValidationError) as refused:
        IotecVerifySettings()
    assert KEY not in str(refused.value)
    assert "http://verify-api" not in str(refused.value)


def test_ioTec_verify_is_reached_over_https_only() -> None:
    with pytest.raises(ValueError):
        IotecVerifyClient(
            client_id="test-client", api_key=SecretStr(KEY), base_url="http://verify-api.iotec.io"
        )
    with pytest.raises(ValueError):
        IotecVerifyClient(client_id="", api_key=SecretStr(KEY))


def test_no_lookups_until_a_provider_is_chosen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINVEST_PAYOUT_NAME_PROVIDER", raising=False)
    assert PayoutNameSettings().provider() is None


def test_ioTec_lookups_need_the_verify_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINVEST_PAYOUT_NAME_PROVIDER", "iotec")
    monkeypatch.delenv("FINVEST_IOTEC_VERIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("FINVEST_IOTEC_VERIFY_API_KEY", raising=False)
    with pytest.raises(ValueError) as missing:
        PayoutNameSettings().provider()
    assert "FINVEST_IOTEC_VERIFY_API_KEY" in str(missing.value)
    monkeypatch.setenv("FINVEST_IOTEC_VERIFY_CLIENT_ID", "test-client")
    monkeypatch.setenv("FINVEST_IOTEC_VERIFY_API_KEY", KEY)
    provider = PayoutNameSettings().provider()
    assert isinstance(provider, IotecRegisteredNames)


def test_a_send_result_is_one_of_four() -> None:
    assert {sent.value for sent in Sent} == {"accepted", "refused", "not_received", "uncertain"}
