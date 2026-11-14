"""Reading Finvest's ioTec wallet balance, against a fake ioTec."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from app.infrastructure.iotec import IotecSettings, IotecTokens
from app.modules.reconciliation.domain.wallet import WalletCheck, WalletReading
from app.modules.reconciliation.providers.iotec import IotecWallet
from app.shared.money import Currency, Money
from tests.support.clock import FakeClock

WALLET = "5e83b187-801e-410e-b76e-f491928547e0"
NOW = datetime(2026, 11, 14, 9, tzinfo=UTC)


def wallet(
    route: httpx.MockTransport | None = None, body: dict[str, object] | None = None
) -> tuple[IotecWallet, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    answer = {
        "id": WALLET,
        "currency": "UGX",
        "actualBalance": 15150000.0,
        "name": "FINVEST",
        **(body or {}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "id.iotec.test":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 300})
        seen.append(request)
        return httpx.Response(200, content=json.dumps(answer).encode())

    http = httpx.AsyncClient(transport=route or httpx.MockTransport(handler))
    settings = IotecSettings(
        client_id="finvest",
        client_secret=SecretStr("not-a-real-secret"),
        wallet_id=WALLET,
        pay_url="https://pay.iotec.test",
        token_url="https://id.iotec.test/connect/token",
    )
    return IotecWallet(
        settings, IotecTokens(settings, http, FakeClock()), http, FakeClock(NOW)
    ), seen


async def test_the_wallet_is_read_by_its_id_in_whole_shillings() -> None:
    reader, seen = wallet()
    assert await reader.wallet_balance() == WalletReading(Money(15_150_000, Currency.UGX), NOW)
    assert str(seen[0].url) == f"https://pay.iotec.test/api/wallet-balance/{WALLET}"
    assert seen[0].headers["Authorization"] == "Bearer token"


@pytest.mark.parametrize(
    "body",
    [
        {"actualBalance": 100.5},
        {"actualBalance": "100"},
        {"actualBalance": None},
        {"actualBalance": True},
        {"currency": "USD"},
        {"id": "00000000-0000-0000-0000-000000000000"},
    ],
)
async def test_a_balance_we_cannot_believe_is_no_reading(body: dict[str, object]) -> None:
    reader, _ = wallet(body=body)
    assert await reader.wallet_balance() is None


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(500),
        lambda request: httpx.Response(200, content=b"<html>"),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused")),
    ],
)
async def test_ioTec_out_of_reach_is_no_reading(handler: object) -> None:
    reader, _ = wallet(httpx.MockTransport(handler))  # type: ignore[arg-type]
    assert await reader.wallet_balance() is None


def test_the_difference_is_the_wallet_less_the_ledger() -> None:
    reading = WalletReading(Money(15_150_000, Currency.UGX), NOW)
    assert WalletCheck(reading, Money(15_165_000, Currency.UGX)).difference == Money(
        -15_000, Currency.UGX
    )
    assert WalletCheck(None, Money(1, Currency.UGX)).difference is None
