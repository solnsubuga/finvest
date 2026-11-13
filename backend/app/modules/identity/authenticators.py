"""An authenticator app (TOTP) as a member of staff's second step, in place of a passkey.

Staff choose a passkey or an authenticator app when they enrol. Unlike a passkey, the server
must hold the app's secret to check a code. It is stored encrypted (AES-256-GCM) with
`FINVEST_AUTHENTICATOR_KEY`, which lives in the API's environment and never in the database,
so a copy of the database alone cannot produce codes. Without the key the option is off.
"""

import base64
import os
from dataclasses import dataclass
from datetime import datetime

import structlog
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.settings import Settings
from app.infrastructure.throttling import clear_attempts, reserve_attempts, throttle_key
from app.modules.identity.domain import totp
from app.modules.identity.passkeys import PasskeyService, check_enrolment_password
from app.modules.identity.service import IdentityService, IssuedSession
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.push_ids import push_id

# Wrong codes allowed in fifteen minutes. Six digits leave a guesser no real chance at this rate.
CODES_PER_ACCOUNT = 5
SETUPS_PER_ENROLMENT = 10
_NONCE_BYTES = 12

logger = structlog.get_logger()


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
        self._cipher = (
            AESGCM(base64.urlsafe_b64decode(key.get_secret_value())) if key is not None else None
        )

    @property
    def enabled(self) -> bool:
        return self._cipher is not None

    def _require(self) -> AESGCM:
        if self._cipher is None:
            raise ApplicationError("unavailable")
        return self._cipher

    # The row's ID is bound into the encryption, so a sealed secret copied onto another row
    # does not open.
    def _seal(self, secret: bytes, row_id: str) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return nonce + self._require().encrypt(nonce, secret, row_id.encode())

    def _open(self, sealed: bytes, row_id: str) -> bytes:
        try:
            return self._require().decrypt(
                sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:], row_id.encode()
            )
        except InvalidTag:
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
        secret = totp.new_secret()
        row_id = push_id(self.clock)
        now = self.clock.now()
        async with self.database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE identity_authenticators SET removed_at = :now "
                    "WHERE bound_to = :enrolment AND confirmed_at IS NULL AND removed_at IS NULL"
                ),
                {"now": now, "enrolment": enrolment.id},
            )
            await session.execute(
                text(
                    "INSERT INTO identity_authenticators (id, actor_kind, actor_id, "
                    "secret_sealed, bound_to, created_at) "
                    "VALUES (:id, 'staff', :staff, :sealed, :enrolment, :now)"
                ),
                {
                    "id": row_id,
                    "staff": enrolment.staff_id,
                    "sealed": self._seal(secret, row_id),
                    "enrolment": enrolment.id,
                    "now": now,
                },
            )
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

    async def sign_in(self, login_id: str, code: str) -> IssuedSession:
        """Check a code from the app and open the session the password step started."""
        self._require()
        actor = await self.enrolments.login_actor(login_id, "staff")
        assert actor.id is not None
        # Counted per sign-in attempt as well as per account, before the code is looked at.
        await self._count_attempt(actor.id, login_id)
        async with self.database.transaction() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, secret_sealed, last_step FROM identity_authenticators "
                        "WHERE actor_kind = 'staff' AND actor_id = :staff "
                        "AND confirmed_at IS NOT NULL AND removed_at IS NULL"
                    ),
                    {"staff": actor.id},
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
        issued = await self.identity.open_session(
            login_id, "staff", second_step="authenticator", transport="cookie"
        )
        await self._clear_attempts(actor.id)
        return issued

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
