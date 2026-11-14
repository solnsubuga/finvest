"""Company and group accounts, against real PostgreSQL: the applicant, the people who sign, the
documents, the two members of staff who open it, and the fences the database holds."""

import asyncio
import secrets
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import Database
from app.infrastructure.storage import MemoryStorage
from app.modules.organisations import service as organisations
from app.shared.actors import SYSTEM, Actor
from app.shared.errors import ApplicationError
from tests.support.database import TestDatabase
from tests.support.http import Api, running_api
from tests.support.kyc import verify

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.4\n% a test document\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
async def api(test_database: TestDatabase, app_database: Database) -> AsyncIterator[Api]:
    async with running_api(test_database, app_database, storage=MemoryStorage()) as api:
        yield api


async def person(api: Api, *, verified: bool = True) -> Actor:
    client = await api.client()
    if verified:
        await verify(api.database, client, api.clock)
    return client


async def number_of(api: Api, client: Actor) -> str:
    async with api.database.actor_transaction(client) as session:
        found = await session.scalar(
            text("SELECT account_number FROM kyc_checks WHERE client_id = :id"), {"id": client.id}
        )
    assert isinstance(found, str)
    return found


async def apply(
    api: Api, kind: str = "company", approvals: int = 1, name: str = "Kampala Traders Ltd"
) -> httpx.Response:
    return await api.http.post(
        "/client/organisations",
        json={
            "kind": kind,
            "name": name,
            "registration_number": f"8002{secrets.randbelow(10**10):010d}"
            if kind == "company"
            else None,
            "approvals_needed": approvals,
            "role": "Director" if kind == "company" else "Chairperson",
        },
    )


async def upload(
    api: Api,
    organisation_id: str,
    kind: str,
    data: bytes = PDF,
    content_type: str = "application/pdf",
) -> httpx.Response:
    return await api.http.put(
        f"/client/organisations/{organisation_id}/documents/{kind}",
        files={"document": ("file", data, content_type)},
    )


async def invite(api: Api, organisation_id: str, to: str, role: str = "Director") -> httpx.Response:
    return await api.http.post(
        f"/client/organisations/{organisation_id}/signatories", json={"to": to, "role": role}
    )


async def step(api: Api, organisation_id: str, name: str, note: str = "Checked") -> httpx.Response:
    return await api.http.post(
        f"/staff/organisations/{organisation_id}/{name}", json={"note": note}
    )


async def notices(api: Api, client: Actor, event: str) -> list[str]:
    async with api.database.actor_transaction(client) as session:
        rows = await session.execute(
            text("SELECT body FROM notifications WHERE client_id = :id AND event = :event"),
            {"id": client.id, "event": event},
        )
        return [row.body for row in rows.all()]


async def run_invitations(worker_database: Database, api: Api) -> int:
    """Run every invitation job the way the worker does. Returns how many people were told."""
    told = 0
    async with worker_database.actor_transaction(SYSTEM) as session:
        payloads = (
            await session.execute(
                text("SELECT payload FROM jobs WHERE kind = :kind ORDER BY created_at"),
                {"kind": organisations.TELL_INVITED_KIND},
            )
        ).scalars()
        for payload in list(payloads):
            told += await organisations.tell_invited(session, dict(payload), api.clock)
    return told


async def ready_company(api: Api, worker_database: Database) -> tuple[Actor, Actor, str]:
    """A company with two directors who joined and both documents, not yet handed in."""
    director = await person(api)
    applicant = await person(api)
    applied = await apply(api)
    assert applied.status_code == 201, applied.text
    organisation_id = applied.json()["id"]
    assert (await invite(api, organisation_id, await number_of(api, director))).status_code == 201
    for kind in ("certificate_of_incorporation", "board_resolution"):
        assert (await upload(api, organisation_id, kind)).status_code == 200
    await api.as_client(director)
    joined = await api.http.post(
        f"/client/organisations/{organisation_id}/answer", json={"join": True}
    )
    assert joined.status_code == 200, joined.text
    await api.as_client(applicant)
    return applicant, director, organisation_id


