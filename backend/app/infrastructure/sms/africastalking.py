"""Send text messages through Africa's Talking (api.africastalking.com).

The request is the one Africa's Talking's own SDKs send: a form POST to `/version1/messaging`
with `username`, `to`, `message` and, when Finvest has a registered sender name, `from`. The
API key goes in the `apiKey` header.

Africa's Talking takes no idempotency key and has no way to look up a sent message by our
reference, so a request whose answer was lost cannot be settled. It stays uncertain and is
never sent again, through this provider or any other. A person who did not get a code asks
for a new one.

Every other failure means Africa's Talking did not take the message, so the fallback
provider (`FallbackSms` in `fallback.py`) may send it instead:

- unreachable, or turned away (429, 503): deferred;
- the credentials, the request or the number refused (401, 403, 4xx), or a recipient status
  other than processed, sent or queued: rejected.

Error messages are safe to store: they never hold a phone number, the text, the cost or a
credential.
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

DEFAULT_BASE_URL = "https://api.africastalking.com"
SANDBOX_BASE_URL = "https://api.sandbox.africastalking.com"
# A recipient the message was taken for: 100 Processed, 101 Sent, 102 Queued.
ACCEPTED = frozenset({100, 101, 102})
# Recipient statuses that say which number or account is at fault, so support can tell them
# apart. Every other refused status keeps the plain `sms_rejected`.
REFUSED_CODES = {
    403: "sms_unsupported_number",  # InvalidPhoneNumber
    404: "sms_unsupported_number",  # UnsupportedNumberType
    405: "sms_provider_balance",  # InsufficientBalance
    406: "sms_blocked_by_recipient",  # UserInBlacklist
    409: "sms_blocked_by_recipient",  # DoNotDisturbRejection
}
# Africa's Talking message ids look like `ATXid_0f1e2d...`. Anything else is not stored.
MESSAGE_ID = re.compile(r"^AT[A-Za-z]*id_[A-Za-z0-9]{1,64}$")


class AfricasTalkingSms:
    name = "africastalking"

    def __init__(
        self,
        *,
        username: str,
        api_key: SecretStr,
        sender_id: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20,
        connect_timeout: float = 5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not username or not api_key.get_secret_value():
            raise ValueError("An Africa's Talking username and API key are required")
        self.username = username
        self.api_key = api_key
        self.sender_id = sender_id or None
        self.base_url = base_url.rstrip("/")
        # A short wait to connect, so a provider that is down hands over to the fallback quickly.
        self.timeout = httpx.Timeout(timeout, connect=connect_timeout)
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {
            "apiKey": self.api_key.get_secret_value(),
            "Accept": "application/json",
            "User-Agent": "finvest",
        }

    async def send(self, message: SmsMessage) -> SmsResult:
        form = {"username": self.username, "to": message.to, "message": message.text}
        if self.sender_id is not None:
            form["from"] = self.sender_id
        try:
            response = await self._post(form)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            # The request never reached Africa's Talking.
            raise SmsDeferred("Africa's Talking is unreachable") from None
        except httpx.HTTPError as error:
            raise SmsUncertain(
                f"no answer from Africa's Talking ({type(error).__name__})"
            ) from None
        status = response.status_code
        if 200 <= status < 300:
            return self._read(response)
        if status in (401, 403):
            raise SmsRejected(
                f"Africa's Talking refused the credentials ({status})", code="sms_auth_failed"
            )
        if status in (429, 503):
            # Turned away before any work was done.
            raise SmsDeferred(f"Africa's Talking is turning requests away ({status})")
        if status >= 500:
            # It may have queued the message before failing, and cannot be asked.
            raise SmsUncertain(f"Africa's Talking failed ({status})")
        raise SmsRejected(f"Africa's Talking rejected the message ({status})")

    def _read(self, response: httpx.Response) -> SmsResult:
        try:
            found = response.json()
        except ValueError:
            found = None
        data = found.get("SMSMessageData") if isinstance(found, dict) else None
        recipients = data.get("Recipients") if isinstance(data, dict) else None
        if not isinstance(recipients, list):
            # A success status with an answer we cannot read: it may have been sent.
            raise SmsUncertain("Africa's Talking answered in a form we cannot read")
        if not recipients:
            # Sent to 0 of 1: nothing was taken.
            raise SmsRejected("Africa's Talking took the message for nobody")
        recipient = recipients[0]
        if not isinstance(recipient, dict):
            raise SmsUncertain("Africa's Talking answered in a form we cannot read")
        code = recipient.get("statusCode")
        if not isinstance(code, int):
            raise SmsUncertain("Africa's Talking gave no status for the message")
        if code not in ACCEPTED:
            raise SmsRejected(
                f"Africa's Talking refused the message ({code})",
                code=REFUSED_CODES.get(code, "sms_rejected"),
            )
        value = recipient.get("messageId")
        message_id = value if isinstance(value, str) and MESSAGE_ID.fullmatch(value) else None
        return SmsResult(provider=self.name, provider_message_id=message_id)

    async def _post(self, form: dict[str, str]) -> httpx.Response:
        url = f"{self.base_url}/version1/messaging"
        if self._client is not None:
            return await self._client.post(
                url, data=form, headers=self._headers(), timeout=self.timeout
            )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.post(url, data=form, headers=self._headers())
