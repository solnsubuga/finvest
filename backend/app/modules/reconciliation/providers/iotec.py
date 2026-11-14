"""The balance of Finvest's ioTec wallet, from `GET /api/wallet-balance/{walletId}`.

ioTec gives `actualBalance`, the wallet's total. Its `availableBalance` is deprecated and not
read. Anything that is not a whole number of shillings in the configured currency is not
believed, and the reading is None, as when ioTec cannot be reached.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

import httpx

from app.infrastructure.iotec import IotecSettings, IotecTokens, IotecUnavailable
from app.modules.reconciliation.domain.wallet import WalletReading
from app.shared.clock import Clock
from app.shared.money import Currency, Money, MoneyError


class IotecWallet:
    def __init__(
        self, settings: IotecSettings, tokens: IotecTokens, http: httpx.AsyncClient, clock: Clock
    ) -> None:
        self._settings = settings
        self._tokens = tokens
        self._http = http
        self._clock = clock

    async def wallet_balance(self) -> WalletReading | None:
        url = f"{self._settings.pay_url.rstrip('/')}/api/wallet-balance/{self._settings.wallet_id}"
        try:
            answer = await self._get(url)
        except (IotecUnavailable, httpx.TransportError):
            return None
        if answer.status_code != 200:
            return None
        try:
            found = json.loads(answer.content, parse_float=Decimal)
        except (ValueError, InvalidOperation):
            return None
        if not isinstance(found, dict) or found.get("currency") != self._settings.currency_code:
            return None
        if str(found.get("id", "")).lower() != self._settings.wallet_id.lower():
            return None
        amount = found.get("actualBalance")
        if isinstance(amount, bool) or not isinstance(amount, Decimal | int):
            return None
        whole = Decimal(amount)
        if whole != whole.to_integral_value():
            return None
        try:
            balance = Money(int(whole), Currency.UGX)
        except MoneyError:
            return None
        return WalletReading(balance, self._clock.now())

    async def _get(self, url: str) -> httpx.Response:
        answer = await self._http.get(url, headers=_auth(await self._tokens.bearer()))
        if answer.status_code == 401:
            self._tokens.forget()
            answer = await self._http.get(url, headers=_auth(await self._tokens.bearer()))
        return answer


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}