async def test_a_company_opens_after_its_people_join_and_two_staff_approve(
    api: Api, worker_database: Database
) -> None:
    director = await person(api)
    applicant = await person(api)
    applied = await apply(api)
    assert applied.status_code == 201, applied.text
    body = applied.json()
    organisation_id = body["id"]
    assert body["status"] == "draft" and body["is_applicant"] is True
    assert body["documents_needed"] == ["certificate_of_incorporation", "board_resolution"]
    assert body["problems"] == ["documents_missing"]
    assert [s["role"] for s in body["signatories"]] == ["Director"]

    invited = await invite(api, organisation_id, await number_of(api, director))
    assert invited.status_code == 201, invited.text
    added = invited.json()["signatories"][1]
    assert added["status"] == "invited" and added["name"].endswith(".")
    assert "signatories_waiting" in invited.json()["problems"]
    again = await invite(api, organisation_id, await number_of(api, director))
    assert again.json()["error"]["fields"][0]["code"] == "already_a_signatory"

    refused = await api.http.post(f"/client/organisations/{organisation_id}/submission")
    assert refused.status_code == 409
    codes = {f["code"] for f in refused.json()["error"]["fields"]}
    assert codes == {"signatories_waiting", "documents_missing"}

    for kind in ("certificate_of_incorporation", "board_resolution"):
        uploaded = await upload(api, organisation_id, kind)
        assert uploaded.status_code == 200, uploaded.text
    assert await run_invitations(worker_database, api) == 1
    assert len(await notices(api, director, "organisation_invited")) == 1

    await api.as_client(director)
    listed = (await api.http.get("/client/organisations")).json()["items"]
    assert [(o["id"], o["invited"], o["is_applicant"]) for o in listed] == [
        (organisation_id, True, False)
    ]
    # Only the applicant changes it.
    assert (await upload(api, organisation_id, "board_resolution")).status_code == 403
    joined = await api.http.post(
        f"/client/organisations/{organisation_id}/answer", json={"join": True}
    )
    assert joined.status_code == 200, joined.text
    assert joined.json()["invited"] is False

    await api.as_client(applicant)
    submitted = await api.http.post(f"/client/organisations/{organisation_id}/submission")
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted" and submitted.json()["problems"] == []
    late = await api.http.patch(f"/client/organisations/{organisation_id}", json={"name": "X"})
    assert late.status_code == 409

    checker = await api.staff("operations")
    queue = (await api.http.get("/staff/organisations")).json()["items"]
    assert [o["id"] for o in queue] == [organisation_id]
    assert queue[0]["can_check"] is True and queue[0]["can_approve"] is False
    assert all(s["verified_name"] for s in queue[0]["signatories"])
    links = await api.http.post(f"/staff/organisations/{organisation_id}/document-links")
    assert links.status_code == 200, links.text
    assert len(links.json()["documents"]) == 2
    checked = await step(api, organisation_id, "check")
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "checked" and checked.json()["can_approve"] is False
    assert (await step(api, organisation_id, "approval")).status_code == 403

    await api.staff("compliance")
    inbox = (await api.http.get("/staff/approvals")).json()
    assert any(i["kind"] == "organisation" and i["id"] == organisation_id for i in inbox["items"])
    approved = await step(api, organisation_id, "approval", "Second check done")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved" and body["checked_by"] == checker.id
    assert body["account_number"].startswith("30") and len(body["account_number"]) == 10
    for client in (applicant, director):
        assert len(await notices(api, client, "organisation_approved")) == 1

    async with api.database.actor_transaction(checker) as session:
        actions = (
            await session.execute(
                text(
                    "SELECT action FROM audit_events WHERE resource_type = 'organisation' "
                    "AND resource_id = :id ORDER BY occurred_at, id"
                ),
                {"id": organisation_id},
            )
        ).scalars()
        assert list(actions) == [
            "organisation.applied",
            "organisation.signatory_invited",
            "organisation.document_added",
            "organisation.document_added",
            "organisation.signatory_joined",
            "organisation.submitted",
            "organisation.documents_viewed",
            "organisation.checked",
            "organisation.approved",
        ]


