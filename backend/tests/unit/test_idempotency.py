from typing import Annotated

import httpx
import pytest
from pydantic import Field

from app.api.app import create_app
from app.api.idempotency import IdempotencyKey
from app.infrastructure.idempotency import parse_key, request_digest
from app.infrastructure.settings import Settings
from app.shared.contracts import Contract
from app.shared.errors import ApplicationError, ErrorEnvelope


def test_the_digest_is_stable_and_covers_the_whole_command() -> None:
    first = request_digest("invest", {"amount": 50_000, "product": "tbill-91"})
    assert first == request_digest("invest", {"product": "tbill-91", "amount": 50_000})
    assert first != request_digest("invest", {"amount": 50_001, "product": "tbill-91"})
    assert first != request_digest("withdraw", {"amount": 50_000, "product": "tbill-91"})
    assert len(first) == 64


def test_the_digest_refuses_values_that_have_no_stable_form() -> None:
    with pytest.raises(ValueError, match="Out of range"):
        request_digest("invest", {"amount": float("nan")})


@pytest.mark.parametrize("key", ["a1b2c3d4", "0d1f8c1e-6a8b-4c8e-9d57-2b0a4c3e9f11", "k" * 128])
def test_well_formed_keys_are_accepted(key: str) -> None:
    assert parse_key(key) == key


@pytest.mark.parametrize("key", [None, "", "   "])
def test_a_missing_key_is_its_own_error(key: str | None) -> None:
    with pytest.raises(ApplicationError) as raised:
        parse_key(key)
    assert raised.value.code == "idempotency_key_required"


@pytest.mark.parametrize("key", ["short", "k" * 129, "has space1", " padded-key ", "naïve-key"])
def test_a_malformed_key_is_refused_not_trimmed(key: str) -> None:
    with pytest.raises(ApplicationError) as raised:
        parse_key(key)
    assert raised.value.code == "validation_failed"


class Order(Contract):
    amount: Annotated[int, Field(gt=0)]


async def test_a_route_that_declares_the_key_refuses_requests_without_it() -> None:
    app = create_app(
        Settings(database_url="postgresql://finvest_app:unused@localhost/finvest", api_docs=True)
    )

    @app.post("/orders")
    async def order(body: Order, key: IdempotencyKey) -> dict[str, str]:
        return {"key": key}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        missing = await client.post("/orders", json={"amount": 1})
        assert missing.status_code == 428
        error = ErrorEnvelope.model_validate(missing.json()).error
        assert error.code == "idempotency_key_required"
        assert error.fields[0].field_path == ["header", "Idempotency-Key"]

        malformed = await client.post(
            "/orders", json={"amount": 1}, headers={"Idempotency-Key": "bad key"}
        )
        assert malformed.status_code == 422
        assert "bad key" not in malformed.text

        accepted = await client.post(
            "/orders", json={"amount": 1}, headers={"Idempotency-Key": "order-0001"}
        )
        assert accepted.json() == {"key": "order-0001"}

        parameters = (await client.get("/openapi.json")).json()["paths"]["/orders"]["post"][
            "parameters"
        ]
        assert [parameter["name"] for parameter in parameters] == ["Idempotency-Key"]
