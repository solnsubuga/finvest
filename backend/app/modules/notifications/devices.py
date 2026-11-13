"""The phones a client's push notifications go to.

The app registers its Expo push token after sign-in and gives it up at sign-out. Registering
again, after an update or a new sign-in, brings a removed device back and makes this client
the one the phone belongs to now.
"""

from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.push import InvalidPush, check_token
from app.shared.actors import Actor
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

Platform = Literal["ios", "android"]
# More than this many live devices for one client is not a person's phones.
MAX_DEVICES = 10

_REGISTER = text(
    "INSERT INTO push_devices (id, client_id, token, platform, registered_at) "
    "VALUES (:id, :client_id, :token, :platform, :now) "
    "ON CONFLICT ON CONSTRAINT uq_push_devices_client_token DO UPDATE SET "
    "platform = EXCLUDED.platform, registered_at = EXCLUDED.registered_at, "
    "removed_at = NULL, removed_reason = NULL"
)
_LIVE = text(
    "SELECT count(*) FROM push_devices WHERE client_id = :client_id AND removed_at IS NULL "
    "AND token <> :token"
)
_REMOVE = text(
    "UPDATE push_devices SET removed_at = :now, removed_reason = 'signed_out' "
    "WHERE client_id = :client_id AND token = :token AND removed_at IS NULL"
)


def _client(actor: Actor) -> str:
    if actor.kind != "client" or actor.id is None:
        raise ApplicationError("forbidden")
    return actor.id


def _token(token: str) -> str:
    try:
        return check_token(token)
    except InvalidPush:
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["token"], code="invalid")]
        ) from None


async def register(
    session: AsyncSession, actor: Actor, token: str, platform: Platform, now: datetime
) -> None:
    client_id = _client(actor)
    token = _token(token)
    live = (await session.execute(_LIVE, {"client_id": client_id, "token": token})).scalar_one()
    if live >= MAX_DEVICES:
        raise ApplicationError("conflict")
    await session.execute(
        _REGISTER,
        {"id": push_id(), "client_id": client_id, "token": token, "platform": platform, "now": now},
    )


async def remove(session: AsyncSession, actor: Actor, token: str, now: datetime) -> None:
    """Stop sending to this phone. Removing a token that is not registered does nothing."""
    await session.execute(
        _REMOVE, {"client_id": _client(actor), "token": _token(token), "now": now}
    )
