"""The ioTec Verify connection shared by every check that uses it.

ioTec Verify (verify-api.iotec.io) answers identity checks and phone number lookups. Each module
owns its own requests and how it reads the answers; this holds what they share: the settings,
the two headers, https only, and how a send is sorted.

How a send is sorted, the same for every caller:

* `ACCEPTED`: a 2xx answer carrying the request's ID.
* `REFUSED`: ioTec answered and will not run it (another 4xx).
* `NOT_RECEIVED`: it certainly never ran. A connect failure, or 401, 403 or 429, which ioTec
  answers before running anything. Safe to send again.
* `UNCERTAIN`: it may have arrived. A read timeout, a dropped connection, 408, a 5xx, or a 2xx
  with no request ID to read it back by. Never sent again on a guess.

Nothing here is logged. No error carries what ioTec sent back, because its answers carry the
client's details, and the key never appears in an error or a repr.
"""

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

import httpx
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_URL = "https://verify-api.iotec.io"
_REQUEST_ID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


class IotecVerifySettings(BaseSettings):
    """From the ioTec Verify portal, Management > Settings > Configuration."""

    model_config = SettingsConfigDict(
        env_prefix="FINVEST_", extra="ignore", hide_input_in_errors=True
    )

    iotec_verify_client_id: str | None = None
    iotec_verify_api_key: SecretStr | None = None
    iotec_verify_base_url: str = BASE_URL

    @field_validator("iotec_verify_base_url")
    @classmethod
    def https_only(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("FINVEST_IOTEC_VERIFY_BASE_URL must use https")
        return value

    @property
    def configured(self) -> bool:
        return bool(self.iotec_verify_client_id) and self.iotec_verify_api_key is not None

    def client(self) -> "IotecVerifyClient":
        if not self.configured:
            raise ValueError(
                "ioTec Verify needs FINVEST_IOTEC_VERIFY_CLIENT_ID and FINVEST_IOTEC_VERIFY_API_KEY"
            )
        assert self.iotec_verify_client_id and self.iotec_verify_api_key  # checked above
        return IotecVerifyClient(
            client_id=self.iotec_verify_client_id,
            api_key=self.iotec_verify_api_key,
            base_url=self.iotec_verify_base_url,
        )


class Sent(StrEnum):
    ACCEPTED = "accepted"
    REFUSED = "refused"
    NOT_RECEIVED = "not_received"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class SendResult:
    sent: Sent
    # The ID ioTec gave the request. Only for `ACCEPTED`, lower case.
    request_id: str | None = None
    # The HTTP status, for an error code. Never the body.
    status: int | None = None


# One file in a form: its name, its bytes and its content type.
Upload = tuple[str, bytes, str]


@dataclass(frozen=True, slots=True)
class _Content:
    json: Mapping[str, object] | None = None
    data: Mapping[str, str] | None = None
    files: Mapping[str, Upload] | None = None


class VerifyUnavailable(Exception):
    """A read could not be answered. Reading changes nothing at ioTec, so it can be tried again.

    The message is safe to log: it never quotes what ioTec sent.
    """


@dataclass(frozen=True)
class IotecVerifyClient:
    client_id: str
    api_key: SecretStr = field(repr=False)
    base_url: str = BASE_URL
    timeout: float = 30.0
    # A test passes a client on a fake transport.
    http: httpx.AsyncClient | None = field(default=None, repr=False)
    retry_pause: float = 1.0

    def __post_init__(self) -> None:
        if not self.client_id or not self.api_key.get_secret_value():
            raise ValueError("ioTec Verify needs a client ID and an API key")
        if not self.base_url.startswith("https://"):
            # The key and the client's details travel in every request.
            raise ValueError("ioTec Verify is only reached over https")

    async def send(self, path: str, body: Mapping[str, object]) -> SendResult:
        """POST a JSON request. One that certainly never arrived is sent once more, after a
        pause, so a dropped connection does not leave the check out."""
        return await self._send(path, _Content(json=body))

    async def send_form(
        self, path: str, fields: Mapping[str, str], files: Mapping[str, Upload]
    ) -> SendResult:
        """POST a `multipart/form-data` request, such as a face match's two photos. Sorted and
        sent again exactly as `send` is."""
        return await self._send(path, _Content(data=fields, files=files))

    async def _send(self, path: str, content: "_Content") -> SendResult:
        result = await self._send_once(path, content)
        if result.sent is Sent.NOT_RECEIVED:
            await asyncio.sleep(self.retry_pause)
            result = await self._send_once(path, content)
        return result

    async def read(self, path: str) -> Mapping[str, object]:
        """GET a request's current state, as a JSON object."""
        try:
            response = await self._request("GET", path, _Content())
        except httpx.HTTPError:
            raise VerifyUnavailable("ioTec Verify is unreachable") from None
        if response.status_code != 200:
            raise VerifyUnavailable(f"ioTec Verify answered {response.status_code}")
        body = _json(response)
        if body is None:
            raise VerifyUnavailable("ioTec Verify answered with something that is not JSON")
        return body

    async def _send_once(self, path: str, content: "_Content") -> SendResult:
        try:
            response = await self._request("POST", path, content)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return SendResult(Sent.NOT_RECEIVED)
        except httpx.HTTPError:
            # A read timeout or a dropped connection: it may have arrived.
            return SendResult(Sent.UNCERTAIN)
        status = response.status_code
        if 200 <= status < 300:
            request_id = _request_id(response)
            if request_id is None:
                # Accepted, but with nothing to read it back by.
                return SendResult(Sent.UNCERTAIN, status=status)
            return SendResult(Sent.ACCEPTED, request_id, status)
        if status in (401, 403, 429):
            # Refused before it ran: wrong credentials, or too many requests.
            return SendResult(Sent.NOT_RECEIVED, status=status)
        if status >= 500 or status == 408:
            return SendResult(Sent.UNCERTAIN, status=status)
        return SendResult(Sent.REFUSED, status=status)

    async def _request(self, method: str, path: str, content: "_Content") -> httpx.Response:
        headers = {
            "Client-Id": self.client_id,
            "X-Api-Key": self.api_key.get_secret_value(),
            "User-Agent": "finvest",
        }
        url = self.base_url.rstrip("/") + path
        if self.http is not None:
            return await self.http.request(
                method,
                url,
                headers=headers,
                json=content.json,
                data=content.data,
                files=content.files,
                timeout=self.timeout,
            )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.request(
                method,
                url,
                headers=headers,
                json=content.json,
                data=content.data,
                files=content.files,
            )


def is_request_id(value: str) -> bool:
    return _REQUEST_ID.fullmatch(value) is not None


def _json(response: httpx.Response) -> Mapping[str, object] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _request_id(response: httpx.Response) -> str | None:
    body = _json(response)
    found = None if body is None else body.get("id")
    return found.lower() if isinstance(found, str) and is_request_id(found) else None