async def test_a_group_needs_two_people_and_two_approvals(api: Api) -> None:
    await person(api)
    applied = await apply(api, kind="group", approvals=1, name="Ntinda Savings Group")
    assert applied.status_code == 201, applied.text
    problems = applied.json()["problems"]
    assert problems == ["too_few_signatories", "approvals_too_few", "documents_missing"]
    assert applied.json()["registration_number"] is None
    organisation_id = applied.json()["id"]
    # A company's document is not a group's.
    wrong = await upload(api, organisation_id, "certificate_of_incorporation")
    assert wrong.json()["error"]["fields"][0]["code"] == "not_for_this_kind"
    changed = await api.http.patch(
        f"/client/organisations/{organisation_id}", json={"approvals_needed": 3}
    )
    assert "approvals_more_than_signatories" in changed.json()["problems"]


async def test_a_document_must_be_what_it_claims_to_be(api: Api) -> None:
    await person(api)
    organisation_id = (await apply(api)).json()["id"]
    fake = await upload(api, organisation_id, "board_resolution", data=b"<html>")
    assert fake.status_code == 422
    assert fake.json()["error"]["fields"][0]["code"] == "not_a_file_of_that_type"
    other = await upload(api, organisation_id, "board_resolution", PNG, "text/html")
    assert other.json()["error"]["fields"][0]["code"] == "unsupported_content_type"
    replaced = await upload(api, organisation_id, "board_resolution", PNG, "image/png")
    assert replaced.status_code == 200
    replaced = await upload(api, organisation_id, "board_resolution")
    assert [d["content_type"] for d in replaced.json()["documents"]] == ["application/pdf"]


async def test_only_approved_clients_apply_or_sign(api: Api) -> None:
    unverified = await person(api, verified=False)
    refused = await apply(api)
    assert refused.status_code == 409
    assert refused.json()["error"]["fields"][0]["code"] == "not_verified"

    applicant = await person(api)
    organisation_id = (await apply(api)).json()["id"]
    async with api.database.transaction() as session:
        phone = await session.scalar(
            text("SELECT phone FROM identity_clients WHERE id = :id"), {"id": unverified.id}
        )
    nobody = await invite(api, organisation_id, str(phone))
    assert nobody.status_code == 422
    assert nobody.json()["error"]["fields"][0]["code"] == "no_such_client"
    myself = await invite(api, organisation_id, await number_of(api, applicant))
    assert myself.json()["error"]["fields"][0]["code"] == "already_a_signatory"


async def test_staff_send_it_back_and_the_applicant_hands_it_in_again(
    api: Api, worker_database: Database
) -> None:
    applicant, _, organisation_id = await ready_company(api, worker_database)
    assert (await api.http.post(f"/client/organisations/{organisation_id}/submission")).is_success
    await api.staff("operations")
    assert (await step(api, organisation_id, "check")).is_success
    returned = await step(api, organisation_id, "return", "The resolution is not signed")
    assert returned.status_code == 200, returned.text
    assert returned.json()["status"] == "draft" and returned.json()["checked_by"] is None

    await api.as_client(applicant)
    seen = (await api.http.get(f"/client/organisations/{organisation_id}")).json()
    assert seen["return_note"] == "The resolution is not signed"
    assert len(await notices(api, applicant, "organisation_returned")) == 1
    assert (await upload(api, organisation_id, "board_resolution")).is_success
    again = await api.http.post(f"/client/organisations/{organisation_id}/submission")
    assert again.status_code == 200 and again.json()["return_note"] is None

    await api.staff("compliance")
    rejected = await step(api, organisation_id, "rejection", "Not a real company")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert (await step(api, organisation_id, "check")).status_code == 409
    assert len(await notices(api, applicant, "organisation_rejected")) == 1


