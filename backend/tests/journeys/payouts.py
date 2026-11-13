"""Payout destination steps: a client adds a number, proves it, and staff check the name."""

from app.modules.kyc import service as kyc
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
    world: World, checker: Person, client: Person, destination_id: str, proof_name: str
) -> destinations.DestinationView:
    """Staff compare the name the network holds for the number with the name on the ID.

    The ID name is the one KYC verified, read from KYC, as the console route will.
    """
    async with world.api_database.actor_transaction(checker.actor) as session:
        id_name = await kyc.verified_name(session, client.id)
        assert id_name is not None
        return await destinations.approve(
            session,
            checker.actor,
            destination_id,
            id_name=id_name,
            proof_name=proof_name,
            statement_date=None,
            reason="Checked against the network's registered name",
            clock=world.clock,
        )
