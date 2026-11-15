"""Staff recognise each holding on a client's page by its product and maturity, not its ID."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from app.infrastructure.database import Database
from app.modules.investments import service as investments
from app.shared.actors import SYSTEM
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.investments import PRESENT, pay, request, today
from tests.support.kyc import verify

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        # The page reads the ledger at the end of today, so the API's clock is now.
        running.clock.current = datetime.now(UTC)
        yield running


async def test_an_investment_holding_is_named_by_its_product_and_maturity(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    client = await api.client()
    await verify(app_database, client, PRESENT)
    asked = await request(app_database, client, PRESENT)
    await pay(worker_database, asked, asked.amount, PRESENT)
    async with worker_database.actor_transaction(SYSTEM) as session:
        investment = await investments.start_investment(
            session, asked.id, confirmed_on=today(), clock=PRESENT
        )
    assert investment.maturity_date is not None
    await api.staff("support")
    response = await api.http.get(f"/staff/clients/{investment.client_id}")
    assert response.status_code == 200, response.text
    page = response.json()
    held = [line for line in page["owed"] if line["account_reference"] == investment.id]
    assert held, page["owed"]
    for line in held:
        assert line["product_name"] == investment.product_name
        assert line["matures_on"] == investment.maturity_date.isoformat()
    others = [line for line in page["owed"] if line["account_reference"] != investment.id]
    assert all(line["product_name"] is None and line["matures_on"] is None for line in others)
