"""Steps every journey starts with: a client signs up, secures the account and passes KYC."""

from app.modules.kyc import service as kyc
from app.modules.kyc.providers import ManualProvider
from app.shared.actors import Actor
from tests.support.journey import (
    PASSWORD,
    Person,
    World,
    a_phone,
    authenticator_code,
    decode_secret,
)
from tests.support.kyc import SEALER, ready_draft


async def sign_up(world: World, *, phone: str | None = None, email: str | None = None) -> Person:
    """Sign up on the phone app with a texted code and a password, over HTTP.

    The code reaches the client the way it does in production: the API queues a message and
    the worker sends it through the SMS provider.
    """
    phone = phone or a_phone()
    http = world.http()
    started = await http.post("/client/signup/start", json={"phone": phone, "email": email})
    assert started.status_code == 200, started.text
    await world.drain()
    confirmed = await http.post(
        "/client/signup/confirm",
        json={"challenge_id": started.json()["challenge_id"], "code": world.texted_code(phone)},
    )
    assert confirmed.status_code == 200, confirmed.text
    completed = await http.post(
        "/client/signup/complete",
        json={"ticket": confirmed.json()["ticket"], "password": PASSWORD},
    )
    assert completed.status_code == 200, completed.text
    body = completed.json()
    http.headers["Authorization"] = f"Bearer {body['token']}"
    return Person(Actor("client", body["session"]["client_id"]), http, phone=phone, email=email)


async def add_authenticator(world: World, client: Person) -> None:
    setup = await client.http.post("/client/authenticator/options")
    assert setup.status_code == 200, setup.text
    secret = decode_secret(setup.json()["secret"])
    confirmed = await client.http.post(
        "/client/authenticator", json={"code": authenticator_code(world, secret)}
    )
    assert confirmed.status_code == 204, confirmed.text
    client.authenticator = secret


async def sign_in_with_authenticator(world: World, client: Person) -> None:
    """Sign out, then back in with the password and the authenticator app, on a new phone."""
    assert client.phone is not None and client.authenticator is not None
    assert (await client.http.post("/client/sign-out")).status_code == 204
    assert (await client.http.get("/client/session")).status_code == 401
    # A code is good for one step of thirty seconds and is never accepted twice.
    world.clock.advance(seconds=31)
    http = world.http()
    started = await http.post(
        "/client/login/start", json={"phone": client.phone, "password": PASSWORD}
    )
    assert started.status_code == 200, started.text
    assert "authenticator" in started.json()["methods"]
    verified = await http.post(
        "/client/login/authenticator/verify",
        json={
            "login_id": started.json()["login_id"],
            "code": authenticator_code(world, client.authenticator),
        },
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["session"]["client_id"] == client.id
    assert verified.json()["session"]["restricted_until"] is None
    http.headers["Authorization"] = f"Bearer {verified.json()['token']}"
    client.http = http


async def pass_kyc(world: World, client: Person, reviewer: Person) -> str:
    """The client sends their ID and photos; a member of staff checks and approves them.

    KYC has no routes yet, so both sides call the KYC service on the API's connection, as the
    routes will. Returns the check's ID.
    """
    async with world.api_database.actor_transaction(client.actor) as session:
        await ready_draft(session, client.actor, world.clock)
        check = await kyc.submit(session, SEALER, client.actor, ManualProvider(), world.clock)
    async with world.api_database.actor_transaction(reviewer.actor) as session:
        queue = await kyc.review_queue(session)
        assert check.id in [item.id for item in queue]
        await kyc.approve(session, SEALER, reviewer.actor, check.id, "Documents match", world.clock)
        assert await kyc.verified_name(session, client.id) is not None
    # The worker sends the client the approval notice, as it would at once.
    await world.drain()
    return check.id


async def onboarded(world: World, reviewer: Person | None = None, **contact: str) -> Person:
    """A client who signed up, added an authenticator app and passed KYC."""
    client = await sign_up(world, **contact)
    await add_authenticator(world, client)
    await pass_kyc(world, client, reviewer or await world.staff("compliance"))
    return client
