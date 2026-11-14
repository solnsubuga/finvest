"""The ioTec Pay disbursements adapter against a fake ioTec, sorted by what is safe next."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from app.infrastructure.iotec import IotecSettings, IotecTokens
from app.modules.payments.domain.deposits import Network
from app.modules.payments.domain.outcomes import (
    Accepted,
    Confirmed,
    DeferReason,
    Deferred,
    NotFound,
    Pending,
    Rejected,
    RejectionCode,
    Unavailable,
    Uncertain,
    UncertainReason,
)
from app.modules.payments.settings import Deployment, PaymentsSettings, SandboxNotAllowed
from app.modules.payouts.domain.provider import DisbursementRequest
from app.modules.payouts.providers.iotec import IotecDisbursements
from app.modules.payouts.wiring import disbursements_provider
from app.modules.reconciliation.providers.iotec import IotecWallet
from app.modules.reconciliation.wiring import wallet_balances
from app.shared.money import Currency, Money
from tests.support.clock import FakeClock

WALLET = "5e83b187-801e-410e-b76e-f491928547e0"
IOTEC_ID = "2d629907-d515-4943-84b7-da337292bdba"
PAYOUT = "-OdPayout00000000001"
REQUEST = DisbursementRequest(PAYOUT, Network.MTN, "+256772000123", Money(250_000, Currency.UGX))

Route = Callable[[httpx.Request], httpx.Response]


def settings(currency: str = "UGX") -> IotecSettings:
    return IotecSettings(
        client_id="finvest",
        client_secret=SecretStr("not-a-real-secret"),
        wallet_id=WALLET,
        pay_url="https://pay.iotec.test",
        token_url="https://id.iotec.test/connect/token",
        currency_code=currency,
    )


class FakeIotec:
    """Answers the token route and the wallet balance itself, and hands every other Pay call to
    `route`. `balance` is what the wallet holds; a Response answers the balance read instead."""

    def __init__(self, route: Route, balance: int | httpx.Response = 10_000_000) -> None:
        self.route = route
        self.balance = balance
        self.calls: list[httpx.Request] = []
        self.balance_reads = 0
        self.tokens_issued = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "id.iotec.test":
            self.tokens_issued += 1
            return httpx.Response(
                200, json={"access_token": f"token-{self.tokens_issued}", "expires_in": 300}
            )
        if request.url.path.startswith("/api/wallet-balance/"):
            self.balance_reads += 1
            if isinstance(self.balance, httpx.Response):
                return self.balance
            return httpx.Response(
                200, json={"id": WALLET, "currency": "UGX", "actualBalance": self.balance}
            )
        self.calls.append(request)
        return self.route(request)


def adapter(
    route: Route, currency: str = "UGX", balance: int | httpx.Response = 10_000_000
) -> tuple[IotecDisbursements, FakeIotec]:
    fake = FakeIotec(route, balance)
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    chosen = settings(currency)
    return IotecDisbursements(chosen, IotecTokens(chosen, http, FakeClock()), http), fake


def view(status: str = "Pending", **changes: object) -> dict[str, object]:
    return {
        "id": IOTEC_ID,
        "status": status,
        "externalId": PAYOUT,
        "amount": 250000.0,
        "currency": "UGX",
        "payee": "256772000123",
        **changes,
    }


def answer(status: int = 200, body: object = None, **headers: str) -> Route:
    def route(request: httpx.Request) -> httpx.Response:
        if body is None:
            return httpx.Response(status, headers=headers)
        return httpx.Response(status, content=json.dumps(body).encode(), headers=headers)

    return route


def raising(error: Exception) -> Route:
    def route(request: httpx.Request) -> httpx.Response:
        raise error

    return route


# Sending


async def test_a_send_asks_ioTec_to_pay_the_number_with_our_payout_as_its_reference() -> None:
    provider, fake = adapter(answer(200, view("Pending")))
    assert await provider.send_disbursement(REQUEST) == Accepted(IOTEC_ID)
    [sent] = fake.calls
    assert sent.method == "POST"
    assert str(sent.url) == "https://pay.iotec.test/api/disbursements/disburse"
    assert sent.headers["Authorization"] == "Bearer token-1"
    assert json.loads(sent.content) == {
        "category": "MobileMoney",
        "currency": "UGX",
        "walletId": WALLET,
        "externalId": PAYOUT,
        "payee": "256772000123",
        "amount": 250000,
        "payeeNote": "Finvest payout",
    }


async def test_ioTec_test_wallets_are_paid_in_their_own_currency() -> None:
    provider, fake = adapter(answer(200, view("Success", currency="ITX")), currency="ITX")
    assert await provider.send_disbursement(REQUEST) == Accepted(IOTEC_ID)
    assert json.loads(fake.calls[0].content)["currency"] == "ITX"


@pytest.mark.parametrize("status", ["Pending", "SentToVendor", "Success", "AwaitingApproval"])
async def test_a_payment_ioTec_took_is_accepted_and_read_back(status: str) -> None:
    provider, _ = adapter(answer(200, view(status)))
    assert await provider.send_disbursement(REQUEST) == Accepted(IOTEC_ID)


@pytest.mark.parametrize("status", ["Failed", "Rejected", "Cancelled"])
async def test_a_payment_that_failed_at_once_is_rejected_with_its_reference(status: str) -> None:
    provider, _ = adapter(answer(200, view(status)))
    assert await provider.send_disbursement(REQUEST) == Rejected(RejectionCode.OTHER, IOTEC_ID)


async def test_a_refusal_about_the_number_rejects_the_payout() -> None:
    refused = {"code": "BadRequest", "message": "Invalid payee"}
    provider, _ = adapter(answer(400, refused))
    assert await provider.send_disbursement(REQUEST) == Rejected(RejectionCode.OTHER)


@pytest.mark.parametrize(
    ("refused", "reason"),
    [
        ({"code": "BadRequest", "message": "Invalid wallet"}, None),
        ({"code": "InsufficientFunds", "message": None}, DeferReason.WALLET_SHORT),
        ({"code": "BadRequest", "message": "Wallet balance is too low"}, DeferReason.WALLET_SHORT),
    ],
)
async def test_a_refusal_about_our_wallet_waits_instead_of_failing_the_client(
    refused: dict[str, object], reason: DeferReason | None
) -> None:
    provider, _ = adapter(answer(400, refused))
    assert await provider.send_disbursement(REQUEST) == Deferred(reason=reason)


async def test_a_wallet_holding_less_than_the_payout_waits_without_sending() -> None:
    provider, fake = adapter(answer(200, view()), balance=249_999)
    assert await provider.send_disbursement(REQUEST) == Deferred(reason=DeferReason.WALLET_SHORT)
    assert fake.balance_reads == 1
    assert fake.calls == []


async def test_a_wallet_holding_exactly_the_payout_sends_it() -> None:
    provider, fake = adapter(answer(200, view()), balance=250_000)
    assert await provider.send_disbursement(REQUEST) == Accepted(IOTEC_ID)
    assert [call.method for call in fake.calls] == ["POST"]


@pytest.mark.parametrize(
    "balance",
    [
        httpx.Response(500),
        httpx.Response(200, json={"id": WALLET, "currency": "ITX", "actualBalance": 0}),
        httpx.Response(200, json={"id": WALLET, "currency": "UGX", "actualBalance": "lots"}),
        httpx.Response(200, content=b"not json"),
    ],
)
async def test_a_balance_that_cannot_be_read_does_not_stop_the_send(
    balance: httpx.Response,
) -> None:
    provider, _ = adapter(answer(200, view()), balance=balance)
    assert await provider.send_disbursement(REQUEST) == Accepted(IOTEC_ID)


async def test_a_failed_send_for_our_balance_waits_instead_of_failing_the_client() -> None:
    failed = view("Failed", statusCode="failed", statusMessage="Insufficient wallet balance")
    provider, _ = adapter(answer(200, failed))
    assert await provider.send_disbursement(REQUEST) == Deferred(reason=DeferReason.WALLET_SHORT)


async def test_a_failed_send_for_another_reason_still_fails() -> None:
    failed = view("Failed", statusCode="failed", statusMessage="Payee number not registered")
    provider, _ = adapter(answer(200, failed))
    assert await provider.send_disbursement(REQUEST) == Rejected(RejectionCode.OTHER, IOTEC_ID)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("slow to connect"),
        httpx.PoolTimeout("no free connection"),
    ],
)
async def test_a_request_that_never_left_is_deferred(error: Exception) -> None:
    provider, _ = adapter(raising(error))
    assert await provider.send_disbursement(REQUEST) == Deferred()


async def test_no_token_means_nothing_was_sent() -> None:
    def no_tokens(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    http = httpx.AsyncClient(transport=httpx.MockTransport(no_tokens))
    chosen = settings()
    provider = IotecDisbursements(chosen, IotecTokens(chosen, http, FakeClock()), http)
    assert await provider.send_disbursement(REQUEST) == Deferred()


async def test_a_busy_ioTec_is_tried_again_after_its_hint() -> None:
    provider, _ = adapter(answer(429, **{"Retry-After": "20"}))
    assert await provider.send_disbursement(REQUEST) == Deferred(20)


async def test_a_token_ioTec_no_longer_accepts_is_replaced_once() -> None:
    answers = iter([httpx.Response(401), httpx.Response(200, json=view())])
    provider, fake = adapter(lambda request: next(answers))
    assert await provider.send_disbursement(REQUEST) == Accepted(IOTEC_ID)
    assert [call.headers["Authorization"] for call in fake.calls] == [
        "Bearer token-1",
        "Bearer token-2",
    ]


async def test_credentials_ioTec_refuses_twice_wait_for_staff() -> None:
    provider, fake = adapter(answer(401))
    assert await provider.send_disbursement(REQUEST) == Deferred()
    assert len(fake.calls) == 2


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (httpx.ReadTimeout("no answer"), UncertainReason.TIMED_OUT),
        (httpx.WriteTimeout("half sent"), UncertainReason.TIMED_OUT),
        (httpx.ReadError("reset"), UncertainReason.CONNECTION_LOST),
        (httpx.RemoteProtocolError("closed"), UncertainReason.CONNECTION_LOST),
    ],
)
async def test_no_answer_after_sending_is_uncertain(
    error: Exception, reason: UncertainReason
) -> None:
    provider, _ = adapter(raising(error))
    assert await provider.send_disbursement(REQUEST) == Uncertain(reason)


@pytest.mark.parametrize(
    "route",
    [
        answer(500),
        answer(502),
        answer(200, "not an object"),
        answer(200, view(id="not-a-uuid")),
        answer(200, view(externalId="-OdSomeoneElse000001")),
        lambda request: httpx.Response(200, content=b"<html>"),
    ],
)
async def test_an_answer_we_cannot_trust_is_uncertain(route: Route) -> None:
    provider, _ = adapter(route)
    assert await provider.send_disbursement(REQUEST) == Uncertain(UncertainReason.PROVIDER_ERROR)


@pytest.mark.parametrize(
    "request_",
    [
        DisbursementRequest(PAYOUT, Network.MTN, "+254712000123", Money(250_000, Currency.UGX)),
        DisbursementRequest(PAYOUT, Network.MTN, "+256772000123", Money(25_000, Currency.USD)),
    ],
)
async def test_what_ioTec_cannot_pay_is_refused_without_calling_it(
    request_: DisbursementRequest,
) -> None:
    provider, fake = adapter(answer(200, view()))
    assert await provider.send_disbursement(request_) == Rejected(RejectionCode.OTHER)
    assert fake.calls == []


# Reading the status


async def test_a_status_is_read_by_ioTecs_id_when_we_have_it() -> None:
    provider, fake = adapter(answer(200, view("Success")))
    reading = await provider.disbursement_status(request_id=PAYOUT, provider_reference=IOTEC_ID)
    assert reading == Confirmed(IOTEC_ID, Money(250_000, Currency.UGX))
    assert str(fake.calls[0].url) == f"https://pay.iotec.test/api/disbursements/status/{IOTEC_ID}"


async def test_a_send_with_no_answer_is_found_by_our_payout_id() -> None:
    provider, fake = adapter(answer(200, view("SentToVendor")))
    reading = await provider.disbursement_status(request_id=PAYOUT, provider_reference=None)
    assert reading == Pending(IOTEC_ID)
    assert (
        str(fake.calls[0].url) == f"https://pay.iotec.test/api/disbursements/external-id/{PAYOUT}"
    )


async def test_the_amount_ioTec_says_it_paid_is_passed_on_for_the_payout_to_check() -> None:
    provider, _ = adapter(answer(200, view("Success", amount=249999)))
    reading = await provider.disbursement_status(request_id=PAYOUT, provider_reference=IOTEC_ID)
    assert reading == Confirmed(IOTEC_ID, Money(249_999, Currency.UGX))


@pytest.mark.parametrize(
    "status", ["Pending", "SentToVendor", "AwaitingApproval", "Scheduled", "New"]
)
async def test_a_payment_still_on_its_way_is_pending(status: str) -> None:
    provider, _ = adapter(answer(200, view(status)))
    reading = await provider.disbursement_status(request_id=PAYOUT, provider_reference=IOTEC_ID)
    assert reading == Pending(IOTEC_ID)


@pytest.mark.parametrize("status", ["Failed", "Rejected", "Cancelled", "RolledBack"])
async def test_a_payment_that_did_not_go_is_rejected(status: str) -> None:
    provider, _ = adapter(answer(200, view(status)))
    reading = await provider.disbursement_status(request_id=PAYOUT, provider_reference=IOTEC_ID)
    assert reading == Rejected(RejectionCode.OTHER, IOTEC_ID)


async def test_no_record_is_not_found() -> None:
    provider, _ = adapter(answer(404))
    assert (
        await provider.disbursement_status(request_id=PAYOUT, provider_reference=None) == NotFound()
    )


async def test_a_record_for_another_payout_says_nothing_about_ours() -> None:
    provider, _ = adapter(answer(200, view("Success", externalId="-OdSomeoneElse000001")))
    assert (
        await provider.disbursement_status(request_id=PAYOUT, provider_reference=IOTEC_ID)
        == NotFound()
    )


@pytest.mark.parametrize(
    "route",
    [
        answer(500),
        answer(401),
        answer(429),
        raising(httpx.ReadTimeout("no answer")),
        raising(httpx.ConnectError("refused")),
        answer(200, view("Success", amount=250000.5)),
        answer(200, view("Success", amount="250000")),
        answer(200, view("Success", amount=0)),
        answer(200, view("Success", amount=True)),
        answer(200, view("Success", currency="USD")),
        answer(200, view("Success", id=None)),
        lambda request: httpx.Response(200, content=b"{"),
    ],
)
async def test_a_read_that_tells_us_nothing_is_tried_again(route: Route) -> None:
    provider, _ = adapter(route)
    reading = await provider.disbursement_status(request_id=PAYOUT, provider_reference=IOTEC_ID)
    assert reading == Unavailable()


async def test_a_reference_that_is_not_ioTecs_is_looked_up_by_our_id() -> None:
    provider, fake = adapter(answer(200, view("Pending")))
    await provider.disbursement_status(request_id=PAYOUT, provider_reference="sbxp:paid:UGX:1:2:3")
    assert fake.calls[0].url.path == f"/api/disbursements/external-id/{PAYOUT}"


# Choosing the provider


@pytest.fixture
def iotec_environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("FINVEST_IOTEC_CLIENT_ID", "client")
    monkeypatch.setenv("FINVEST_IOTEC_CLIENT_SECRET", "client-secret-for-tests")
    monkeypatch.setenv("FINVEST_IOTEC_WALLET_ID", WALLET)
    return monkeypatch


def test_payouts_go_through_ioTec_when_payments_do(iotec_environment: pytest.MonkeyPatch) -> None:
    chosen = disbursements_provider(PaymentsSettings(provider="iotec"), FakeClock())
    assert isinstance(chosen, IotecDisbursements)
    assert chosen.name == "iotec"
    assert isinstance(wallet_balances(PaymentsSettings(provider="iotec"), FakeClock()), IotecWallet)


def test_the_wallet_is_not_read_where_ioTec_is_not_the_provider() -> None:
    assert wallet_balances(PaymentsSettings(provider="sandbox"), FakeClock()) is None


def test_an_ioTec_test_wallet_never_pays_in_production(
    iotec_environment: pytest.MonkeyPatch,
) -> None:
    iotec_environment.setenv("FINVEST_IOTEC_CURRENCY_CODE", "ITX")
    with pytest.raises(SandboxNotAllowed):
        disbursements_provider(
            PaymentsSettings(provider="iotec"), FakeClock(), Deployment(environment="production")
        )
