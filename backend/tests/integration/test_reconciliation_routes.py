"""Staff reconcile through the HTTP routes: statements, the day's figures, matching and the close.

The integration database is shared, and a closed day can never take another entry, so nothing
here closes a day. The journeys close their days through these routes, in a database of their
own (`tests/journeys/test_99_day_close.py`).
"""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import Response

from app.infrastructure.database import Database
from app.modules.reconciliation import statements
from app.modules.reconciliation.domain.statements import StatementSource
from app.shared.business_day import business_date
from app.shared.push_ids import push_id
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.ledger import staff

pytestmark = pytest.mark.integration

# Only this file imports statements for the Airtel wallet in this database.
POT = {"source": "iotec", "account_reference": "airtel"}
FIRST_DAY = date(2025, 6, 1)
HEADER = "Date,Details,Reference,In,Out\n"


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database) as running:
        running.clock.current = datetime.now(UTC)
        yield running


def code(response: Response) -> str:
    error = response.json()["error"]
    fields = error.get("fields") or []
    return str(fields[0]["code"] if fields else error["code"])


async def next_day(database: Database) -> tuple[date, int]:
    """The Airtel wallet's next statement day, and the balance it must open at."""
    async with database.actor_transaction(staff()) as session:
        last = await statements.last_statement(session, StatementSource.IOTEC, "airtel")
    return (
        (FIRST_DAY, 0)
        if last is None
        else (last.business_date + timedelta(days=1), last.closing_balance.minor)
    )


async def upload(
    api: Api, day: date, opening: str, closing: str, csv: str, **form: str
) -> Response:
    return await api.http.post(
        "/staff/reconciliation/statements",
        data={
            **POT,
            "statement_date": day.isoformat(),
            "opening_balance": opening,
            "closing_balance": closing,
            **form,
        },
        files={"file": ("statement.csv", csv.encode(), "text/csv")},
    )


async def test_staff_import_a_statement_and_list_it(api: Api) -> None:
    await api.staff("finance")
    day, opening = await next_day(api.database)
    reference = push_id()
    csv = HEADER + f'{day.isoformat()},Collection,{reference},"1,500,000",\n'
    closing = opening + 1_500_000
    imported = await upload(api, day, f"{opening:,}", f"{closing:,}", csv)
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert (body["business_date"], body["line_count"]) == (day.isoformat(), 1)
    assert (body["opening"]["minor"], body["closing"]["minor"]) == (opening, closing)

    again = await upload(api, day, f"{opening:,}", f"{closing:,}", csv)
    assert (again.status_code, code(again)) == (409, "statement_already_imported")
    skipped = await upload(api, day + timedelta(days=2), f"{closing:,}", f"{closing:,}", HEADER)
    assert (skipped.status_code, code(skipped)) == (409, "statement_day_out_of_order")

    pots = await api.http.get("/staff/reconciliation/pots")
    assert pots.status_code == 200, pots.text
    [airtel] = [pot for pot in pots.json() if pot["account_reference"] == "airtel"]
    assert (airtel["last_statement_date"], airtel["last_closing"]["minor"]) == (
        day.isoformat(),
        closing,
    )
    listed = await api.http.get(
        "/staff/reconciliation/statements",
        params={**POT, "date_from": day.isoformat(), "date_to": day.isoformat()},
    )
    assert [each["id"] for each in listed.json()] == [body["id"]]
    reversed_range = await api.http.get(
        "/staff/reconciliation/statements",
        params={
            **POT,
            "date_from": day.isoformat(),
            "date_to": (day - timedelta(days=1)).isoformat(),
        },
    )
    assert (reversed_range.status_code, code(reversed_range)) == (422, "date_range_reversed")


async def test_a_statement_is_refused_whole_with_the_row_that_is_wrong(api: Api) -> None:
    await api.staff("finance")
    day, opening = await next_day(api.database)
    good = f"{day.isoformat()},Collection,{push_id()},1000,\n"
    bad = f"{day.isoformat()},Collection,{push_id()},12.5,\n"
    refused = await upload(api, day, str(opening), str(opening + 1000), HEADER + good + bad)
    assert refused.status_code == 422, refused.text
    [problem] = refused.json()["error"]["fields"]
    assert problem["field_path"] == ["body", "file", 2]
    # The amount is never echoed back.
    assert "12.5" not in refused.text

    off = await upload(api, day, str(opening), str(opening + 1), HEADER + good)
    assert (off.status_code, code(off)) == (422, "statement_does_not_add_up")
    balance = await upload(api, day, "1500,00", str(opening), HEADER)
    assert balance.json()["error"]["fields"][0]["field_path"] == ["body", "opening_balance"]
    unknown = await api.http.post(
        "/staff/reconciliation/statements",
        data={
            "source": "iotec",
            "account_reference": "somewhere",
            "statement_date": day.isoformat(),
            "opening_balance": "0",
            "closing_balance": "0",
        },
        files={"file": ("statement.csv", HEADER.encode(), "text/csv")},
    )
    assert (unknown.status_code, code(unknown)) == (422, "pot_unknown")
    today = business_date(api.clock.now())
    early = await upload(api, today, "0", "0", HEADER)
    assert early.status_code == 409, early.text
    # Nothing of a refused statement was saved.
    assert (await next_day(api.database))[0] == day


