"""Account code maps over HTTP and in PostgreSQL: two people change a map, every step is on the
audit trail, and an accounting system's journal is written with the approved map."""

import csv
import io
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.modules.ledger import closing
from app.shared.clock import SystemClock
from app.shared.push_ids import push_id
from tests.integration.test_dealing_api import Api, api, code, signed_in_staff
from tests.support.ledger import staff

pytestmark = pytest.mark.integration
__all__ = ["api"]

CLOCK = SystemClock()
BASE = "/staff/finance/account-codes"


async def closed_day(api: Api) -> str:
    closer = staff()
    async with api.database.actor_transaction(closer) as session:
        # Integration tests only close days in 2001, so close its first day if nothing is closed.
        closed = (
            await closing.closed_through(session)
            or (
                await closing.close_through(session, closer, date(2001, 1, 1), CLOCK)
            ).closed_through
        )
    return closed.isoformat()


def system(body: dict[str, object], name: str) -> dict[str, object]:
    systems = body["systems"]
    assert isinstance(systems, list)
    (found,) = [s for s in systems if s["system"] == name]
    assert isinstance(found, dict)
    return found


async def withdraw_waiting(api: Api, name: str) -> None:
    """Another test's proposal may still be waiting. Its proposer is gone, so an admin rejects
    it: the test needs the system to itself."""
    _, admin = await signed_in_staff(api, "admin")
    found = system((await api.http.get(BASE, headers=admin)).json(), name)
    proposal = found["proposal"]
    if isinstance(proposal, dict):
        rejected = await api.http.post(
            f"{BASE}/proposals/{proposal['id']}/decision",
            json={"decision": "rejected", "reason": "Clearing the way for a test"},
            headers=admin,
        )
        assert rejected.status_code == 200, rejected.text


async def test_settings_viewers_see_the_maps_and_only_admins_change_them(api: Api) -> None:
    _, support = await signed_in_staff(api, "support")
    refused = await api.http.get(BASE, headers=support)
    assert (refused.status_code, code(refused)) == (403, "forbidden")
    # Finance holds `settings.view`, so it reads the maps, but only `account_codes.change`
    # proposes one.
    _, finance = await signed_in_staff(api, "finance")
    assert (await api.http.get(BASE, headers=finance)).status_code == 200
    refused = await api.http.post(
        f"{BASE}/sage/proposals", json={"accounts": {}, "reason": "x"}, headers=finance
    )
    assert (refused.status_code, code(refused)) == (403, "forbidden")
    assert (await api.http.get(BASE)).status_code == 401


