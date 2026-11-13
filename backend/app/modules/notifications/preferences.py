"""A client's choice of push, SMS and email for each kind of alert."""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.domain.events import Category, Channel
from app.modules.notifications.domain.preferences import (
    CHANNELS,
    InvalidPreference,
    check,
    effective,
)
from app.shared.actors import Actor
from app.shared.errors import ApplicationError, FieldProblem

_SAVED = text("SELECT category, channels FROM notification_preferences WHERE client_id = :id")
_SAVE = text(
    "INSERT INTO notification_preferences (client_id, category, channels, updated_at) "
    "VALUES (:id, :category, CAST(:channels AS text[]), :now) "
    "ON CONFLICT ON CONSTRAINT pk_notification_preferences "
    "DO UPDATE SET channels = EXCLUDED.channels, updated_at = EXCLUDED.updated_at"
)


async def channels_for_client(
    session: AsyncSession, client_id: str
) -> dict[Category, frozenset[Channel]]:
    """Every kind of alert with the channels in force for this client."""
    rows = (await session.execute(_SAVED, {"id": client_id})).all()
    saved = {
        Category(row.category): frozenset(Channel(channel) for channel in row.channels)
        for row in rows
    }
    return effective(saved)


async def save(
    session: AsyncSession,
    client: Actor,
    choices: dict[Category, frozenset[Channel]],
    now: datetime,
) -> dict[Category, frozenset[Channel]]:
    """Save the client's choice for the kinds of alert named, and leave the rest as they are."""
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    problems = []
    for category, channels in choices.items():
        try:
            check(category, channels)
        except InvalidPreference as error:
            problems.append(FieldProblem(field_path=[category.value], code=error.code))
    if problems:
        raise ApplicationError("validation_failed", fields=problems)
    for category, channels in choices.items():
        await session.execute(
            _SAVE,
            {
                "id": client.id,
                "category": category.value,
                # Stored in one order, so equal choices are equal rows.
                "channels": [channel.value for channel in CHANNELS if channel in channels],
                "now": now,
            },
        )
    return await channels_for_client(session, client.id)