async def test_the_day_shows_each_pot_and_why_it_cannot_close_yet(api: Api) -> None:
    await api.staff("finance")
    yesterday = business_date(api.clock.now()) - timedelta(days=1)
    seen = await api.http.get(f"/staff/reconciliation/days/{yesterday.isoformat()}")
    assert seen.status_code == 200, seen.text
    day = seen.json()
    assert {(p["source"], p["account_reference"]) for p in day["positions"]} == {
        ("iotec", "mtn"),
        ("iotec", "airtel"),
        ("bank", "trust"),
    }
    if day["close"] is None:
        assert day["blockers"], "a day with pots and no statements cannot be closed"
    for position in day["positions"]:
        assert position["ledger_closing"]["currency"] == "UGX"
        if position["statement_closing"] is None:
            assert position["unexplained"] is None

    today = business_date(api.clock.now()).isoformat()
    early = await api.http.post(f"/staff/reconciliation/days/{today}/prepare")
    assert (early.status_code, code(early)) == (409, "day_not_ended")
    early = await api.http.post(f"/staff/reconciliation/days/{today}/refresh")
    assert (early.status_code, code(early)) == (409, "day_not_ended")


async def test_matching_and_deciding_a_close_are_refused_plainly(api: Api) -> None:
    await api.staff("finance")
    nothing = await api.http.post(
        "/staff/reconciliation/matches",
        json={
            "statement_line_id": push_id(),
            "entry_id": push_id(),
            "line_no": 1,
            "reason": "Same transfer",
        },
    )
    assert (nothing.status_code, code(nothing)) == (409, "match_not_found")
    blank = await api.http.post(
        "/staff/reconciliation/matches",
        json={"statement_line_id": push_id(), "entry_id": push_id(), "line_no": 1, "reason": ""},
    )
    assert blank.status_code == 422, blank.text

    far = "2019-01-01"
    unprepared = await api.http.post(f"/staff/reconciliation/days/{far}/approve")
    assert (unprepared.status_code, code(unprepared)) == (409, "close_not_prepared")
    back = await api.http.post(
        f"/staff/reconciliation/days/{far}/send-back", json={"reason": "Wrong figures"}
    )
    assert (back.status_code, code(back)) == (409, "close_not_prepared")

    # The second signature needs the second step from the last ten minutes.
    api.clock.current += timedelta(minutes=11)
    stale = await api.http.post(f"/staff/reconciliation/days/{far}/approve")
    assert (stale.status_code, code(stale)) == (403, "confirm_it_is_you")


async def test_reconciliation_needs_its_permission(api: Api) -> None:
    await api.staff("support")
    assert (await api.http.get("/staff/reconciliation/pots")).status_code == 403
    day = (business_date(api.clock.now()) - timedelta(days=1)).isoformat()
    assert (await api.http.get(f"/staff/reconciliation/days/{day}")).status_code == 403
    assert (await api.http.post(f"/staff/reconciliation/days/{day}/prepare")).status_code == 403


async def test_seeing_reconciliation_does_not_let_you_change_it(api: Api) -> None:
    # Operations holds `reconciliation.view` but not `reconciliation.change`.
    await api.staff("operations")
    day, opening = await next_day(api.database)
    assert (await api.http.get("/staff/reconciliation/pots")).status_code == 200
    assert (await api.http.get(f"/staff/reconciliation/days/{day.isoformat()}")).status_code == 200
    imported = await upload(api, day, f"{opening:,}", f"{opening:,}", HEADER)
    assert (imported.status_code, code(imported)) == (403, "forbidden")
    for path in ("refresh", "prepare", "approve"):
        refused = await api.http.post(f"/staff/reconciliation/days/{day.isoformat()}/{path}")
        assert (refused.status_code, code(refused)) == (403, "forbidden"), path
    sent_back = await api.http.post(
        f"/staff/reconciliation/days/{day.isoformat()}/send-back", json={"reason": "No"}
    )
    assert sent_back.status_code == 403
    matched = await api.http.post(
        "/staff/reconciliation/matches",
        json={"statement_line_id": push_id(), "entry_id": push_id(), "line_no": 1, "reason": "No"},
    )
    assert matched.status_code == 403
