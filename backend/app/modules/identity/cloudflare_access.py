"""The Cloudflare Zero Trust list that the console's Access policy lets in.

Infrastructure makes the list (an EMAIL list, empty at first) and puts it in the Access policy
for the console and the pre-launch client app, next to the break-glass addresses Terraform owns
(infra/README.md). This adapter only reads the list and adds or removes one address at a time:

* `GET /accounts/{account}/gateway/lists/{list}/items` to ask whether an address is on it.
* `PATCH /accounts/{account}/gateway/lists/{list}` with `append` or `remove`.

The token needs Account > Zero Trust > Edit and nothing more. It is read by the worker only.
Nothing here is logged, and no error carries an address or what Cloudflare sent back.
"""

from collections.abc import Mapping
from typing import Self

import httpx
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.modules.identity.domain.staff_access import AccessRefused, AccessUnavailable

BASE_URL = "https://api.cloudflare.com/client/v4"
# A staff list is a few dozen addresses. Past this many pages something is wrong.
_MAX_PAGES = 20
_PAGE_SIZE = 1000


class CloudflareAccessSettings(BaseSettings):
    """Written into the deploy by Terraform (account, list) and by Solo (token)."""

    model_config = SettingsConfigDict(
        env_prefix="FINVEST_", extra="ignore", hide_input_in_errors=True
    )

    cloudflare_account_id: str | None = None
    cloudflare_staff_list_id: str | None = None
    cloudflare_access_token: SecretStr | None = None
    cloudflare_api_base_url: str = BASE_URL

    @model_validator(mode="after")
    def https_only(self) -> Self:
        if not self.cloudflare_api_base_url.startswith("https://"):
            raise ValueError("FINVEST_CLOUDFLARE_API_BASE_URL must use https")
        return self

    @property
    def configured(self) -> bool:
        return (
            bool(self.cloudflare_account_id)
            and bool(self.cloudflare_staff_list_id)
            and self.cloudflare_access_token is not None
            and bool(self.cloudflare_access_token.get_secret_value())
        )

    def access_list(self) -> "CloudflareStaffList | None":
        """None until all three are set: the team view then says Access sync is not set up."""
        if not self.configured:
            return None
        assert self.cloudflare_account_id and self.cloudflare_staff_list_id  # checked above
        assert self.cloudflare_access_token is not None
        return CloudflareStaffList(
            account_id=self.cloudflare_account_id,
            list_id=self.cloudflare_staff_list_id,
            token=self.cloudflare_access_token,
            base_url=self.cloudflare_api_base_url,
        )


class CloudflareStaffList:
    def __init__(
        self,
        *,
        account_id: str,
        list_id: str,
        token: SecretStr,
        base_url: str = BASE_URL,
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("Cloudflare is only reached over https")
        self._url = f"{base_url.rstrip('/')}/accounts/{account_id}/gateway/lists/{list_id}"
        self._token = token
        self._timeout = timeout
        self._client = client

    def __repr__(self) -> str:
        return "CloudflareStaffList()"

    async def contains(self, email: str) -> bool:
        wanted = email.lower()
        page = 1
        while page <= _MAX_PAGES:
            response = await self._send(
                "GET", f"{self._url}/items", params={"page": page, "per_page": _PAGE_SIZE}
            )
            body = _body(response)
            if any(value.lower() == wanted for value in _values(body.get("result"))):
                return True
            info = body.get("result_info")
            pages = info.get("total_pages") if isinstance(info, Mapping) else None
            if not isinstance(pages, int) or page >= pages:
                return False
            page += 1
        raise AccessUnavailable("cloudflare_list_too_long")

    async def add(self, email: str) -> None:
        _body(await self._send("PATCH", self._url, json={"append": [{"value": email}]}))

    async def remove(self, email: str) -> None:
        _body(await self._send("PATCH", self._url, json={"remove": [email]}))

    async def _send(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, int] | None = None,
        json: Mapping[str, object] | None = None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token.get_secret_value()}"}
        try:
            if self._client is not None:
                response = await self._client.request(
                    method, url, params=params, json=json, headers=headers, timeout=self._timeout
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.request(
                        method, url, params=params, json=json, headers=headers
                    )
        except httpx.HTTPError:
            raise AccessUnavailable("cloudflare_unreachable") from None
        status = response.status_code
        if status in (401, 403):
            raise AccessRefused("cloudflare_token_refused")
        if status == 404:
            raise AccessRefused("cloudflare_list_missing")
        if status in (408, 409, 429) or status >= 500:
            raise AccessUnavailable("cloudflare_unavailable")
        if status >= 400:
            # Such as an address the list will not take. The caller asks the list again, so an
            # address that was already there or already gone still counts as done.
            raise AccessRefused("cloudflare_refused")
        return response


def _body(response: httpx.Response) -> Mapping[str, object]:
    try:
        body = response.json()
    except ValueError:
        raise AccessUnavailable("cloudflare_unreadable") from None
    if not isinstance(body, Mapping) or body.get("success") is not True:
        raise AccessUnavailable("cloudflare_unreadable")
    return body


def _values(result: object) -> list[str]:
    """The list's addresses. Cloudflare has sent items both flat and wrapped in one more list."""
    values: list[str] = []
    stack = [result]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, Mapping) and isinstance(item.get("value"), str):
            values.append(item["value"])
    return values
