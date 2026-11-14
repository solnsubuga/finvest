"""The app PIN: set it on a phone app session, and open a locked session with it.

A session of the phone app with a PIN locks after `LOCK_AFTER` unused (see `_touch`), and then
answers nothing but the PIN, `GET /client/session` and signing out. Each check charges a wrong
try before the hash is compared, in one UPDATE, so parallel guesses cannot pass the limit. The
fifth wrong PIN in a row ends the session, and the client logs in again with their password and
a second step.

Some actions on the phone app ask for the PIN again (see `CONFIRMATION_LASTS`): `confirm` checks
it the same way and gives a one-use confirmation for that action, which the action's own route
spends with `spend`. A browser has no PIN, so nothing there asks for one.
"""

from dataclasses import dataclass
from typing import NamedTuple

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.identity import trail
from app.modules.identity.domain.pins import (
    CONFIRMATION_LASTS,
    MAX_WRONG_PINS,
    PinAction,
    pin_problem,
)
from app.modules.identity.domain.sessions import new_token, token_digest
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.passkeys import RECENT_SIGN_IN
from app.modules.identity.service import ClientSession
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class PinCheck:
    """`unlocked` when the PIN was right. Otherwise how many tries are left before sign-out."""

    unlocked: bool
    tries_left: int


@dataclass(frozen=True, slots=True)
class PinConfirmation:
    """A right PIN gives `confirmation`, to send with the action. Otherwise the tries left."""

    confirmed: bool
    tries_left: int
    confirmation: str | None = None


class _Checked(NamedTuple):
    right: bool
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
        confirmed = client.confirmed_at is not None and now - client.confirmed_at <= RECENT_SIGN_IN
        if now - client.signed_in_at > RECENT_SIGN_IN and not confirmed:
            # A new PIN follows a fresh log in, as a forgotten one does, or a passkey or app
            # used again on this session to change it.
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
        checked = await self._check(client, pin)
        if not checked.right:
            return PinCheck(unlocked=False, tries_left=checked.tries_left)
        async with self.database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE identity_sessions SET last_seen_at = :now "
                    "WHERE id = :id AND revoked_at IS NULL"
                ),
                {"now": self.clock.now(), "id": client.session_id},
            )
        logger.info("pin_unlocked")
        return PinCheck(unlocked=True, tries_left=MAX_WRONG_PINS)

    def needs_confirmation(self, client: ClientSession) -> bool:
        """Whether actions in this session are confirmed with the PIN: a phone app with one."""
        return client.transport == "token" and client.pin_set

    async def confirm(self, client: ClientSession, pin: str, action: PinAction) -> PinConfirmation:
        """Check the PIN for one action. Right gives a one-use confirmation for it."""
        if not self.needs_confirmation(client):
            raise ApplicationError("conflict")
        checked = await self._check(client, pin)
        if not checked.right:
            return PinConfirmation(confirmed=False, tries_left=checked.tries_left)
        now = self.clock.now()
        token = new_token()
        async with self.database.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO identity_pin_confirmations "
                    "(id, session_id, action, token_hash, created_at, expires_at) "
                    "VALUES (:id, :session, :action, :hash, :now, :expires)"
                ),
                {
                    "id": push_id(self.clock),
                    "session": client.session_id,
                    "action": action,
                    "hash": token_digest(token),
                    "now": now,
                    "expires": now + CONFIRMATION_LASTS,
                },
            )
            await trail.note(
                session,
                self.clock,
                client.actor,
                "identity.pin.confirmed",
                resource_type="session",
                resource_id=client.session_id,
                details={"action": action},
            )
        logger.info("pin_confirmed", action=action)
        return PinConfirmation(confirmed=True, tries_left=MAX_WRONG_PINS, confirmation=token)

    async def spend(
        self,
        client: ClientSession,
        action: PinAction,
        confirmation: str | None,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        """Use up the confirmation an action needs. Refuses with `pin_required` without one.

        Nothing is asked of a session that confirms nothing with a PIN (a browser, or a phone
        with no PIN yet). A confirmation works once, for its action, from its own session.

        Pass the action's own `session` so the confirmation is used up only if the action
        commits: a failed action, or a retry of the same idempotency key after a timeout, then
        finds it still there. Call it after the idempotency replay check, so a replay that
        answers the earlier result asks for nothing.
        """
        if not self.needs_confirmation(client):
            return
        spent = None
        if confirmation:
            if session is not None:
                spent = await self._use(session, client, action, confirmation)
            else:
                async with self.database.transaction() as own:
                    spent = await self._use(own, client, action, confirmation)
        if spent is None:
            logger.info("pin_confirmation_refused", error_code="pin_required", action=action)
            raise ApplicationError("pin_required")

    async def _use(
        self, session: AsyncSession, client: ClientSession, action: PinAction, confirmation: str
    ) -> str | None:
        spent: str | None = await session.scalar(
            text(
                "UPDATE identity_pin_confirmations SET used_at = :now "
                "WHERE token_hash = :hash AND session_id = :session AND action = :action "
                "AND used_at IS NULL AND expires_at > :now RETURNING id"
            ),
            {
                "now": self.clock.now(),
                "hash": token_digest(confirmation),
                "session": client.session_id,
                "action": action,
            },
        )
        return spent

    async def _check(self, client: ClientSession, pin: str) -> _Checked:
        """Charge a try, then compare. The fifth wrong PIN in a row ends the session (401)."""
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
            return _Checked(right=True, tries_left=MAX_WRONG_PINS)

        left = MAX_WRONG_PINS - charged.wrong_tries
        logger.info("pin_refused", error_code="invalid_pin", tries_left=left)
        if left > 0:
            return _Checked(right=False, tries_left=left)
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
