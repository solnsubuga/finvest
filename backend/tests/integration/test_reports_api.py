"""Reports are downloaded by the roles that may see them, for periods that fit, and each
download is on the audit trail."""

import csv
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from app.infrastructure.database import Database
from app.modules.audit import service as audit
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.investments import started
from tests.support.ledger import ugx
from tests.support.payouts import World

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        running.clock.current = datetime.now(UTC)
        yield running


def rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


async def download(api: Api, key: str, **period: str) -> list[list[str]]:
    response = await api.http.get(f"/staff/reports/{key}.csv", params=period)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    return rows(response.text)


async def test_every_report_downloads_for_its_offered_period(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    await started(app_database, worker_database, api.clock, amount=ugx(1_000_000))
    admin = await api.staff("admin")
    listed = await api.http.get("/staff/reports")
    assert listed.status_code == 200, listed.text
    reports = listed.json()["reports"]
    assert [r["key"] for r in reports] == [
        "daily_position",
        "deposits_and_withdrawals",
        "interest_and_tax",
        "maturity_ladder",
        "identity_and_risk",
        "client_statements",
        "complaints_quarterly",
    ]
    for report in reports:
        found = await download(
            api, report["key"], date_from=report["date_from"], date_to=report["date_to"]
        )
        assert found[0], report["key"]
    # The ladder far enough ahead to reach the 91 day bill shows it.
    today = listed.json()["today"]
    position = await download(api, "daily_position", date_from=today, date_to=today)
    assert position[0][0] == "Date"
    assert any(row[2] == "client.holding" for row in position[1:])

    async with api.database.actor_transaction(admin) as session:
        events = await audit.events_for_resource(session, "report", "daily_position")
    assert any(e.action == "reports.downloaded" and e.actor_id == admin.id for e in events)


async def test_a_held_payout_is_in_the_days_deposits_and_withdrawals(
    api: Api, app_database: Database, worker_database: Database
) -> None:
    world = await World(app_database, worker_database, api.clock).build(30_000_000, mobile=False)
    held = await world.request(12_000_000)
    # The world moved the clock past the new account's waiting time: read the report then.
    await api.staff("operations")
    today = (await api.http.get("/staff/reports")).json()["today"]
    found = await download(api, "deposits_and_withdrawals", date_from=today, date_to=today)
    # An ID that starts with "-" is written with a "'" first, so a spreadsheet keeps it as text.
    found = [[value.removeprefix("'") for value in row] for row in found]
    [line] = [row for row in found if row[1] == held.id]
    assert line[0] == "payout"
    assert (line[2], line[3], line[5], line[6]) == (world.client_id, "bank", "12000000", "held")


async def test_a_period_that_does_not_fit_is_refused(api: Api) -> None:
    await api.staff("finance")
    response = await api.http.get(
        "/staff/reports/interest_and_tax.csv",
        params={"date_from": "2026-10-02", "date_to": "2026-10-31"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["fields"][0]["code"] == "whole_month_only"


async def test_a_role_downloads_only_the_reports_its_screens_show(api: Api) -> None:
    await api.staff("operations")
    names = [r["key"] for r in (await api.http.get("/staff/reports")).json()["reports"]]
    assert "daily_position" not in names
    refused = await api.http.get(
        "/staff/reports/daily_position.csv",
        params={"date_from": "2026-11-01", "date_to": "2026-11-01"},
    )
    assert refused.status_code == 403
    await api.staff("support")
    assert (await api.http.get("/staff/reports")).status_code == 403
