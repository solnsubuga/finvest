"""A client pays for an investment by mobile money, another by bank transfer, and both start."""

from datetime import timedelta

import pytest

from app.infrastructure.database import Database
from app.modules.investments.holdings import AWAITING_START, HOLDING
from tests.journeys.gaps import expected_missing
from tests.journeys.money import (
    TrustBank,
    balance,
    invest,
    investment,
    is_active,
    pay_by_bank_transfer,
    pay_by_mobile_money,
)
from tests.journeys.onboarding import onboarded
from tests.support.books import audit_for, missing
from tests.support.journey import World

pytestmark = pytest.mark.journey


async def test_a_mobile_money_payment_starts_the_investment(
    world: World, migrator_database: Database
) -> None:
    client = await onboarded(world)
    requested = await invest(world, client, 1_000_000)

    payment = await pay_by_mobile_money(world, client, requested.id)

    assert payment["status"] == "confirmed", payment
    assert world.collections.confirmed[payment["id"]].minor == 1_000_000
    started = await investment(world, requested.id)
    assert is_active(started), started.status
    assert await balance(world, AWAITING_START, owner=client.id, reference=requested.id) == 0
    assert await balance(world, HOLDING, owner=client.id, reference=requested.id) == 1_000_000
    trail = await audit_for(migrator_database, [client.id, requested.id, payment["id"]])
    expected = (
        "payments.deposit_requested",
        "payments.deposit_confirmed",
        "investments.requested",
        "investments.started",
    )
    assert missing(trail, *expected) == expected_missing(*expected)
    assert await world.failed_jobs(world.clock.now() - timedelta(hours=1)) == []


async def test_a_bank_transfer_is_matched_by_one_person_approved_by_another_and_starts(
    world: World, migrator_database: Database, bank: TrustBank
) -> None:
    client = await onboarded(world)
    amount = 2_500_000
    requested = await invest(world, client, amount)
    importer, matcher, approver = (
        await world.staff("operations"),
        await world.staff("finance"),
        await world.staff("finance"),
    )
    # The transfer shows on a past day's statement: a day is imported once it is over.
    sent_on = await bank.receive(world, importer, f"FV{client.id[-6:]}", amount)

    payment = await pay_by_bank_transfer(
        world, client, requested.id, amount, sent_on=sent_on, matcher=matcher, approver=approver
    )

    assert payment["status"] == "confirmed", payment
    assert is_active(await investment(world, requested.id))
    assert await balance(world, HOLDING, owner=client.id, reference=requested.id) == amount
    trail = await audit_for(migrator_database, [payment["id"], requested.id])
    expected = (
        "payments.deposit_requested",
        "payments.bank_transfer_opened",
        "payments.bank_transfer_reviewed",
        "payments.bank_match_approved",
        "payments.deposit_confirmed",
        "investments.started",
    )
    assert missing(trail, *expected) == expected_missing(*expected)
    reviewed = next(row for row in trail if row.action == "payments.bank_transfer_reviewed")
    approved = next(row for row in trail if row.action == "payments.bank_match_approved")
    assert (reviewed.actor_id, approved.actor_id) == (matcher.id, approver.id)
