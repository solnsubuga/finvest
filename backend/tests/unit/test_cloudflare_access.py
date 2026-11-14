"""The Cloudflare Zero Trust list adapter, against a fake Cloudflare."""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.modules.identity.cloudflare_access import CloudflareAccessSettings, CloudflareStaffList
from app.modules.identity.domain.staff_access import (
    AccessRefused,
    AccessState,
    AccessUnavailable,
    belongs_on_list,
    settled_state,
)

TOKEN = "not-a-real-token"
ITEMS = "https://api.cloudflare.test/accounts/acct/gateway/lists/list-1/items"
LIST = "https://api.cloudflare.test/accounts/acct/gateway/lists/list-1"


def staff_list(handler: httpx.MockTransport) -> CloudflareStaffList:
    return CloudflareStaffList(
        account_id="acct",
        list_id="list-1",
        token=SecretStr(TOKEN),
        base_url="https://api.cloudflare.test",
        client=httpx.AsyncClient(transport=handler),
    )


def answering(*responses: httpx.Response, seen: list[httpx.Request]) -> httpx.MockTransport:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    return httpx.MockTransport(handler)


def ok(result: object, **extra: object) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "result": result, **extra})


async def test_contains_reads_flat_and_nested_items_and_ignores_case() -> None:
    seen: list[httpx.Request] = []
    flat = staff_list(answering(ok([{"value": "Ada@finvestug.com"}]), seen=seen))
    assert await flat.contains("ada@finvestug.com") is True
    nested = staff_list(answering(ok([[{"value": "bob@finvestug.com"}]]), seen=seen))
    assert await nested.contains("ada@finvestug.com") is False
    assert seen[0].method == "GET" and str(seen[0].url).startswith(ITEMS)
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"


async def test_contains_reads_every_page() -> None:
    seen: list[httpx.Request] = []
    access = staff_list(
        answering(
            ok([{"value": "a@finvestug.com"}], result_info={"page": 1, "total_pages": 2}),
            ok([{"value": "b@finvestug.com"}], result_info={"page": 2, "total_pages": 2}),
            seen=seen,
        )
    )
    assert await access.contains("b@finvestug.com") is True
    assert [r.url.params["page"] for r in seen] == ["1", "2"]


async def test_add_and_remove_patch_the_list() -> None:
    seen: list[httpx.Request] = []
    access = staff_list(answering(ok({}), ok({}), seen=seen))
    await access.add("ada@finvestug.com")
    await access.remove("ada@finvestug.com")
    assert [(r.method, str(r.url)) for r in seen] == [("PATCH", LIST), ("PATCH", LIST)]
    assert json.loads(seen[0].content) == {"append": [{"value": "ada@finvestug.com"}]}
    assert json.loads(seen[1].content) == {"remove": ["ada@finvestug.com"]}


@pytest.mark.parametrize(
    ("status", "error", "code"),
    [
        (401, AccessRefused, "cloudflare_token_refused"),
        (403, AccessRefused, "cloudflare_token_refused"),
        (404, AccessRefused, "cloudflare_list_missing"),
        (400, AccessRefused, "cloudflare_refused"),
        (429, AccessUnavailable, "cloudflare_unavailable"),
        (500, AccessUnavailable, "cloudflare_unavailable"),
        (503, AccessUnavailable, "cloudflare_unavailable"),
    ],
)
async def test_answers_are_sorted(status: int, error: type[Exception], code: str) -> None:
    access = staff_list(answering(httpx.Response(status, json={"success": False}), seen=[]))
    with pytest.raises(error) as raised:
        await access.add("ada@finvestug.com")
    assert raised.value.code == code  # type: ignore[attr-defined]
    assert "ada" not in str(raised.value)


async def test_a_timeout_or_an_unreadable_answer_is_safe_to_try_again() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(AccessUnavailable):
        await staff_list(httpx.MockTransport(timeout)).contains("ada@finvestug.com")
    garbled = staff_list(answering(httpx.Response(200, content=b"<html>"), seen=[]))
    with pytest.raises(AccessUnavailable):
        await garbled.contains("ada@finvestug.com")


def test_settings_turn_on_only_with_all_three(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "FINVEST_CLOUDFLARE_ACCOUNT_ID",
        "FINVEST_CLOUDFLARE_STAFF_LIST_ID",
        "FINVEST_CLOUDFLARE_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    assert CloudflareAccessSettings().access_list() is None
    monkeypatch.setenv("FINVEST_CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("FINVEST_CLOUDFLARE_STAFF_LIST_ID", "list-1")
    assert CloudflareAccessSettings().access_list() is None
    monkeypatch.setenv("FINVEST_CLOUDFLARE_ACCESS_TOKEN", TOKEN)
    made = CloudflareAccessSettings().access_list()
    assert made is not None and TOKEN not in repr(made)
    monkeypatch.setenv("FINVEST_CLOUDFLARE_API_BASE_URL", "http://api.cloudflare.test")
    with pytest.raises(ValueError):
        CloudflareAccessSettings()


def test_invited_and_active_staff_belong_on_the_list() -> None:
    assert belongs_on_list("invited") and belongs_on_list("active")
    assert not belongs_on_list("disabled")
    assert settled_state(True) is AccessState.IN_ACCESS
    assert settled_state(False) is AccessState.REMOVED
