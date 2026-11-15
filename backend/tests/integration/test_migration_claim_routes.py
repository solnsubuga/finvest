"""A client carried over from Finvest 1.0 claims the account from the link, through HTTP."""

from typing import Any

import httpx
import pytest

from app.modules.identity.domain.terms import CURRENT_VERSIONS
from app.modules.migration import claims
from app.modules.migration.domain.claim import ClaimStatus
from app.shared.actors import SYSTEM, Actor
from tests.integration.test_claims import PASSWORD, Api, a_phone, api, imported, texted

pytestmark = pytest.mark.integration

__all__ = ["api"]  # the fixture, shared with the identity claim tests


async def invited(api: Api) -> tuple[str, str, str]:
    """An imported account with its claim and a link. Returns phone, client ID and token."""
    phone, client_id = await imported(api)
    async with api.worker.actor_transaction(SYSTEM) as session:
        await claims.create_claim(
            session,
            SYSTEM,
            api.clock,
            client_id=client_id,
            legacy_client_ref=f"CL-{client_id[-8:]}",
            needs_id_check=False,
        )
        token = await claims.send_link(session, SYSTEM, api.clock, client_id)
    return phone, client_id, token


async def start(api: Api, token: str) -> httpx.Response:
    return await api.http.post("/client/claim/start", json={"token": token})


async def complete(api: Api, token: str, challenge_id: str, code: str) -> httpx.Response:
    body = {
        "token": token,
        "challenge_id": challenge_id,
        "code": code,
        "password": PASSWORD,
        "accepted_terms": True,
    }
    return await api.http.post("/client/claim/complete", json=body)


def refusal(response: httpx.Response) -> tuple[int, Any]:
    return response.status_code, response.json()["error"]


async def test_a_client_claims_from_the_link_and_is_signed_in(api: Api) -> None:
    phone, client_id, token = await invited(api)

    started = await start(api, token)
    assert started.status_code == 200, started.text
    assert set(started.json()) == {"challenge_id"}
    [code] = await texted(api, phone)

    done = await complete(api, token, started.json()["challenge_id"], code)
    assert done.status_code == 200, done.text
    assert done.json()["session"]["client_id"] == client_id
    bearer = {"Authorization": f"Bearer {done.json()['token']}"}

    mine = await api.http.get("/client/claim", headers=bearer)
    assert mine.status_code == 200
    assert mine.json() == {
        "status": "signed_in",
        "identity_step": "selfie",
        "problem_reported": False,
    }
    # Claiming is when the client agrees to the terms and privacy notice.
    agreed = await api.http.get("/client/terms-acceptances", headers=bearer)
    assert sorted((i["document"], i["version"], i["via"]) for i in agreed.json()["items"]) == [
        ("privacy", CURRENT_VERSIONS["privacy"], "claim"),
        ("terms", CURRENT_VERSIONS["terms"], "claim"),
    ]
    # The link is spent.
    assert refusal(await start(api, token))[0] == 404

    # The identity step is covered in test_migration_claim_steps; here it moves directly.
    client = Actor("client", client_id)
    async with api.database.actor_transaction(client) as session:
        await claims.submit_identity(session, client, api.clock)
    async with api.worker.actor_transaction(SYSTEM) as session:
        await claims.record_identity_result(session, SYSTEM, api.clock, client_id, matched=True)
    reported = await api.http.post(
        "/client/claim/problem",
        json={"description": "One investment is missing."},
        headers=bearer,
    )
    assert reported.status_code == 200
    assert reported.json()["problem_reported"] is True


async def test_a_bad_link_and_a_claimed_account_get_the_same_answer(api: Api) -> None:
    phone, _, token = await invited(api)
    started = await start(api, token)
    [code] = await texted(api, phone)
    assert (await complete(api, token, started.json()["challenge_id"], code)).status_code == 200

    spent = refusal(await start(api, token))
    unknown = refusal(await start(api, "A" * 43))
    malformed = refusal(await start(api, "not a token"))
    assert spent == unknown == malformed
    assert spent[0] == 404
    assert spent[1]["code"] == "not_found"


async def test_a_link_that_expires_before_the_code_is_entered_changes_nothing(api: Api) -> None:
    phone, client_id, token = await invited(api)
    started = await start(api, token)
    [code] = await texted(api, phone)
    api.clock.advance(hours=72)

    late = await complete(api, token, started.json()["challenge_id"], code)
    assert refusal(late)[0] == 404
    async with api.worker.actor_transaction(SYSTEM) as session:
        view = await claims.claim_for(session, client_id)
    assert view is not None and view.status is ClaimStatus.INVITED


async def test_a_link_for_one_account_does_not_finish_another(api: Api) -> None:
    phone, _, token = await invited(api)
    other_phone, other_id, other_token = await invited(api)
    await start(api, token)
    started = await start(api, other_token)
    [other_code] = await texted(api, other_phone)

    # The other account's code with this account's link: refused before anything changes.
    crossed = await complete(api, token, started.json()["challenge_id"], other_code)
    assert refusal(crossed)[0] == 404
    async with api.worker.actor_transaction(SYSTEM) as session:
        view = await claims.claim_for(session, other_id)
    assert view is not None and view.status is ClaimStatus.INVITED
    assert len(await texted(api, phone)) == 1
    # Its owner can still claim it with its own link and code.
    mine = await complete(api, other_token, started.json()["challenge_id"], other_code)
    assert mine.status_code == 200, mine.text


async def test_a_client_who_signed_up_in_the_app_has_no_claim_and_no_404(api: Api) -> None:
    client_id = await api.identity.create_client(phone=a_phone(), email=None, password=PASSWORD)
    issued = await api.identity.issue_session(
        Actor("client", client_id), second_step="passkey", transport="token"
    )
    mine = await api.http.get("/client/claim", headers={"Authorization": f"Bearer {issued.token}"})
    # Every app start asks. Most clients have no claim, which is an answer, not an error.
    assert mine.status_code == 200, mine.text
    assert mine.json() is None


async def test_signing_in_is_needed_to_see_a_claim(api: Api) -> None:
    assert refusal(await api.http.get("/client/claim"))[0] == 401
    problem = await api.http.post("/client/claim/problem", json={"description": "x"})
    assert refusal(problem)[0] == 401


async def test_a_claim_without_agreeing_to_the_terms_changes_nothing(api: Api) -> None:
    phone, _, token = await invited(api)
    started = await start(api, token)
    [code] = await texted(api, phone)
    body = {
        "token": token,
        "challenge_id": started.json()["challenge_id"],
        "code": code,
        "password": PASSWORD,
    }
    refused = await api.http.post("/client/claim/complete", json=body)
    assert (refused.status_code, refused.json()["error"]["code"]) == (422, "validation_failed")
    # The code was not spent, so ticking the box finishes the claim.
    done = await api.http.post("/client/claim/complete", json={**body, "accepted_terms": True})
    assert done.status_code == 200, done.text
