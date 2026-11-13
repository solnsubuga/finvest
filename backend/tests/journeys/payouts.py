"""Payout destination steps: a client adds a number, proves it, and staff check the name."""

from app.modules.payouts import destinations
from app.modules.payouts.domain.destinations import MobileMoneyNumber, ProofKind
from tests.support.journey import Person, World


async def add_number(world: World, client: Person, number: str, holder_name: str) -> str:
    """The client adds a mobile money number, confirms the texted code and uploads proof.

    Payouts have no routes yet, so this calls the payouts service as the client. Returns the
    destination's ID, now waiting for staff.
    """
    async with world.api_database.actor_transaction(client.actor) as session:
        view = await destinations.add_destination(
            session, client.actor, MobileMoneyNumber.parse(number), holder_name, world.clock
        )
        await destinations.confirm_code(session, client.actor, view.id, world.clock)
        await destinations.submit_proof(
            session,
            client.actor,
            view.id,
            ProofKind.NETWORK_REGISTERED_NAME,
            f"proof-{view.id}",
            world.clock,
        )
    return view.id


async def check_name(
    world: World, checker: Person, destination_id: str, proof_name: str
) -> destinations.DestinationView:
    """Staff compare the name the network holds for the number with the name on the ID.

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
    """A second member of staff approves the check."""
    async with world.api_database.actor_transaction(approver.actor) as session:
        return await destinations.approve(
            session, approver.actor, destination_id, reason="Second check", clock=world.clock
        )
