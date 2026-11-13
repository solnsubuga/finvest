"""A new client signs up, secures the account with an authenticator app and passes KYC."""

import pytest

from app.infrastructure.database import Database
from tests.journeys.onboarding import (
    add_authenticator,
    pass_kyc,
    sign_in_with_authenticator,
    sign_up,
)
from tests.support.books import audit_for, missing
from tests.support.journey import World

pytestmark = pytest.mark.journey


async def test_a_client_signs_up_secures_the_account_and_passes_kyc(
    world: World, migrator_database: Database
) -> None:
    client = await sign_up(world)
    session = await client.http.get("/client/session")
    assert session.status_code == 200 and session.json()["client_id"] == client.id

    await add_authenticator(world, client)
    await sign_in_with_authenticator(world, client)
    reviewer = await world.staff("compliance")
    check_id = await pass_kyc(world, client, reviewer)

    trail = await audit_for(migrator_database, [client.id, check_id])
    assert (
        missing(
            trail,
            "identity.client.created",
            "identity.session.opened",
            "identity.authenticator.added",
            "kyc.check.submitted",
            "kyc.check.approved",
        )
        == []
    )
    approved = next(row for row in trail if row.action == "kyc.check.approved")
    assert (approved.actor_kind, approved.actor_id) == ("staff", reviewer.id)
