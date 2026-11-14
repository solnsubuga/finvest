"""The app PIN: set it on a phone app session, and open a locked session with it.

A session of the phone app with a PIN locks after `LOCK_AFTER` unused (see `_touch`), and then
answers nothing but the PIN, `GET /client/session` and signing out. Each check charges a wrong
try before the hash is compared, in one UPDATE, so parallel guesses cannot pass the limit. The
fifth wrong PIN in a row ends the session, and the client logs in again with their password and
a second step.
"""

from dataclasses import dataclass

import structlog
from sqlalchemy import text

from app.infrastructure.database import Database
from app.modules.identity import trail
from app.modules.identity.domain.pins import MAX_WRONG_PINS, pin_problem
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.passkeys import RECENT_SIGN_IN
from app.modules.identity.service import ClientSession
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class PinCheck:
    """`unlocked` when the PIN was right. Otherwise how many tries are left before sign-out."""

    unlocked: bool
    tries_left: int


class PinService:
    def __init__(self, database: Database, clock: Clock, hasher: PasswordHasher) -> None:
        self.database = database
        self.clock = clock
        self.hasher = hasher

    async def set_pin(self, client: ClientSession, pin: str) -> None:
        """Set or replace this session's PIN. Only the phone app, soon after logging in."""
        if client.transport != "token":
            # A browser is shared and has no PIN. Its session ends on its own timers.
            raise ApplicationError("forbidden")
        problem = pin_problem(pin)
        if problem is not None:
            raise ApplicationError(
                "validation_failed", fields=[FieldProblem(field_path=["pin"], code=problem)]
            )
        now = self.clock.now()
        if now - client.signed_in_at > RECENT_SIGN_IN:
            # A new PIN follows a fresh log in, as a forgotten one does.
            raise ApplicationError("sign_in_again")
        hashed = await self.hasher.hash(pin)
        async with self.database.transaction() as session:
            replaced = await session.scalar(
                text(
                    "INSERT INTO identity_app_pins (session_id, pin_hash, created_at) "
                    "VALUES (:id, :hash, :now) ON CONFLICT (session_id) DO UPDATE "
                    "SET pin_hash = EXCLUDED.pin_hash, wrong_tries = 0, "
                    "created_at = EXCLUDED.created_at RETURNING (xmax <> 0)"
                ),
                {"id": client.session_id, "hash": hashed, "now": now},
            )
            # Setting the PIN is using the app, so it does not lock straight away.
            await session.execute(
                text("UPDATE identity_sessions SET last_seen_at = :now WHERE id = :id"),
                {"now": now, "id": client.session_id},
            )
            await trail.note(
                session,
                self.clock,
                client.actor,
                "identity.pin.changed" if replaced else "identity.pin.set",
                resource_type="session",
                resource_id=client.session_id,
            )
        logger.info("pin_set", replaced=bool(replaced))

    async def unlock(self, client: ClientSession, pin: str) -> PinCheck:
        """Check the PIN. Right opens the session; the fifth wrong one in a row ends it."""
        now = self.clock.now()
        async with self.database.transaction() as session:
            charged = (
                await session.execute(
                    text(
                        "UPDATE identity_app_pins SET wrong_tries = wrong_tries + 1 "
                        "WHERE session_id = :id AND wrong_tries < :limit "
                        "RETURNING pin_hash, wrong_tries"
                    ),
                    {"id": client.session_id, "limit": MAX_WRONG_PINS},
                )
            ).one_or_none()
        if charged is None:
            if not client.pin_set:
                raise ApplicationError("conflict")
            # Out of tries already: the session is ending, or ended by a parallel guess.
            await self._end(client)
            raise ApplicationError("unauthorized")

        if await self.hasher.verify(pin, charged.pin_hash):
            async with self.database.transaction() as session:
                await session.execute(
                    text("UPDATE identity_app_pins SET wrong_tries = 0 WHERE session_id = :id"),
                    {"id": client.session_id},
                )
                await session.execute(
                    text(
                        "UPDATE identity_sessions SET last_seen_at = :now "
                        "WHERE id = :id AND revoked_at IS NULL"
                    ),
                    {"now": now, "id": client.session_id},
                )
            logger.info("pin_unlocked")
            return PinCheck(unlocked=True, tries_left=MAX_WRONG_PINS)

        left = MAX_WRONG_PINS - charged.wrong_tries
        logger.info("pin_refused", error_code="invalid_pin", tries_left=left)
        if left > 0:
            return PinCheck(unlocked=False, tries_left=left)
        await self._end(client)
        raise ApplicationError("unauthorized")

    async def _end(self, client: ClientSession) -> None:
        async with self.database.transaction() as session:
            ended = await session.scalar(
                text(
                    "UPDATE identity_sessions SET revoked_at = :now, revoked_reason = 'pin_tries' "
                    "WHERE id = :id AND revoked_at IS NULL RETURNING id"
                ),
                {"now": self.clock.now(), "id": client.session_id},
            )
            if ended is not None:
                await trail.note(
                    session,
                    self.clock,
                    client.actor,
                    "identity.session.ended_after_wrong_pins",
                    resource_type="session",
                    resource_id=client.session_id,
                )
        logger.info("session_ended", reason="pin_tries")