async def test_a_map_takes_two_people_and_is_then_used_for_the_systems_journal(
    api: Api,
) -> None:
    await withdraw_waiting(api, "xero")
    maker_id, maker = await signed_in_staff(api, "admin")
    checker_id, checker = await signed_in_staff(api, "admin")
    listed = (await api.http.get(BASE, headers=maker)).json()
    codes = [c["code"] for c in listed["codes"]]
    xero = system(listed, "xero")
    assert xero["needs_tax_rate"] is True and xero["default_tax_rate"] == "Tax Exempt"
    assert xero["account_label"] == "Account code"

    bad = await api.http.post(
        f"{BASE}/xero/proposals",
        json={"accounts": {"no.such_code": "090"}, "reason": "First map"},
        headers=maker,
    )
    assert (bad.status_code, code(bad)) == (422, "validation_failed")
    assert bad.json()["error"]["fields"] == [
        {"field_path": ["accounts", "no.such_code"], "code": "unknown_code"},
        {"field_path": ["tax_rate"], "code": "required"},
    ]

    accounts = {c: f"X{index}" for index, c in enumerate(codes)}
    proposed = await api.http.post(
        f"{BASE}/xero/proposals",
        json={
            "accounts": accounts,
            "tax_rate": "Tax Exempt",
            "reason": "Chart from the accountant",
        },
        headers=maker,
    )
    assert proposed.status_code == 201, proposed.text
    proposal = system(proposed.json(), "xero")["proposal"]
    assert isinstance(proposal, dict)
    assert proposal["yours"] is True and proposal["accounts"] == accounts
    map_id = proposal["id"]

    again = await api.http.post(
        f"{BASE}/xero/proposals",
        json={"accounts": {}, "tax_rate": "Tax Exempt", "reason": "Second"},
        headers=checker,
    )
    assert (again.status_code, code(again)) == (409, "conflict")

    # Nothing changes for exports until a second person approves.
    _, finance = await signed_in_staff(api, "finance")
    formats = (await api.http.get("/staff/finance", headers=finance)).json()["formats"]
    assert [f["format"] for f in formats] == ["csv", "quickbooks", "xero", "sage"]

    own = await api.http.post(
        f"{BASE}/proposals/{map_id}/decision",
        json={"decision": "approved", "reason": "Mine"},
        headers=maker,
    )
    assert (own.status_code, code(own)) == (403, "forbidden")
    approved = await api.http.post(
        f"{BASE}/proposals/{map_id}/decision",
        json={"decision": "approved", "reason": "Matches the chart"},
        headers=checker,
    )
    assert approved.status_code == 200, approved.text
    active = system(approved.json(), "xero")["active"]
    assert isinstance(active, dict)
    assert active["id"] == map_id and active["decided_by"] == checker_id
    assert active["proposed_by"] == maker_id and active["yours"] is False
    assert system(approved.json(), "xero")["proposal"] is None

    async with api.database.actor_transaction(staff()) as session:
        actions = (
            await session.execute(
                text(
                    "SELECT action, details->>'system' AS system FROM audit_events "
                    "WHERE resource_type = 'finance_code_map' AND resource_id = :id "
                    "ORDER BY occurred_at"
                ),
                {"id": map_id},
            )
        ).all()
    assert [(a.action, a.system) for a in actions] == [
        ("finance.code_map_proposed", "xero"),
        ("finance.code_map_approved", "xero"),
    ]

    screen = (await api.http.get("/staff/finance", headers=finance)).json()
    (xero_format,) = [f for f in screen["formats"] if f["format"] == "xero"]
    assert xero_format["ready"] is True and xero_format["currencies"] == ["UGX"]

    day = await closed_day(api)
    exported = await api.http.post(
        "/staff/finance/journal.csv",
        json={"date_from": day, "date_to": day, "format": "xero"},
        headers=finance,
    )
    assert exported.status_code == 200, exported.text
    assert (
        f'filename="finvest-journal-xero-ugx-{day}.csv"' in exported.headers["content-disposition"]
    )
    reader = csv.reader(io.StringIO(exported.text))
    assert next(reader)[:6] == [
        "*Narration",
        "*Date",
        "Description",
        "*AccountCode",
        "*TaxRate",
        "*Amount",
    ]
    for row in reader:
        assert row[3] in accounts.values() and row[4] == "Tax Exempt"

    async with api.database.actor_transaction(staff()) as session:
        recorded = (
            await session.execute(
                text(
                    "SELECT format, code_map_id FROM finance_journal_exports "
                    "WHERE format = 'xero' ORDER BY exported_at DESC LIMIT 1"
                )
            )
        ).one()
    assert (recorded.format, recorded.code_map_id) == ("xero", map_id)

    dollars = await api.http.post(
        "/staff/finance/journal.csv",
        json={"currency": "USD", "date_from": day, "date_to": day, "format": "xero"},
        headers=finance,
    )
    assert (dollars.status_code, code(dollars)) == (422, "validation_failed")
    assert dollars.json()["error"]["fields"] == [
        {"field_path": ["format"], "code": "currency_not_supported"}
    ]


