"""Send text messages through ioTec Messaging (messaging-api.iotec.io).

ioTec takes no idempotency key, so a request whose answer was lost may still have been sent.
Each message carries our idempotency key as ioTec's `referenceId`. When the answer is lost,
the transport asks ioTec for a message with that reference before deciding anything:

- found: it was accepted, and the send counts as done;
- not found, or ioTec cannot say: the outcome stays uncertain and the message is never sent
  again blindly. A person who did not get a code asks for a new one.

ioTec delivers to MTN and Airtel numbers in Uganda, so any other number is refused here.

Error messages are safe to store: they never hold a phone number, the text or a credential.
"""

import re

import httpx
from pydantic import SecretStr

from app.infrastructure.sms import (
    SmsDeferred,
    SmsMessage,
    SmsRejected,
    SmsResult,
    SmsUncertain,
)

DEFAULT_BASE_URL = "https://messaging-api.iotec.io"
# Ugandan mobile numbers: the 07 ranges, plus MTN 039 and Airtel 020, the same networks the
# payouts module accepts (docs/decisions.md).
UGANDA_MOBILE = re.compile(r"^\+256((?:7[0-9]|39|20)[0-9]{7})$")
# ioTec's message ids are UUIDs. Anything else is not stored.
MESSAGE_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
FAILED = frozenset({"Failed", "FailedDelivery"})


def local_number(phone: str) -> str:
    """`+256712345678` as ioTec writes it, `0712345678`. Refuses a number ioTec cannot reach."""
    match = UGANDA_MOBILE.fullmatch(phone)
    if match is None:
        raise SmsRejected(
            "ioTec delivers only to Ugandan mobile numbers", code="sms_unsupported_number"
        )
    return "0" + match.group(1)


class IotecSms:
    name = "iotec"

    def __init__(
        self,
        *,
        client_id: str,
        api_key: SecretStr,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not client_id or not api_key.get_secret_value():
            raise ValueError("An ioTec client id and API key are required")
        self.client_id = client_id
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {
            "Client-Id": self.client_id,
            "X-Api-Key": self.api_key.get_secret_value(),
            "User-Agent": "finvest",
        }

    async def send(self, message: SmsMessage) -> SmsResult:
        body = {
            "recipient": local_number(message.to),
            "body": message.text,
            "referenceId": message.idempotency_key,
        }
        try:
            response = await self._request(
                "POST", "/api/sms-message", json=body, headers=self._headers()
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            # The request never reached ioTec.
            raise SmsDeferred("ioTec is unreachable") from None
        except httpx.HTTPError as error:
            return await self._settle(message, f"no answer from ioTec ({type(error).__name__})")
        status = response.status_code
        if 200 <= status < 300:
            found = _json(response)
            if found.get("status") in FAILED:
                raise SmsRejected("ioTec refused the message", code="sms_rejected")
            return SmsResult(provider=self.name, provider_message_id=_message_id(found))
        if status in (401, 403):
            raise SmsRejected(f"ioTec refused the credentials ({status})", code="sms_auth_failed")
        if status == 429:
            # Turned away before any work was done.
            raise SmsDeferred("ioTec asked us to slow down (429)")
        if status >= 500:
            # It may have queued the message before failing.
            return await self._settle(message, f"ioTec failed ({status})")
        raise SmsRejected(f"ioTec rejected the message ({status})")

    async def _settle(self, message: SmsMessage, why: str) -> SmsResult:
        """Ask ioTec whether it holds the message. Found means sent; anything else stays open."""
        try:
            response = await self._request(
                "GET",
                "/api/sms-message",
                params={"ReferenceId": message.idempotency_key, "PageSize": "1"},
                headers=self._headers(),
            )
        except httpx.HTTPError:
            raise SmsUncertain(f"{why}; its status could not be read") from None
        if response.status_code != 200:
            raise SmsUncertain(f"{why}; its status could not be read ({response.status_code})")
        rows = _json(response).get("data")
        matches = [
            row
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict) and row.get("referenceId") == message.idempotency_key
        ]
        if not matches:
            raise SmsUncertain(f"{why}; ioTec holds no message with this reference yet")
        if matches[0].get("status") in FAILED:
            raise SmsRejected("ioTec refused the message", code="sms_rejected")
        return SmsResult(provider=self.name, provider_message_id=_message_id(matches[0]))

    async def _request(self, method: str, path: str, **options: object) -> httpx.Response:
        url = f"{self.base_url}{path}"
        if self._client is not None:
            return await self._client.request(method, url, timeout=self.timeout, **options)  # type: ignore[arg-type]
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.request(method, url, **options)  # type: ignore[arg-type]


def _json(response: httpx.Response) -> dict[str, object]:
    try:
        found = response.json()
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def _message_id(found: dict[str, object]) -> str | None:
    value = found.get("id")
    return value if isinstance(value, str) and MESSAGE_ID.fullmatch(value) else None
