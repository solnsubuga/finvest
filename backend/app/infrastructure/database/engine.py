import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from asyncpg import PostgresError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.settings import Settings
from app.shared.actors import Actor

ACTOR_KIND_SETTING = "finvest.actor_kind"
ACTOR_ID_SETTING = "finvest.actor_id"


async def apply_actor(session: AsyncSession, actor: Actor) -> None:
    """Tell PostgreSQL who this transaction acts for, so row policies can fence its rows.

    `set_config(..., true)` is transaction-local: PostgreSQL discards it at commit or rollback,
    so a pooled connection never carries one request's actor into the next.
    """
    if not session.in_transaction():
        raise RuntimeError("The actor must be applied inside a transaction")
    await session.execute(
        text("SELECT set_config(:kind_setting, :kind, true), set_config(:id_setting, :id, true)"),
        {
            "kind_setting": ACTOR_KIND_SETTING,
            "kind": actor.kind,
            "id_setting": ACTOR_ID_SETTING,
            "id": actor.id or "",
        },
    )


class Database:
    """One bounded pool per process, closed when the process stops."""

    def __init__(self, settings: Settings) -> None:
        self.timeout = settings.database_timeout_seconds
        self.engine = create_async_engine(
            settings.database_url.get_secret_value(),
            pool_size=settings.database_pool_size,
            max_overflow=0,
            pool_timeout=self.timeout,
            pool_pre_ping=True,
            connect_args={
                **settings.database_connect_args(),
                "timeout": self.timeout,
                "command_timeout": self.timeout,
            },
            # Bound parameters can be personal data; keep them out of error text and logs.
            hide_parameters=True,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """One session and one transaction with no actor: row policies show it nothing.

        Commits when the block ends and rolls back if it raises. Never share the session between
        tasks, and never call a provider while it is open.
        """
        async with self.sessions() as session, session.begin():
            yield session

    @asynccontextmanager
    async def actor_transaction(self, actor: Actor) -> AsyncIterator[AsyncSession]:
        """A transaction acting for one client, one staff member or the system."""
        async with self.transaction() as session:
            await apply_actor(session, actor)
            yield session

    async def ready(self) -> bool:
        try:
            async with asyncio.timeout(self.timeout), self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except (SQLAlchemyError, PostgresError, OSError, TimeoutError) as error:
            # The probe only answers 503; this records why, by exception type alone.
            structlog.get_logger().warning("database_not_ready", error_type=type(error).__name__)
            return False

    async def close(self) -> None:
        await self.engine.dispose()
