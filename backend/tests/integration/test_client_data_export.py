"""A client downloads a copy of their data: what it holds, and what it never holds."""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.investments import PRESENT, request
from tests.support.kyc import SEALER, verify
from tests.support.ledger import ugx

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database, identity_sealer=SEALER) as api:
        yield api


async def test_the_copy_holds_the_clients_details_and_records_the_download(api: Api) -> None:
    client = await api.client()
    await verify(api.database, client, api.clock)
    await request(api.database, client, PRESENT, amount=ugx(1_000_000))
    await api.as_client(client)
    await api.http.put(
        "/client/next-of-kin",
        json={
            "people": [
                {
                    "full_name": "Joseph Nakato",
                    "relationship": "spouse",
                    "phone": "0701555210",
                    "id_number": "CM90012345ABCD",
                }
            ]
        },
    )
    response = await api.http.get("/client/data-export")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    copy = response.json()
    assert copy["format"] == "finvest-client-data/1"
    assert copy["account"]["id"] == client.id
    assert copy["identity_check"]["status"] == "approved"
    assert copy["identity_check"]["details"]["document_number"]
    assert copy["next_of_kin"][0]["id_number"] == "CM90012345ABCD"
    assert [i["amount"] for i in copy["investments"]] == [
        {"amount_minor": 1_000_000, "currency": "UGX"}
    ]
    # Clients see net figures only, and nothing about how staff or providers checked them.
    for hidden in ("tax_bps", "annual_rate_bps", "review_flags", "provider", "decided_by"):
        assert hidden not in response.text

    staff = await api.staff()
    async with api.database.actor_transaction(staff) as session:
        actions = (
            await session.execute(
                text(
                    "SELECT action FROM audit_events WHERE resource_type = 'client_data' "
                    "AND resource_id = :id"
                ),
                {"id": client.id},
            )
        ).scalars()
        assert list(actions) == ["client_data.exported"]


async def test_a_new_client_gets_an_almost_empty_copy(api: Api) -> None:
    await api.client()
    copy = (await api.http.get("/client/data-export")).json()
    assert copy["identity_check"] is None and copy["next_of_kin"] == []
    assert copy["investments"] == [] and copy["account_closure"] is None
    assert copy["truncated"] == {"investments": False, "payouts": False}


async def test_the_file_is_named_for_the_day_in_kampala(api: Api) -> None:
    client = await api.client()
    # 22:30 UTC is already the next morning in Kampala.
    now = api.clock.now()
    late = now.replace(hour=22, minute=30, second=0, microsecond=0)
    if late <= now:
        late += timedelta(days=1)
    api.clock.advance(seconds=(late - now).total_seconds())
    await api.as_client(client)
    response = await api.http.get("/client/data-export")
    kampala_day = (late.date() + timedelta(days=1)).isoformat()
    assert (
        f'filename="finvest-my-data-{kampala_day}.json"' in response.headers["content-disposition"]
    )


async def test_a_download_needs_a_recent_sign_in(api: Api) -> None:
    client = await api.client()
    api.clock.advance(minutes=11)
    refused = await api.http.get("/client/data-export")
    assert refused.json()["error"]["code"] == "sign_in_again"
    await api.as_client(client)
    assert (await api.http.get("/client/data-export")).status_code == 200


async def test_without_the_data_key_the_copy_is_unavailable(
    test_database: TestDatabase, app_database: Database
) -> None:
    async with running_api(test_database, app_database) as api:
        await api.client()
        assert (await api.http.get("/client/data-export")).status_code == 503
