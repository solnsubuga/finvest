"""Payout destination steps: a client adds a number, proves it, and staff check the name."""

from app.modules.payouts import destinations
from app.modules.payouts.domain.destinations import MobileMoneyNumber
from tests.support.journey import Person, World
from tests.support.payouts import name_from_network


async def add_number(
    world: World, client: Person, number: str, holder_name: str, network_name: str
) -> str:
    """The client adds a mobile money number and confirms the texted code, and the network
    answers the lookup with `network_name`.

    Calls the payouts service as the client, and answers the lookup as the worker would from
    ioTec. Returns the destination's ID: waiting for staff if the name matches the ID,
    refused if not.
    """
    async with world.api_database.actor_transaction(client.actor) as session:
        view = await destinations.add_destination(
            session, client.actor, MobileMoneyNumber.parse(number), holder_name, world.clock
        )
        await destinations.confirm_code(session, client.actor, view.id, world.clock)
    await name_from_network(
        world.api_database,
        view.id,
        world.clock,
        registered_name=network_name,
        worker=world.worker_database,
    )
    return view.id


async def check_name(
    world: World, checker: Person, destination_id: str, proof_name: str | None = None
) -> destinations.DestinationView:
    """Staff check an account: a number with the name its network gave (no `proof_name`), a
    bank account with the name they read on the proof.

    The service reads the ID name KYC verified itself, as the console route does.
    """
    async with world.api_database.actor_transaction(checker.actor) as session:
        return await destinations.check(
            session,
            checker.actor,
            destination_id,
            proof_name=proof_name,
            statement_date=None,
            reason="Checked against the network's registered name",
            clock=world.clock,
        )


async def approve_check(
    world: World, approver: Person, destination_id: str
) -> destinations.DestinationView:
    """A second member of staff approves the check, and the client is told straight away."""
    async with world.api_database.actor_transaction(approver.actor) as session:
        approved = await destinations.approve(
            session, approver.actor, destination_id, reason="Second check", clock=world.clock
        )
    # The worker sends the notice now, as the real one would, before the journey lets the
    # 24 hour wait pass.
    await world.drain()
    return approved