async def test_the_proposer_withdraws_their_own_and_nobody_else_can(api: Api) -> None:
    await withdraw_waiting(api, "sage")
    _, maker = await signed_in_staff(api, "admin")
    _, other = await signed_in_staff(api, "admin")
    proposed = await api.http.post(
        f"{BASE}/sage/proposals",
        json={"accounts": {}, "reason": "Start empty"},
        headers=maker,
    )
    assert proposed.status_code == 201, proposed.text
    proposal = system(proposed.json(), "sage")["proposal"]
    assert isinstance(proposal, dict)
    by_other = await api.http.post(
        f"{BASE}/proposals/{proposal['id']}/decision",
        json={"decision": "withdrawn", "reason": "Not mine"},
        headers=other,
    )
    assert (by_other.status_code, code(by_other)) == (403, "forbidden")
    withdrawn = await api.http.post(
        f"{BASE}/proposals/{proposal['id']}/decision",
        json={"decision": "withdrawn", "reason": "Changed my mind"},
        headers=maker,
    )
    assert withdrawn.status_code == 200, withdrawn.text
    history = system(withdrawn.json(), "sage")["history"]
    assert isinstance(history, list)
    assert history[0]["id"] == proposal["id"] and history[0]["status"] == "withdrawn"


def _as(actor_id: str) -> str:
    return (
        "SELECT set_config('finvest.actor_kind', 'staff', true), "
        f"set_config('finvest.actor_id', '{actor_id}', true)"
    )


async def test_postgresql_refuses_one_person_deciding_their_own_map(api: Api) -> None:
    await withdraw_waiting(api, "quickbooks")
    maker, other = push_id(), push_id()
    map_id = push_id()
    async with api.database.transaction() as session:
        await session.execute(text(_as(maker)))
        await session.execute(
            text(
                "INSERT INTO finance_code_maps (id, system, reason, proposed_by, proposed_at) "
                "VALUES (:id, 'quickbooks', 'Test', :maker, now())"
            ),
            {"id": map_id, "maker": maker},
        )
        await session.execute(
            text(
                "INSERT INTO finance_code_map_lines (map_id, ledger_code, account) "
                "VALUES (:id, 'bank.trust', 'Trust account')"
            ),
            {"id": map_id},
        )
    refusals = [
        # The proposer approves their own.
        (
            maker,
            "UPDATE finance_code_maps SET status = 'approved', decided_by = :maker, "
            "decided_at = now(), decision_reason = 'Mine' WHERE id = :id",
        ),
        # Someone else adds an account to it.
        (
            other,
            "INSERT INTO finance_code_map_lines (map_id, ledger_code, account) "
            "VALUES (:id, 'client.awaiting_start', 'Client funds')",
        ),
        # An account is changed after it was proposed.
        (maker, "UPDATE finance_code_map_lines SET account = 'Other' WHERE map_id = :id"),
        (maker, "DELETE FROM finance_code_map_lines WHERE map_id = :id"),
    ]
    for actor, statement in refusals:
        with pytest.raises(DBAPIError):
            async with api.database.transaction() as session:
                await session.execute(text(_as(actor)))
                await session.execute(text(statement), {"id": map_id, "maker": maker})
    async with api.database.transaction() as session:
        await session.execute(text(_as(other)))
        await session.execute(
            text(
                "UPDATE finance_code_maps SET status = 'rejected', decided_by = :other, "
                "decided_at = now(), decision_reason = 'Test' WHERE id = :id"
            ),
            {"id": map_id, "other": other},
        )
    with pytest.raises(DBAPIError):
        async with api.database.transaction() as session:
            await session.execute(text(_as(other)))
            await session.execute(
                text("UPDATE finance_code_maps SET status = 'approved' WHERE id = :id"),
                {"id": map_id},
            )
    # An export may only name an approved map of its own system.
    with pytest.raises(DBAPIError):
        async with api.database.transaction() as session:
            await session.execute(text(_as(other)))
            await session.execute(
                text(
                    "INSERT INTO finance_journal_exports (id, currency, date_from, date_to, "
                    "lines, debit_total_minor, exported_by, exported_at, format, code_map_id) "
                    "VALUES (:export, 'UGX', DATE '2001-01-01', DATE '2001-01-01', 0, 0, "
                    ":other, now(), 'quickbooks', :id)"
                ),
                {"export": push_id(), "other": other, "id": map_id},
            )