async def test_a_signatory_who_declines_no_longer_sees_it_and_can_be_asked_again(
    api: Api,
) -> None:
    other = await person(api)
    applicant = await person(api)
    organisation_id = (await apply(api)).json()["id"]
    await invite(api, organisation_id, await number_of(api, other))
    await api.as_client(other)
    declined = await api.http.post(
        f"/client/organisations/{organisation_id}/answer", json={"join": False}
    )
    assert declined.status_code == 200
    assert (await api.http.get(f"/client/organisations/{organisation_id}")).status_code == 404
    await api.as_client(applicant)
    statuses = [
        s["status"]
        for s in (await invite(api, organisation_id, await number_of(api, other))).json()[
            "signatories"
        ]
    ]
    assert statuses == ["joined", "invited"]
    removed = await api.http.delete(
        f"/client/organisations/{organisation_id}/signatories/{other.id}"
    )
    assert [s["status"] for s in removed.json()["signatories"]] == ["joined", "removed"]


async def test_a_stranger_cannot_see_or_change_an_application(api: Api) -> None:
    await person(api)
    organisation_id = (await apply(api)).json()["id"]
    await person(api)
    assert (await api.http.get(f"/client/organisations/{organisation_id}")).status_code == 404
    assert (await api.http.get("/client/organisations")).json()["items"] == []
    patched = await api.http.patch(
        f"/client/organisations/{organisation_id}", json={"name": "Mine"}
    )
    assert patched.status_code == 404


async def test_the_database_refuses_moves_the_service_would_not_make(
    api: Api, worker_database: Database
) -> None:
    applicant, _, organisation_id = await ready_company(api, worker_database)
    # A client cannot approve their own application.
    with pytest.raises(DBAPIError, match=r"only the applicant|a draft is only handed in"):
        async with api.database.actor_transaction(applicant) as session:
            await session.execute(
                text("UPDATE organisations SET status = 'approved' WHERE id = :id"),
                {"id": organisation_id},
            )
    await api.http.post(f"/client/organisations/{organisation_id}/submission")
    checker = await api.staff("operations")
    await step(api, organisation_id, "check")
    # The checker cannot approve their own check, even around the service.
    with pytest.raises(DBAPIError, match=r"second member of staff|two_people"):
        async with api.database.actor_transaction(checker) as session:
            await session.execute(
                text(
                    "UPDATE organisations SET status = 'approved', decided_by = :staff, "
                    "decided_at = now(), decision_note = 'x', account_number = '3000000000' "
                    "WHERE id = :id"
                ),
                {"staff": checker.id, "id": organisation_id},
            )
    # Nobody deletes the record.
    with pytest.raises(DBAPIError, match="permission denied"):
        async with api.database.actor_transaction(checker) as session:
            await session.execute(
                text("DELETE FROM organisation_signatories WHERE organisation_id = :id"),
                {"id": organisation_id},
            )


async def test_two_staff_approving_at_once_open_it_once_with_one_number(
    api: Api, worker_database: Database
) -> None:
    _, _, organisation_id = await ready_company(api, worker_database)
    await api.http.post(f"/client/organisations/{organisation_id}/submission")
    await api.staff("operations")
    await step(api, organisation_id, "check")
    first, second = await api.staff("compliance"), await api.staff("admin")

    async def approve(staff: Actor) -> str:
        try:
            async with api.database.actor_transaction(staff) as session:
                found = await organisations.approve(
                    session, staff, organisation_id, "Second check done", api.clock
                )
        except ApplicationError as error:
            return error.code
        assert found.account_number is not None
        return found.account_number

    outcomes = await asyncio.gather(approve(first), approve(second))
    numbers = [o for o in outcomes if o.startswith("30")]
    assert len(numbers) == 1 and "conflict" in outcomes, outcomes
    async with api.database.actor_transaction(first) as session:
        count = await session.scalar(
            text(
                "SELECT count(*) FROM notifications WHERE event = 'organisation_approved' "
                "AND link_id = :id"
            ),
            {"id": organisation_id},
        )
    assert count == 2
