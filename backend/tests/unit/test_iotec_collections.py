"""The ioTec Pay collections adapter, against a fake ioTec that can answer every way.

The fake follows ioTec's published API (https://iotec.io/api-docs/pay): a client credentials
token from the identity server, `POST /api/collections/collect`, and the two status reads.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import pytest
from pydantic import SecretStr

from app.infrastructure.iotec import IotecSettings, IotecTokens, IotecUnavailable
from app.modules.payments.domain.deposits import Network
from app.modules.payments.domain.outcomes import (
    Accepted,
    Confirmed,
    Deferred,
    NotFound,
    Pending,
    Rejected,
    RejectionCode,
    Unavailable,
    Uncertain,
    UncertainReason,
)
from app.modules.payments.domain.provider import CollectionRequest, InvalidNotice
from app.modules.payments.providers.iotec import IotecCollections
from app.modules.payments.settings import Deployment, PaymentsSettings, SandboxNotAllowed
from app.modules.payments.wiring import collections_provider, ensure_allowed
from app.shared.money import Currency, Money
from app.shared.push_ids import push_id
from tests.support.clock import FakeClock

TOKEN_URL = "https://id.example/connect/token"
PAY_URL = "https://pay.example"
WALLET = "5e83b187-801e-410e-b76e-f491928547e0"
TRANSACTION = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
AMOUNT = Money(250_000, Currency.UGX)
CALLBACK_TOKEN = "callback-token-for-tests"
DEPOSIT = push_id()

Answer = Callable[[httpx.Request], httpx.Response]


def settings(**changes: object) -> IotecSettings:
    values: dict[str, object] = {
        "client_id": "client",
        "client_secret": SecretStr("client-secret-for-tests"),
        "wallet_id": WALLET,
        "token_url": TOKEN_URL,
        "pay_url": PAY_URL,
        "callback_token": SecretStr(CALLBACK_TOKEN),
    }
    values.update(changes)
    return IotecSettings.model_validate(values)


def transaction(status: str, **changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": TRANSACTION,
        "status": status,
        "statusCode": status.lower(),
        "externalId": DEPOSIT,
        "walletId": WALLET,
        "amount": AMOUNT.minor,
        "currency": "UGX",
    }
    body.update(changes)
    return body


@dataclass
class FakeIotec:
    """Answers the token request itself, and every pay request with `answer`."""

    answer: Answer
    token_lifetime: int = 300
    token_status: int = 200
    tokens_issued: int = 0
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            if self.token_status != 200:
                return httpx.Response(self.token_status)
            self.tokens_issued += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"token-{self.tokens_issued}",
                    "expires_in": self.token_lifetime,
                    "token_type": "Bearer",
                },
            )
        self.requests.append(request)
        return self.answer(request)


def adapter(fake: FakeIotec, clock: FakeClock | None = None, **changes: object) -> IotecCollections:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return IotecCollections(settings(**changes), http, clock or FakeClock())


def ask(deposit_id: str | None = None, amount: Money = AMOUNT) -> CollectionRequest:
    return CollectionRequest(deposit_id or push_id(), Network.MTN, "+256772123456", amount)


def answering(status: int, body: object = None, **headers: str) -> Answer:
    def answer(_: httpx.Request) -> httpx.Response:
        if body is None:
            return httpx.Response(status, headers=headers)
        return httpx.Response(status, json=body, headers=headers)

    return answer


def raising(error: type[httpx.TransportError]) -> Answer:
    def answer(request: httpx.Request) -> httpx.Response:
        raise error("simulated", request=request)

    return answer


# Sending ------------------------------------------------------------------------------------


async def test_a_collection_asks_for_exactly_the_amount_from_the_payer_without_the_plus() -> None:
    fake = FakeIotec(answering(200, transaction("Pending")))
    deposit_id = push_id()
    result = await adapter(fake).request_collection(ask(deposit_id))
    assert result == Accepted(TRANSACTION)
    [sent] = fake.requests
    assert sent.method == "POST" and str(sent.url) == f"{PAY_URL}/api/collections/collect"
    assert sent.headers["authorization"] == "Bearer token-1"
    assert json.loads(sent.content) == {
        "category": "MobileMoney",
        "currency": "UGX",
        "walletId": WALLET,
        "externalId": deposit_id,
        "payer": "256772123456",
        "amount": 250_000,
        "payerNote": "Finvest investment",
        "payeeNote": deposit_id,
        "transactionChargesCategory": "ChargeWallet",
    }


async def test_a_test_wallet_is_sent_the_test_currency() -> None:
    fake = FakeIotec(answering(200, transaction("Pending")))
    await adapter(fake, currency_code="ITX").request_collection(ask())
    assert json.loads(fake.requests[0].content)["currency"] == "ITX"


@pytest.mark.parametrize("status", ["Pending", "SentToVendor", "AwaitingApproval", "Success"])
async def test_a_collection_ioTec_took_is_accepted_and_its_status_read_later(status: str) -> None:
    fake = FakeIotec(answering(200, transaction(status)))
    assert await adapter(fake).request_collection(ask()) == Accepted(TRANSACTION)


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        ("failed", RejectionCode.OTHER),
        ("insufficient_funds", RejectionCode.INSUFFICIENT_FUNDS),
        ("request_timeout", RejectionCode.APPROVAL_TIMED_OUT),
        ("declined_by_user", RejectionCode.DECLINED),
        (None, RejectionCode.OTHER),
    ],
)
async def test_a_collection_ioTec_refused_at_once_is_rejected_with_its_reference(
    status_code: str | None, expected: RejectionCode
) -> None:
    fake = FakeIotec(answering(200, transaction("Failed", statusCode=status_code)))
    assert await adapter(fake).request_collection(ask()) == Rejected(expected, TRANSACTION)


@pytest.mark.parametrize("status", [400, 404])
async def test_a_request_ioTec_refused_is_rejected(status: int) -> None:
    fake = FakeIotec(answering(status, {"code": "BadRequest", "message": "Invalid wallet"}))
    assert await adapter(fake).request_collection(ask()) == Rejected(RejectionCode.OTHER)


async def test_busy_is_deferred_with_ioTecs_hint() -> None:
    fake = FakeIotec(answering(429, None, **{"Retry-After": "20"}))
    assert await adapter(fake).request_collection(ask()) == Deferred(retry_after_seconds=20)


async def test_a_refused_token_is_dropped_and_the_request_sent_once_more_with_a_new_one() -> None:
    answers = iter([httpx.Response(401), httpx.Response(200, json=transaction("Pending"))])
    fake = FakeIotec(lambda _: next(answers))
    assert await adapter(fake).request_collection(ask()) == Accepted(TRANSACTION)
    assert [request.headers["authorization"] for request in fake.requests] == [
        "Bearer token-1",
        "Bearer token-2",
    ]


async def test_a_token_refused_twice_defers_the_request() -> None:
    fake = FakeIotec(answering(401))
    assert await adapter(fake).request_collection(ask()) == Deferred()
    assert len(fake.requests) == 2


async def test_without_a_token_nothing_is_sent_and_the_request_is_deferred() -> None:
    fake = FakeIotec(answering(200, transaction("Pending")), token_status=500)
    assert await adapter(fake).request_collection(ask()) == Deferred()
    assert fake.requests == []


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout])
async def test_a_connection_that_never_opened_is_deferred(
    error: type[httpx.TransportError],
) -> None:
    assert await adapter(FakeIotec(raising(error))).request_collection(ask()) == Deferred()


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (httpx.ReadTimeout, UncertainReason.TIMED_OUT),
        (httpx.WriteTimeout, UncertainReason.TIMED_OUT),
        (httpx.ReadError, UncertainReason.CONNECTION_LOST),
        (httpx.RemoteProtocolError, UncertainReason.CONNECTION_LOST),
    ],
)
async def test_no_answer_after_sending_is_uncertain_and_never_sent_again(
    error: type[httpx.TransportError], reason: UncertainReason
) -> None:
    fake = FakeIotec(raising(error))
    assert await adapter(fake).request_collection(ask()) == Uncertain(reason)
    assert len(fake.requests) == 1


@pytest.mark.parametrize(
    "answer",
    [
        answering(500),
        answering(502),
        answering(200, ["not", "an", "object"]),
        answering(200, {"status": "Pending"}),
        answering(200, transaction("SomethingNew")),
        answering(200, transaction("Pending", id="not a safe/reference")),
        lambda _: httpx.Response(200, content=b"<html>"),
    ],
)
async def test_an_answer_that_cannot_be_trusted_is_uncertain(answer: Answer) -> None:
    result = await adapter(FakeIotec(answer)).request_collection(ask())
    assert result == Uncertain(UncertainReason.PROVIDER_ERROR)


@pytest.mark.parametrize("amount", [Money(1_000, Currency.USD), Money(0, Currency.UGX)])
async def test_anything_but_a_positive_shilling_amount_is_refused_without_sending(
    amount: Money,
) -> None:
    fake = FakeIotec(answering(200, transaction("Pending")))
    assert await adapter(fake).request_collection(ask(amount=amount)) == Rejected(
        RejectionCode.OTHER
    )
    assert fake.requests == []


# Reading the status -------------------------------------------------------------------------


async def test_a_successful_collection_is_confirmed_for_the_amount_ioTec_says_moved() -> None:
    fake = FakeIotec(answering(200, transaction("Success", amount=250_001)))
    reading = await adapter(fake).collection_status(
        request_id=DEPOSIT, provider_reference=TRANSACTION
    )
    assert reading == Confirmed(TRANSACTION, Money(250_001, Currency.UGX))
    assert str(fake.requests[0].url) == f"{PAY_URL}/api/collections/status/{TRANSACTION}"


async def test_a_whole_amount_written_with_a_decimal_point_is_read_exactly() -> None:
    def answer(_: httpx.Request) -> httpx.Response:
        body = json.dumps(transaction("Success")).replace('"amount": 250000', '"amount": 250000.0')
        return httpx.Response(200, content=body.encode())

    reading = await adapter(FakeIotec(answer)).collection_status(
        request_id=DEPOSIT, provider_reference=TRANSACTION
    )
    assert reading == Confirmed(TRANSACTION, AMOUNT)


async def test_without_ioTecs_id_the_status_is_found_by_the_deposit_id() -> None:
    deposit_id = push_id()
    fake = FakeIotec(answering(200, transaction("Pending", externalId=deposit_id)))
    reading = await adapter(fake).collection_status(request_id=deposit_id, provider_reference=None)
    assert reading == Pending(TRANSACTION)
    assert str(fake.requests[0].url) == f"{PAY_URL}/api/collections/external-id/{deposit_id}"


@pytest.mark.parametrize("reference", [None, TRANSACTION])
async def test_a_record_for_another_deposit_is_not_read_as_this_one(reference: str | None) -> None:
    fake = FakeIotec(answering(200, transaction("Success", externalId=push_id())))
    reading = await adapter(fake).collection_status(
        request_id=DEPOSIT, provider_reference=reference
    )
    assert reading == Unavailable()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("Pending", Pending(TRANSACTION)),
        ("SentToVendor", Pending(TRANSACTION)),
        ("Failed", Rejected(RejectionCode.OTHER, TRANSACTION)),
        ("Cancelled", Rejected(RejectionCode.DECLINED, TRANSACTION)),
        ("RolledBack", Rejected(RejectionCode.OTHER, TRANSACTION)),
    ],
)
async def test_each_ioTec_status_reads_as_ours(status: str, expected: object) -> None:
    fake = FakeIotec(answering(200, transaction(status)))
    reading = await adapter(fake).collection_status(
        request_id=DEPOSIT, provider_reference=TRANSACTION
    )
    assert reading == expected


async def test_no_record_is_not_found() -> None:
    reading = await adapter(FakeIotec(answering(404))).collection_status(
        request_id=push_id(), provider_reference=None
    )
    assert reading == NotFound()


@pytest.mark.parametrize(
    "answer",
    [
        answering(500),
        answering(400, {"code": "BadRequest"}),
        raising(httpx.ReadTimeout),
        raising(httpx.ConnectError),
        answering(200, transaction("Success", amount=250_000.5)),
        answering(200, transaction("Success", amount="250000")),
        answering(200, transaction("Success", amount=True)),
        answering(200, transaction("Success", currency="USD")),
        answering(200, transaction("Success", id="3fa85f64-0000-0000-0000-000000000000")),
        answering(200, transaction("Mystery")),
    ],
)
async def test_a_status_that_cannot_be_trusted_decides_nothing(answer: Answer) -> None:
    reading = await adapter(FakeIotec(answer)).collection_status(
        request_id=DEPOSIT, provider_reference=TRANSACTION
    )
    assert reading == Unavailable()


async def test_without_a_token_the_status_is_unavailable() -> None:
    fake = FakeIotec(answering(200, transaction("Success")), token_status=401)
    reading = await adapter(fake).collection_status(
        request_id=DEPOSIT, provider_reference=TRANSACTION
    )
    assert reading == Unavailable()


async def test_a_test_wallets_amount_is_read_in_the_test_currency() -> None:
    fake = FakeIotec(answering(200, transaction("Success", currency="ITX")))
    reading = await adapter(fake, currency_code="ITX").collection_status(
        request_id=DEPOSIT, provider_reference=TRANSACTION
    )
    assert reading == Confirmed(TRANSACTION, AMOUNT)


# The token ----------------------------------------------------------------------------------


async def test_one_token_is_reused_until_shortly_before_it_expires() -> None:
    clock = FakeClock()
    fake = FakeIotec(answering(200, transaction("Pending")), token_lifetime=300)
    provider = adapter(fake, clock)
    await provider.request_collection(ask())
    clock.advance(seconds=260)
    await provider.request_collection(ask())
    assert fake.tokens_issued == 1
    clock.advance(seconds=15)
    await provider.request_collection(ask())
    assert fake.tokens_issued == 2


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, json={"access_token": "", "expires_in": 300}),
        httpx.Response(200, json={"access_token": "t", "expires_in": "300"}),
        httpx.Response(200, json={"token_type": "Bearer"}),
        httpx.Response(200, content=b"nope"),
        httpx.Response(400, json={"error": "invalid_client"}),
    ],
)
async def test_a_token_answer_that_cannot_be_used_is_unavailable_and_names_no_secret(
    answer: httpx.Response,
) -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: answer))
    tokens = IotecTokens(settings(), http, FakeClock())
    with pytest.raises(IotecUnavailable) as raised:
        await tokens.bearer()
    assert "client-secret-for-tests" not in str(raised.value)


async def test_the_token_request_uses_client_credentials() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": "t", "expires_in": 300})

    http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    assert await IotecTokens(settings(), http, FakeClock()).bearer() == "t"
    form = dict(item.split("=") for item in seen[0].content.decode().split("&"))
    assert form == {
        "client_id": "client",
        "client_secret": "client-secret-for-tests",
        "grant_type": "client_credentials",
    }


# Callbacks ----------------------------------------------------------------------------------


def callback(body: object, token: str | None = CALLBACK_TOKEN) -> tuple[dict[str, str], bytes]:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers, json.dumps(body).encode()


def test_a_callback_with_the_agreed_token_is_a_hint_for_one_status_change() -> None:
    provider = adapter(FakeIotec(answering(500)))
    deposit_id = push_id()
    notice = provider.read_notice(*callback(transaction("Success", externalId=deposit_id)))
    assert notice.notice_id == f"{TRANSACTION}:Success"
    assert notice.request_id == deposit_id and notice.provider_reference == TRANSACTION
    later = provider.read_notice(*callback(transaction("SentToVendor", externalId=deposit_id)))
    assert later.notice_id != notice.notice_id


@pytest.mark.parametrize("token", [None, "wrong", f"{CALLBACK_TOKEN}x"])
def test_a_callback_without_the_agreed_token_is_refused(token: str | None) -> None:
    provider = adapter(FakeIotec(answering(500)))
    with pytest.raises(InvalidNotice):
        provider.read_notice(*callback(transaction("Success"), token))


def test_callbacks_are_refused_when_no_token_is_agreed() -> None:
    provider = adapter(FakeIotec(answering(500)), callback_token=None)
    with pytest.raises(InvalidNotice):
        provider.read_notice(*callback(transaction("Success")))


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        json.dumps({"status": "Success"}).encode(),
        json.dumps({"id": 7, "status": "Success"}).encode(),
        json.dumps({"id": TRANSACTION, "status": "Success", "externalId": 5}).encode(),
        b"{" + b" " * 20_000 + b"}",
    ],
)
def test_a_callback_that_cannot_be_read_is_refused(body: bytes) -> None:
    provider = adapter(FakeIotec(answering(500)))
    with pytest.raises(InvalidNotice):
        provider.read_notice({"Authorization": f"Bearer {CALLBACK_TOKEN}"}, body)


# Choosing the provider ----------------------------------------------------------------------


@pytest.fixture
def iotec_environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("FINVEST_IOTEC_CLIENT_ID", "client")
    monkeypatch.setenv("FINVEST_IOTEC_CLIENT_SECRET", "client-secret-for-tests")
    monkeypatch.setenv("FINVEST_IOTEC_WALLET_ID", WALLET)
    return monkeypatch


def test_iotec_is_chosen_by_the_payments_setting(iotec_environment: pytest.MonkeyPatch) -> None:
    chosen = collections_provider(
        PaymentsSettings(provider="iotec"), FakeClock(), Deployment(environment="production")
    )
    assert isinstance(chosen, IotecCollections) and chosen.name == "iotec"


@pytest.mark.parametrize("environment", ["production", "prod", "something-new"])
def test_a_test_wallet_is_refused_where_real_money_is_expected(
    iotec_environment: pytest.MonkeyPatch, environment: str
) -> None:
    iotec_environment.setenv("FINVEST_IOTEC_CURRENCY_CODE", "ITX")
    with pytest.raises(SandboxNotAllowed):
        ensure_allowed(PaymentsSettings(provider="iotec"), Deployment(environment=environment))


def test_a_test_wallet_is_allowed_in_staging(iotec_environment: pytest.MonkeyPatch) -> None:
    iotec_environment.setenv("FINVEST_IOTEC_CURRENCY_CODE", "ITX")
    ensure_allowed(PaymentsSettings(provider="iotec"), Deployment(environment="staging"))


@pytest.mark.parametrize("field", ["token_url", "pay_url"])
@pytest.mark.parametrize("address", ["http://pay.example", "ftp://pay.example", "https://", ""])
def test_ioTec_is_reached_over_https_only(field: str, address: str) -> None:
    with pytest.raises(ValueError, match="https"):
        settings(**{field: address})


def test_the_scheduler_checks_the_test_wallet_rule_without_ioTec_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("CLIENT_ID", "CLIENT_SECRET", "WALLET_ID"):
        monkeypatch.delenv(f"FINVEST_IOTEC_{name}", raising=False)
    ensure_allowed(PaymentsSettings(provider="iotec"), Deployment(environment="production"))
    monkeypatch.setenv("FINVEST_IOTEC_CURRENCY_CODE", "ITX")
    with pytest.raises(SandboxNotAllowed):
        ensure_allowed(PaymentsSettings(provider="iotec"), Deployment(environment="production"))


def test_iotec_without_its_credentials_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CLIENT_ID", "CLIENT_SECRET", "WALLET_ID"):
        monkeypatch.delenv(f"FINVEST_IOTEC_{name}", raising=False)
    with pytest.raises(ValueError, match="client_id") as raised:
        collections_provider(PaymentsSettings(provider="iotec"), FakeClock())
    assert "client-secret-for-tests" not in str(raised.value)
