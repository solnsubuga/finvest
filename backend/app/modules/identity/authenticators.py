"""An authenticator app (TOTP) as a second step of sign-in, for clients and staff.

Staff choose a passkey or an authenticator app when they enrol. A signed-in client can add
one from their security settings. Unlike a passkey, the server
must hold the app's secret to check a code. It is sealed (`app/infrastructure/sealing.py`) with
`FINVEST_AUTHENTICATOR_KEY`, which lives in the API's environment and never in the database,
so a copy of the database alone cannot produce codes. Without the key the option is off.
"""

import base64
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.sealing import Sealer, SealError, associated_data, key_from_text
from app.infrastructure.settings import Settings
from app.infrastructure.throttling import clear_attempts, reserve_attempts, throttle_key
from app.modules.identity.domain import totp
from app.modules.identity.domain.sessions import Transport
from app.modules.identity.passkeys import (
    RECENT_SIGN_IN,
    PasskeyService,
    check_enrolment_password,
)
from app.modules.identity.service import (
    AccountKind,
    ClientSession,
    IdentityService,
    IssuedSession,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id

# Wrong codes allowed in fifteen minutes. Six digits leave a guesser no real chance at this rate.
CODES_PER_ACCOUNT = 5
SETUPS_PER_ENROLMENT = 10

logger = structlog.get_logger()


def _place(row_id: str) -> bytes:
    return associated_data("identity_authenticators", "secret_sealed", row_id)


@dataclass(frozen=True, slots=True)
class AuthenticatorSetup:
    """Shown once while enrolling: scan `uri` as a QR code, or type `secret` into the app."""

    secret: str
    uri: str


class AuthenticatorService:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        settings: Settings,
        identity: IdentityService,
        enrolments: PasskeyService,
    ) -> None:
        self.database = database
        self.clock = clock
        self.identity = identity
        self.enrolments = enrolments
        key = settings.authenticator_key
        self._sealer = (
            Sealer(key_from_text(key.get_secret_value()), purpose="authenticator")
            if key is not None
            else None
        )

    @property
    def enabled(self) -> bool:
        return self._sealer is not None

    def _require(self) -> Sealer:
        if self._sealer is None:
            raise ApplicationError("unavailable")
        return self._sealer

    # Where the secret lives is bound into the encryption, so a sealed secret copied onto
    # another row does not open.
    def _seal(self, secret: bytes, row_id: str) -> bytes:
        return self._require().seal(base64.b64encode(secret).decode(), _place(row_id))

    def _open(self, sealed: bytes, row_id: str) -> bytes:
        try:
            return base64.b64decode(self._require().open(sealed, _place(row_id)))
        except SealError:
            # The key changed, or the row was tampered with. Nothing can be checked against it.
            logger.error("authenticator_unreadable", error_code="unavailable")
            raise ApplicationError("unavailable") from None

    # Enrolment

    async def enrolment_setup(self, token: str, *, address: str | None) -> AuthenticatorSetup:
        """Make a new secret for this enrolment link. An earlier unfinished one is dropped."""
        self._require()
        enrolment = await self.enrolments.enrolment(token, address=address)
        await reserve_attempts(
            self.database,
            self.clock,
            [(throttle_key("authenticator-setup", enrolment.id), SETUPS_PER_ENROLMENT)],
        )
        secret = await self._pending(Actor("staff", enrolment.staff_id), enrolment.id)
        return AuthenticatorSetup(totp.secret_text(secret), totp.setup_uri(secret, enrolment.email))

    async def enrol(
        self, token: str, password: str, code: str, *, address: str | None
    ) -> IssuedSession:
        """Finish enrolment with a code from the app, proving it was set up correctly."""
        self._require()
        enrolment = await self.enrolments.enrolment(token, address=address)
        check_enrolment_password(enrolment, password)
        await self._count_attempt(enrolment.staff_id)
        async with self.database.transaction() as session:
            pending = (
                await session.execute(
                    text(
                        "SELECT id, secret_sealed FROM identity_authenticators "
                        "WHERE bound_to = :enrolment AND confirmed_at IS NULL "
                        "AND removed_at IS NULL"
                    ),
                    {"enrolment": enrolment.id},
                )
            ).one_or_none()
        if pending is None:
            raise ApplicationError("expired")
        secret = self._open(bytes(pending.secret_sealed), pending.id)
        step = totp.matching_step(secret, code, self.clock.now(), None)
        if step is None:
            raise self._wrong_code()

        async def add(session: AsyncSession, now: datetime) -> None:
            # Enrolment has just marked every earlier second step removed, this one included.
            await session.execute(
                text(
                    "UPDATE identity_authenticators SET confirmed_at = :now, removed_at = NULL, "
                    "last_step = :step WHERE id = :id"
                ),
                {"now": now, "step": step, "id": pending.id},
            )

        issued = await self.enrolments.finish_enrolment(enrolment, password, "authenticator", add)
        await self._clear_attempts(enrolment.staff_id)
        return issued

    # The second step of sign-in

    async def sign_in(
        self, login_id: str, code: str, kind: AccountKind = "staff", transport: Transport = "cookie"
    ) -> IssuedSession:
        """Check a code from the app and open the session the password step started."""
        self._require()
        actor = await self.enrolments.login_actor(login_id, kind)
        assert actor.id is not None
        # Counted per sign-in attempt as well as per account, before the code is looked at.
        await self._count_attempt(actor.id, login_id)
        await self._accept(actor, code)
        issued = await self.identity.open_session(
            login_id, kind, second_step="authenticator", transport=transport
        )
        await self._clear_attempts(actor.id)
        return issued

    async def _accept(self, actor: Actor, code: str) -> None:
        """Accept `code` for this person's confirmed app once, or raise `invalid_code`."""
        async with self.database.transaction() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, secret_sealed, last_step FROM identity_authenticators "
                        "WHERE actor_kind = :kind AND actor_id = :actor "
                        "AND confirmed_at IS NOT NULL AND removed_at IS NULL"
                    ),
                    {"kind": actor.kind, "actor": actor.id},
                )
            ).one_or_none()
        if row is None:
            raise self._wrong_code()
        secret = self._open(bytes(row.secret_sealed), row.id)
        step = totp.matching_step(secret, code, self.clock.now(), row.last_step)
        if step is None:
            raise self._wrong_code()
        async with self.database.transaction() as session:
            # Two requests with the same code race here, and only one moves the step forward.
            first = await session.scalar(
                text(
                    "UPDATE identity_authenticators SET last_step = :step WHERE id = :id "
                    "AND removed_at IS NULL AND (last_step IS NULL OR last_step < :step) "
                    "RETURNING true"
                ),
                {"step": step, "id": row.id},
            )
        if not first:
            raise self._wrong_code()

    # A signed-in client's authenticator app

    def _recent(self, client: ClientSession) -> None:
        if self.clock.now() - client.signed_in_at > RECENT_SIGN_IN:
            raise ApplicationError("sign_in_again")

    async def client_has(self, client: ClientSession) -> bool:
        async with self.database.transaction() as session:
            return bool(
                await session.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM identity_authenticators WHERE actor_kind "
                        "= 'client' AND actor_id = :actor AND confirmed_at IS NOT NULL "
                        "AND removed_at IS NULL)"
                    ),
                    {"actor": client.client_id},
                )
            )

    async def client_setup(self, client: ClientSession) -> AuthenticatorSetup:
        """Make a new secret for this session. It replaces nothing until a code confirms it."""
        self._require()
        self._recent(client)
        await reserve_attempts(
            self.database,
            self.clock,
            [(throttle_key("authenticator-setup", client.session_id), SETUPS_PER_ENROLMENT)],
        )
        secret = await self._pending(client.actor, client.session_id)
        return AuthenticatorSetup(totp.secret_text(secret), totp.setup_uri(secret, client.phone))

    async def client_confirm(self, client: ClientSession, code: str) -> None:
        """Turn the app on with a code from it. An app the client had before stops working."""
        self._require()
        self._recent(client)
        await self._count_attempt(client.client_id)
        async with self.database.transaction() as session:
            pending = (
                await session.execute(
                    text(
                        "SELECT id, secret_sealed FROM identity_authenticators "
                        "WHERE bound_to = :session AND actor_kind = 'client' "
                        "AND actor_id = :actor AND confirmed_at IS NULL AND removed_at IS NULL"
                    ),
                    {"session": client.session_id, "actor": client.client_id},
                )
            ).one_or_none()
        if pending is None:
            raise ApplicationError("expired")
        secret = self._open(bytes(pending.secret_sealed), pending.id)
        now = self.clock.now()
        step = totp.matching_step(secret, code, now, None)
        if step is None:
            raise self._wrong_code()
        async with self.database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE identity_authenticators SET removed_at = :now WHERE actor_kind = "
                    "'client' AND actor_id = :actor AND confirmed_at IS NOT NULL "
                    "AND removed_at IS NULL"
                ),
                {"now": now, "actor": client.client_id},
            )
            await session.execute(
                text(
                    "UPDATE identity_authenticators SET confirmed_at = :now, last_step = :step "
                    "WHERE id = :id AND removed_at IS NULL"
                ),
                {"now": now, "step": step, "id": pending.id},
            )
        await self._clear_attempts(client.client_id)
        logger.info("authenticator_added", kind="client")

    async def client_remove(self, client: ClientSession) -> None:
        self._recent(client)
        async with self.database.transaction() as session:
            removed = await session.scalar(
                text(
                    "UPDATE identity_authenticators SET removed_at = :now WHERE actor_kind = "
                    "'client' AND actor_id = :actor AND confirmed_at IS NOT NULL "
                    "AND removed_at IS NULL RETURNING true"
                ),
                {"now": self.clock.now(), "actor": client.client_id},
            )
        if not removed:
            raise ApplicationError("not_found")
        logger.info("authenticator_removed", kind="client")

    async def _pending(self, actor: Actor, bound_to: str) -> bytes:
        """Store a new unconfirmed secret for `bound_to`, dropping an earlier unfinished one."""
        secret = totp.new_secret()
        row_id = push_id(self.clock)
        now = self.clock.now()
        async with self.database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE identity_authenticators SET removed_at = :now "
                    "WHERE bound_to = :bound AND confirmed_at IS NULL AND removed_at IS NULL"
                ),
                {"now": now, "bound": bound_to},
            )
            await session.execute(
                text(
                    "INSERT INTO identity_authenticators (id, actor_kind, actor_id, "
                    "secret_sealed, bound_to, created_at) "
                    "VALUES (:id, :kind, :actor, :sealed, :bound, :now)"
                ),
                {
                    "id": row_id,
                    "kind": actor.kind,
                    "actor": actor.id,
                    "sealed": self._seal(secret, row_id),
                    "bound": bound_to,
                    "now": now,
                },
            )
        return secret

    async def _count_attempt(self, staff_id: str, login_id: str | None = None) -> None:
        keys = [(throttle_key("authenticator-code", staff_id), CODES_PER_ACCOUNT)]
        if login_id is not None:
            keys.append((throttle_key("authenticator-login", login_id), CODES_PER_ACCOUNT))
        await reserve_attempts(self.database, self.clock, keys)

    async def _clear_attempts(self, staff_id: str) -> None:
        await clear_attempts(self.database, [throttle_key("authenticator-code", staff_id)])

    def _wrong_code(self) -> ApplicationError:
        logger.info("authenticator_refused", error_code="invalid_code")
        return ApplicationError("invalid_code")
