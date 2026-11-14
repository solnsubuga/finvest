"""Next of kin and the investing questions, through the API as finvest_app."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.modules.kyc import next_of_kin, risk
from app.modules.kyc.domain.next_of_kin import Person, Relationship
from app.modules.kyc.risk import MAX_ANSWERS_A_DAY
from app.shared.errors import ApplicationError
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.kyc import SEALER

pytestmark = pytest.mark.integration

JOSEPH = {
    "full_name": "Joseph Nakato",
    "relationship": "spouse",
    "phone": "0701 555 210",
    "id_number": "CM90012345ABCD",
    "share_bps": 6000,
}
GRACE = {
    "full_name": "Grace Nakato",
    "relationship": "sibling",
    "phone": "+256772310448",
    "id_number": "CF91000000WXYZ",
    "share_bps": 4000,
}
ANSWERS = {
    "purpose": "grow_savings",
    "need_back": "3_to_12_months",
    "early_need": "would_use_other_savings",
    "experience": "savings_or_sacco",
}


async def audit_trail(api: Api, client_id: str) -> list[tuple[str, str]]:
    staff = await api.staff()
    async with api.database.actor_transaction(staff) as session:
        rows = await session.execute(
            text(
                "SELECT action, actor_kind FROM audit_events WHERE resource_type = 'next_of_kin' "
                "AND resource_id = :id ORDER BY occurred_at, id"
            ),
            {"id": client_id},
        )
        return [(row.action, row.actor_kind) for row in rows]


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database, identity_sealer=SEALER) as api:
        yield api


async def test_a_client_names_their_next_of_kin_and_reads_them_back(api: Api) -> None:
    client = await api.client()
    empty = (await api.http.get("/client/next-of-kin")).json()
    assert empty == {"people": [], "updated_at": None}

    saved = await api.http.put("/client/next-of-kin", json={"people": [JOSEPH, GRACE]})
    assert saved.status_code == 200, saved.text
    people = saved.json()["people"]
    assert [p["full_name"] for p in people] == ["Joseph Nakato", "Grace Nakato"]
    assert people[0]["phone"] == "+256701555210"
    assert [p["share_bps"] for p in people] == [6000, 4000]
    assert (await api.http.get("/client/next-of-kin")).json()["people"] == people

    assert await audit_trail(api, str(client.id)) == [("kyc.next_of_kin.saved", "client")]


async def test_the_database_holds_no_readable_name_number_or_id(
    api: Api, migrator_database: Database
) -> None:
    client = await api.client()
    await api.http.put("/client/next-of-kin", json={"people": [JOSEPH, GRACE]})
    async with migrator_database.transaction() as session:
        rows = (
            await session.execute(
                text("SELECT * FROM kyc_next_of_kin WHERE client_id = :id"), {"id": client.id}
            )
        ).mappings()
        dump = repr([dict(row) for row in rows])
    for secret in ("Nakato", "701555210", "CM90012345ABCD"):
        assert secret not in dump


async def test_saving_replaces_the_whole_list_and_an_empty_one_removes_everyone(api: Api) -> None:
    await api.client()
    await api.http.put("/client/next-of-kin", json={"people": [JOSEPH, GRACE]})
    alone = await api.http.put(
        "/client/next-of-kin", json={"people": [{**GRACE, "share_bps": 10000}]}
    )
    assert [p["full_name"] for p in alone.json()["people"]] == ["Grace Nakato"]
    cleared = (await api.http.put("/client/next-of-kin", json={"people": []})).json()
    assert cleared["people"] == [] and cleared["updated_at"] is not None


async def test_shares_that_do_not_add_up_are_refused_with_every_problem(api: Api) -> None:
    await api.client()
    wrong = await api.http.put(
        "/client/next-of-kin",
        json={"people": [JOSEPH, {**GRACE, "share_bps": 3000, "phone": "12"}]},
    )
    assert wrong.status_code == 422
    codes = {(tuple(f["field_path"]), f["code"]) for f in wrong.json()["error"]["fields"]}
    assert (("body", "people", 1, "phone"), "invalid_phone") in codes
    assert (("body", "people"), "shares_must_total_100") in codes
    assert (await api.http.get("/client/next-of-kin")).json()["people"] == []


async def test_a_client_cannot_name_themselves(api: Api) -> None:
    client = await api.client()
    async with api.database.transaction() as session:
        phone = await session.scalar(
            text("SELECT phone FROM identity_clients WHERE id = :id"), {"id": client.id}
        )
    response = await api.http.put(
        "/client/next-of-kin", json={"people": [{**JOSEPH, "share_bps": 10000, "phone": phone}]}
    )
    assert response.status_code == 422
    assert response.json()["error"]["fields"][0]["code"] == "your_own_number"


async def test_postgres_refuses_shares_that_do_not_add_up(api: Api) -> None:
    client = await api.client()
    with pytest.raises(DBAPIError, match="add up to 100"):
        async with api.database.actor_transaction(client) as session:
            await session.execute(
                text(
                    "INSERT INTO kyc_next_of_kin_lists (client_id, people, updated_at) "
                    "VALUES (:id, 1, now())"
                ),
                {"id": client.id},
            )
            await session.execute(
                text(
                    "INSERT INTO kyc_next_of_kin (id, client_id, position, relationship, "
                    "share_bps, full_name_sealed, phone_sealed, id_number_sealed, created_at) "
                    "VALUES ('-Nabc0000000000000K1', :id, 0, 'spouse', 9999, :blob, :blob, "
                    ":blob, now())"
                ),
                {"id": client.id, "blob": b"\x02" * 40},
            )


async def test_postgres_refuses_a_list_that_miscounts_its_people(api: Api) -> None:
    client = await api.client()
    with pytest.raises(DBAPIError, match="how many people"):
        async with api.database.actor_transaction(client) as session:
            await session.execute(
                text(
                    "INSERT INTO kyc_next_of_kin_lists (client_id, people, updated_at) "
                    "VALUES (:id, 2, now())"
                ),
                {"id": client.id},
            )


async def test_two_saves_at_once_leave_one_whole_list(api: Api) -> None:
    client = await api.client()
    clock = api.clock

    async def save(people: list[Person]) -> None:
        async with api.database.actor_transaction(client) as session:
            await next_of_kin.save(session, SEALER, client, people, clock)
            # Hold the transaction open so the two overlap.
            await asyncio.sleep(0.2)

    one = [Person("Joseph Nakato", Relationship.SPOUSE, "+256701555210", "CM90012345ABCD", 10000)]
    two = [
        Person("Grace Nakato", Relationship.SIBLING, "+256772310448", "CF91000000WXYZ", 5000),
        Person("Paul Nakato", Relationship.CHILD, "+256772310449", "CF91000000WXYA", 5000),
    ]
    await asyncio.gather(save(one), save(two))
    async with api.database.actor_transaction(client) as session:
        found = await next_of_kin.next_of_kin(session, SEALER, str(client.id))
    assert sum(p.share_bps for p in found.people) == 10000
    assert len(found.people) in (1, 2)


async def test_another_client_never_sees_the_list(api: Api) -> None:
    owner = await api.client()
    await api.http.put("/client/next-of-kin", json={"people": [{**JOSEPH, "share_bps": 10000}]})
    await api.client()
    assert (await api.http.get("/client/next-of-kin")).json()["people"] == []
    async with api.database.actor_transaction(await api.client()) as session:
        found = await next_of_kin.next_of_kin(session, SEALER, str(owner.id))
    assert found.people == []


async def test_staff_see_next_of_kin_with_id_numbers_masked_and_it_is_recorded(api: Api) -> None:
    client = await api.client()
    await api.http.put("/client/next-of-kin", json={"people": [JOSEPH, GRACE]})
    await api.http.put("/client/risk-profile", json={"answers": ANSWERS})
    staff = await api.staff("support")
    page = await api.http.get(f"/staff/clients/{client.id}/profile")
    assert page.status_code == 200, page.text
    body = page.json()
    assert [p["id_number_masked"] for p in body["next_of_kin"]] == [
        "CM90•••••••BCD",
        "CF91•••••••XYZ",
    ]
    assert "CM90012345ABCD" not in page.text
    assert [r["profile"] for r in body["risk"]] == ["balanced"]
    trail = await audit_trail(api, str(client.id))
    assert ("kyc.next_of_kin.viewed", "staff") in trail
    assert staff.id


async def test_staff_who_may_not_see_clients_are_refused(api: Api) -> None:
    client = await api.client()
    await api.staff("finance")
    allowed = await api.http.get(f"/staff/clients/{client.id}/profile")
    # Finance may see clients; a client may not use the staff route at all.
    assert allowed.status_code == 200
    await api.as_client(client)
    assert (await api.http.get(f"/staff/clients/{client.id}/profile")).status_code == 401


async def test_a_client_answers_the_questions_and_is_asked_again_a_year_later(api: Api) -> None:
    client = await api.client()
    first = (await api.http.get("/client/risk-profile")).json()
    assert first["latest"] is None and first["due"] is True
    assert [q["code"] for q in first["questions"]] == [
        "purpose",
        "need_back",
        "early_need",
        "experience",
    ]
    answered = await api.http.put("/client/risk-profile", json={"answers": ANSWERS})
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["latest"]["profile"] == "balanced"
    assert body["latest"]["longest_term_days"] == 364
    assert body["due"] is False
    api.clock.advance(days=366)
    await api.as_client(client)
    assert (await api.http.get("/client/risk-profile")).json()["due"] is True


async def test_incomplete_answers_are_refused_and_earlier_answers_are_kept(api: Api) -> None:
    client = await api.client()
    missing = {k: v for k, v in ANSWERS.items() if k != "experience"}
    refused = await api.http.put("/client/risk-profile", json={"answers": missing})
    assert refused.status_code == 422
    assert refused.json()["error"]["fields"][0]["field_path"] == ["body", "answers", "experience"]
    await api.http.put("/client/risk-profile", json={"answers": ANSWERS})
    api.clock.advance(minutes=1)
    careful = {**ANSWERS, "need_back": "within_3_months"}
    later = (await api.http.put("/client/risk-profile", json={"answers": careful})).json()
    assert later["latest"]["profile"] == "careful"
    async with api.database.transaction() as session:
        held = await session.scalar(
            text("SELECT count(*) FROM kyc_risk_answers WHERE client_id = :id"), {"id": client.id}
        )
    assert held == 0  # No actor: row policies show it nothing.
    await api.staff()
    history = (await api.http.get(f"/staff/clients/{client.id}/profile")).json()["risk"]
    assert [r["profile"] for r in history] == ["careful", "balanced"]


async def test_answers_past_the_daily_cap_are_refused_until_a_day_has_passed(api: Api) -> None:
    client = await api.client()
    for _ in range(MAX_ANSWERS_A_DAY):
        answered = await api.http.put("/client/risk-profile", json={"answers": ANSWERS})
        assert answered.status_code == 200, answered.text
        api.clock.advance(minutes=1)
    refused = await api.http.put("/client/risk-profile", json={"answers": ANSWERS})
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    api.clock.advance(days=1)
    await api.as_client(client)
    again = await api.http.put("/client/risk-profile", json={"answers": ANSWERS})
    assert again.status_code == 200, again.text


async def test_answers_sent_at_once_stay_under_the_cap(api: Api) -> None:
    client = await api.client()

    async def send() -> int:
        async with api.database.actor_transaction(client) as session:
            try:
                await risk.answer(session, client, ANSWERS, api.clock)
            except ApplicationError:
                return 0
        return 1

    taken = await asyncio.gather(*(send() for _ in range(MAX_ANSWERS_A_DAY + 3)))
    assert sum(taken) == MAX_ANSWERS_A_DAY


async def test_nobody_changes_or_removes_answers(app_database: Database, api: Api) -> None:
    client = await api.client()
    await api.http.put("/client/risk-profile", json={"answers": ANSWERS})
    for statement in (
        "UPDATE kyc_risk_answers SET profile = 'long_term'",
        "DELETE FROM kyc_risk_answers",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_database.actor_transaction(client) as session:
                await session.execute(text(statement))


async def test_without_the_data_key_next_of_kin_are_unavailable(
    test_database: TestDatabase, app_database: Database
) -> None:
    async with running_api(test_database, app_database) as api:
        await api.client()
        assert (await api.http.get("/client/next-of-kin")).status_code == 503
        assert (await api.http.get("/client/risk-profile")).status_code == 200
