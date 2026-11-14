"""Passkeys (WebAuthn) as the second step of sign-in, and staff enrolment.

The device keeps the private key and proves itself by signing a fresh random challenge after
the person unlocks it with a fingerprint, face or device PIN. The server stores the public key
and a counter. A challenge works once, for five minutes, for one purpose and one person, and
is tied to the sign-in, enrolment link or session it was made for. Only its SHA-256 is stored.

Each audience has its own passkey site (`FINVEST_CLIENT_PASSKEY_SITE`,
`FINVEST_STAFF_PASSKEY_SITE`), and a response is accepted only from that audience's origins.
"""

import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import (
    parse_authentication_credential_json,
    parse_client_data_json,
    parse_registration_credential_json,
)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.infrastructure.database import Database
from app.infrastructure.settings import Settings
from app.infrastructure.throttling import reserve_attempts, throttle_key
from app.modules.identity import trail
from app.modules.identity.domain.passwords import password_problem
from app.modules.identity.domain.permissions import Role
from app.modules.identity.domain.sessions import (
    SecondStep,
    Transport,
    new_token,
    token_digest,
)
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import (
    AccountKind,
    ClientSession,
    IdentityService,
    IssuedSession,
    StaffSession,
)
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

CHALLENGE_LIFETIME = timedelta(minutes=5)
ENROLMENT_LIFETIME = timedelta(hours=72)
MOST_PASSKEYS = 10
# Adding or removing a passkey changes who can sign in for good, so a session alone is not
# enough: the person must have signed in this recently.
RECENT_SIGN_IN = timedelta(minutes=10)
# Attempts in fifteen minutes.
OPTIONS_PER_BINDING = 10
ENROLMENT_LOOKUPS_PER_ADDRESS = 30
TRANSPORTS = {transport.value for transport in AuthenticatorTransport}
Purpose = Literal["register", "sign_in", "reverify"]

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class PasskeyView:
    id: str
    name: str
    created_at: datetime
    last_used_at: datetime | None


@dataclass(frozen=True, slots=True)
class Enrolment:
    id: str
    staff_id: str
    name: str
    email: str
    role: Role


def _digest(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


def _failed() -> ApplicationError:
    return ApplicationError("passkey_failed")


class PasskeyService:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        settings: Settings,
        identity: IdentityService,
        hasher: PasswordHasher,
    ) -> None:
        self.database = database
        self.clock = clock
        self.identity = identity
        self.hasher = hasher
        self._sites: dict[AccountKind, str | None] = {
            "client": settings.client_passkey_site,
            "staff": settings.staff_passkey_site,
        }
        self._origins: dict[AccountKind, list[str]] = {
            "client": [*settings.client_allowed_origins, *settings.client_passkey_app_origins],
            "staff": list(settings.staff_allowed_origins),
        }

    def enabled(self, kind: AccountKind) -> bool:
        return self._sites[kind] is not None and bool(self._origins[kind])

    def _site(self, kind: AccountKind) -> tuple[str, list[str]]:
        site, origins = self._sites[kind], self._origins[kind]
        if site is None or not origins:
            raise ApplicationError("unavailable")
        return site, origins

    # Challenges

    async def _store_challenge(
        self, challenge: bytes, purpose: Purpose, actor: Actor, bound_to: str
    ) -> None:
        now = self.clock.now()
        async with self.database.transaction() as session:
            await session.execute(
                text("DELETE FROM identity_passkey_challenges WHERE expires_at < :stale"),
                {"stale": now - timedelta(hours=1)},
            )
            await session.execute(
                text(
                    "INSERT INTO identity_passkey_challenges (challenge_hash, purpose, "
                    "actor_kind, actor_id, bound_to, created_at, expires_at) "
                    "VALUES (:hash, :purpose, :kind, :actor, :bound, :now, :expires)"
                ),
                {
                    "hash": _digest(challenge),
                    "purpose": purpose,
                    "kind": actor.kind,
                    "actor": actor.id,
                    "bound": bound_to,
                    "now": now,
                    "expires": now + CHALLENGE_LIFETIME,
                },
            )

    async def _take_challenge(
        self, client_data_json: bytes, purpose: Purpose, actor: Actor, bound_to: str
    ) -> bytes:
        """Spend the challenge the device signed. A second use, or anyone else's, is refused."""
        try:
            challenge = parse_client_data_json(client_data_json).challenge
        except (WebAuthnException, ValueError):
            raise _failed() from None
        async with self.database.transaction() as session:
            taken = await session.scalar(
                text(
                    "UPDATE identity_passkey_challenges SET consumed_at = :now "
                    "WHERE challenge_hash = :hash AND purpose = :purpose AND actor_kind = :kind "
                    "AND actor_id = :actor AND bound_to = :bound AND consumed_at IS NULL "
                    "AND expires_at > :now RETURNING true"
                ),
                {
                    "now": self.clock.now(),
                    "hash": _digest(challenge),
                    "purpose": purpose,
                    "kind": actor.kind,
                    "actor": actor.id,
                    "bound": bound_to,
                },
            )
        if not taken:
            raise ApplicationError("expired")
        return challenge

    # Adding a passkey

    async def _registration_options(
        self, actor: Actor, bound_to: str, *, user_name: str, display_name: str
    ) -> dict[str, Any]:
        site, _ = self._site(actor.kind)  # type: ignore[arg-type]
        await reserve_attempts(
            self.database,
            self.clock,
            [(throttle_key("passkey-options", bound_to), OPTIONS_PER_BINDING)],
        )
        assert actor.id is not None
        async with self.database.transaction() as session:
            existing = await self._credentials(session, actor)
        challenge = secrets.token_bytes(32)
        options = generate_registration_options(
            rp_id=site,
            rp_name="Finvest",
            user_id=actor.id.encode(),
            user_name=user_name,
            user_display_name=display_name,
            challenge=challenge,
            timeout=int(CHALLENGE_LIFETIME.total_seconds() * 1000),
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            # The same device is not added twice.
            exclude_credentials=existing,
        )
        await self._store_challenge(challenge, "register", actor, bound_to)
        result: dict[str, Any] = json.loads(options_to_json(options))
        return result

    async def _verify_registration(
        self, actor: Actor, bound_to: str, credential: dict[str, Any]
    ) -> dict[str, Any]:
        """Check the new passkey and return the row to insert. Stores nothing."""
        site, origins = self._site(actor.kind)  # type: ignore[arg-type]
        try:
            parsed = parse_registration_credential_json(credential)
        except (WebAuthnException, ValueError):
            raise _failed() from None
        challenge = await self._take_challenge(
            parsed.response.client_data_json, "register", actor, bound_to
        )
        try:
            verified = verify_registration_response(
                credential=parsed,
                expected_challenge=challenge,
                expected_rp_id=site,
                expected_origin=origins,
                require_user_verification=True,
            )
        except (WebAuthnException, ValueError):
            logger.info("passkey_refused", kind=actor.kind, error_code="passkey_failed")
            raise _failed() from None
        transports = [
            transport.value
            for transport in parsed.response.transports or []
            if transport.value in TRANSPORTS
        ][:8]
        return {
            "id": push_id(self.clock),
            "kind": actor.kind,
            "actor": actor.id,
            "credential": verified.credential_id,
            "key": verified.credential_public_key,
            "count": verified.sign_count,
            "transports": transports,
        }

    async def _insert(
        self, session: AsyncSession, row: dict[str, Any], name: str, now: datetime
    ) -> PasskeyView:
        count = await session.scalar(
            text(
                "SELECT count(*) FROM identity_passkeys WHERE actor_kind = :kind "
                "AND actor_id = :actor AND removed_at IS NULL"
            ),
            row,
        )
        if count >= MOST_PASSKEYS:
            raise ApplicationError("conflict")
        name = name.strip()[:60] or "Passkey"
        await session.execute(
            text(
                "INSERT INTO identity_passkeys (id, actor_kind, actor_id, credential_id, "
                "public_key, sign_count, transports, name, created_at) VALUES (:id, :kind, "
                ":actor, :credential, :key, :count, CAST(:transports AS text[]), :name, :now)"
            ),
            {**row, "name": name, "now": now},
        )
        await trail.note(
            session,
            self.clock,
            Actor(row["kind"], row["actor"]),
            "identity.passkey.added",
            resource_type="passkey",
            resource_id=row["id"],
        )
        logger.info("passkey_added", kind=row["kind"])
        return PasskeyView(row["id"], name, now, None)

    async def _credentials(
        self, session: AsyncSession, actor: Actor
    ) -> list[PublicKeyCredentialDescriptor]:
        rows = await session.execute(
            text(
                "SELECT credential_id, transports FROM identity_passkeys WHERE actor_kind = :kind "
                "AND actor_id = :actor AND removed_at IS NULL ORDER BY id"
            ),
            {"kind": actor.kind, "actor": actor.id},
        )
        return [
            PublicKeyCredentialDescriptor(
                id=bytes(row.credential_id),
                transports=[AuthenticatorTransport(t) for t in row.transports] or None,
            )
            for row in rows
        ]

    # The second step of sign-in

    async def login_actor(self, login_id: str, kind: AccountKind) -> Actor:
        async with self.database.transaction() as session:
            actor_id = await session.scalar(
                text(
                    "SELECT actor_id FROM identity_login_challenges WHERE id = :id "
                    "AND actor_kind = :kind AND consumed_at IS NULL AND expires_at > :now"
                ),
                {"id": login_id, "kind": kind, "now": self.clock.now()},
            )
        if actor_id is None:
            raise ApplicationError("expired")
        return Actor(kind, actor_id)

    async def sign_in_options(self, login_id: str, kind: AccountKind) -> dict[str, Any]:
        """What the browser needs to ask this person's device to sign. Step one came first."""
        actor = await self.login_actor(login_id, kind)
        return await self._assertion_options(actor, "sign_in", login_id)

    async def _assertion_options(
        self, actor: Actor, purpose: Purpose, bound_to: str
    ) -> dict[str, Any]:
        assert actor.kind != "system"
        site, _ = self._site(actor.kind)
        await reserve_attempts(
            self.database,
            self.clock,
            [(throttle_key("passkey-options", bound_to), OPTIONS_PER_BINDING)],
        )
        async with self.database.transaction() as session:
            allowed = await self._credentials(session, actor)
        if not allowed:
            raise _failed()
        challenge = secrets.token_bytes(32)
        options = generate_authentication_options(
            rp_id=site,
            challenge=challenge,
            timeout=int(CHALLENGE_LIFETIME.total_seconds() * 1000),
            allow_credentials=allowed,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        await self._store_challenge(challenge, purpose, actor, bound_to)
        result: dict[str, Any] = json.loads(options_to_json(options))
        return result

    async def sign_in(
        self, login_id: str, kind: AccountKind, credential: dict[str, Any], transport: Transport
    ) -> IssuedSession:
        """Verify the device's signature and open the session the password step started."""
        actor = await self.login_actor(login_id, kind)
        await self._check_assertion(actor, credential, "sign_in", login_id)
        return await self.identity.open_session(
            login_id, kind, second_step="passkey", transport=transport
        )

    async def _check_assertion(
        self, actor: Actor, credential: dict[str, Any], purpose: Purpose, bound_to: str
    ) -> None:
        """Verify a signature from one of this person's passkeys over a challenge we issued."""
        assert actor.kind != "system"
        kind: AccountKind = actor.kind
        site, origins = self._site(kind)
        try:
            parsed = parse_authentication_credential_json(credential)
        except (WebAuthnException, ValueError):
            raise _failed() from None
        challenge = await self._take_challenge(
            parsed.response.client_data_json, purpose, actor, bound_to
        )
        async with self.database.transaction() as session:
            passkey = (
                await session.execute(
                    text(
                        "SELECT id, public_key, sign_count FROM identity_passkeys "
                        "WHERE credential_id = :credential AND actor_kind = :kind "
                        "AND actor_id = :actor AND removed_at IS NULL"
                    ),
                    {"credential": parsed.raw_id, "kind": kind, "actor": actor.id},
                )
            ).one_or_none()
        if passkey is None:
            # Someone else's passkey, or one that was removed.
            logger.info("passkey_refused", kind=kind, error_code="passkey_failed")
            raise _failed()
        try:
            verified = verify_authentication_response(
                credential=parsed,
                expected_challenge=challenge,
                expected_rp_id=site,
                expected_origin=origins,
                credential_public_key=bytes(passkey.public_key),
                credential_current_sign_count=passkey.sign_count,
                require_user_verification=True,
            )
        except (WebAuthnException, ValueError):
            logger.info("passkey_refused", kind=kind, error_code="passkey_failed")
            raise _failed() from None
        async with self.database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE identity_passkeys SET sign_count = :count, last_used_at = :now "
                    "WHERE id = :id"
                ),
                {"count": verified.new_sign_count, "now": self.clock.now(), "id": passkey.id},
            )

    # A member of staff confirming it is them before an approval

    async def reconfirm_options(self, staff: StaffSession) -> dict[str, Any]:
        return await self._assertion_options(staff.actor, "reverify", staff.session_id)

    async def reconfirm(self, staff: StaffSession, credential: dict[str, Any]) -> datetime:
        """Check a fresh passkey signature on this session. Returns when it stops counting."""
        await self._check_assertion(staff.actor, credential, "reverify", staff.session_id)
        return await self.identity.confirm_staff(staff, "passkey")

    # A signed-in client confirming it is them before changing how they sign in

    async def client_reconfirm_options(self, client: ClientSession) -> dict[str, Any]:
        return await self._assertion_options(client.actor, "reverify", client.session_id)

    async def client_reconfirm(self, client: ClientSession, credential: dict[str, Any]) -> datetime:
        """Check a fresh passkey signature on this session. Returns when it stops counting."""
        await self._check_assertion(client.actor, credential, "reverify", client.session_id)
        return await self.identity.confirm_client(client, "passkey")

    # A signed-in client's passkeys

    def _recent(self, client: ClientSession) -> None:
        if self.clock.now() - client.signed_in_at > RECENT_SIGN_IN:
            raise ApplicationError("sign_in_again")

    async def list(self, client: ClientSession) -> list[PasskeyView]:
        async with self.database.transaction() as session:
            rows = await session.execute(
                text(
                    "SELECT id, name, created_at, last_used_at FROM identity_passkeys "
                    "WHERE actor_kind = 'client' AND actor_id = :actor AND removed_at IS NULL "
                    "ORDER BY id"
                ),
                {"actor": client.client_id},
            )
            return [PasskeyView(r.id, r.name, r.created_at, r.last_used_at) for r in rows]

    async def client_registration_options(self, client: ClientSession) -> dict[str, Any]:
        self._recent(client)
        return await self._registration_options(
            client.actor, client.session_id, user_name=client.phone, display_name="Finvest"
        )

    async def add(
        self, client: ClientSession, credential: dict[str, Any], name: str
    ) -> PasskeyView:
        self._recent(client)
        row = await self._verify_registration(client.actor, client.session_id, credential)
        try:
            async with self.database.transaction() as session:
                return await self._insert(session, row, name, self.clock.now())
        except IntegrityError:
            raise ApplicationError("conflict") from None

    async def remove(self, client: ClientSession, passkey_id: str) -> None:
        self._recent(client)
        async with self.database.transaction() as session:
            removed = await session.scalar(
                text(
                    "UPDATE identity_passkeys SET removed_at = :now WHERE id = :id "
                    "AND actor_kind = 'client' AND actor_id = :actor AND removed_at IS NULL "
                    "RETURNING true"
                ),
                {"now": self.clock.now(), "id": passkey_id, "actor": client.client_id},
            )
            if removed:
                await trail.note(
                    session,
                    self.clock,
                    client.actor,
                    "identity.passkey.removed",
                    resource_type="passkey",
                    resource_id=passkey_id,
                )
        if not removed:
            raise ApplicationError("not_found")
        logger.info("passkey_removed", kind="client")

    # Staff enrolment

    async def create_enrolment(self, staff_id: str) -> str:
        """Make the one-time link token for a member of staff. An earlier link stops working.

        The token is returned once, to be sent to the person, and is never stored. Used for a
        new member, and again for one who lost their passkey.
        """
        async with self.database.transaction() as session:
            token = await issue_enrolment(session, self.clock, staff_id, Actor("staff", staff_id))
        logger.info("staff_enrolment_created", staff_id=staff_id)
        return token

    async def enrolment(self, token: str, *, address: str | None) -> Enrolment:
        """Who an enrolment link is for. Unknown, used and out-of-date links all look alike."""
        if address is not None:
            await reserve_attempts(
                self.database,
                self.clock,
                [(throttle_key("enrolment", address), ENROLMENT_LOOKUPS_PER_ADDRESS)],
            )
        async with self.database.transaction() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT e.id, s.id AS staff_id, s.name, s.email, s.role "
                        "FROM identity_staff_enrolments e "
                        "JOIN identity_staff s ON s.id = e.staff_id "
                        "WHERE e.token_hash = :hash AND e.consumed_at IS NULL "
                        "AND e.expires_at > :now AND s.status <> 'disabled'"
                    ),
                    {"hash": token_digest(token), "now": self.clock.now()},
                )
            ).one_or_none()
        if row is None:
            raise ApplicationError("expired")
        return Enrolment(row.id, row.staff_id, row.name, row.email, row.role)

    async def enrolment_options(self, token: str, *, address: str | None) -> dict[str, Any]:
        enrolment = await self.enrolment(token, address=address)
        return await self._registration_options(
            Actor("staff", enrolment.staff_id),
            enrolment.id,
            user_name=enrolment.email,
            display_name=enrolment.name,
        )

    async def enrol(
        self,
        token: str,
        password: str,
        credential: dict[str, Any],
        name: str,
        *,
        address: str | None,
    ) -> IssuedSession:
        """Finish enrolment: set the password, add the passkey and sign the person in.

        Passkeys, authenticator apps and sessions from before are ended, so a link sent to
        replace a lost device leaves the old one useless.
        """
        enrolment = await self.enrolment(token, address=address)
        # Checked before the device's answer is spent, so a weak password costs no retry.
        check_enrolment_password(enrolment, password)
        actor = Actor("staff", enrolment.staff_id)
        row = await self._verify_registration(actor, enrolment.id, credential)

        async def add(session: AsyncSession, now: datetime) -> None:
            await self._insert(session, row, name, now)

        return await self.finish_enrolment(enrolment, password, "passkey", add)

    async def finish_enrolment(
        self,
        enrolment: Enrolment,
        password: str,
        second_step: SecondStep,
        add: Callable[[AsyncSession, datetime], Awaitable[None]],
    ) -> IssuedSession:
        """Spend the link, replace what the person signed in with before, and sign them in.

        `add` stores the second step the caller has just verified. Everything commits together.
        """
        actor = Actor("staff", enrolment.staff_id)
        hashed = await self.hasher.hash(password)
        now = self.clock.now()
        try:
            async with self.database.transaction() as session:
                spent = await session.scalar(
                    text(
                        "UPDATE identity_staff_enrolments SET consumed_at = :now "
                        "WHERE id = :id AND consumed_at IS NULL AND expires_at > :now "
                        "RETURNING true"
                    ),
                    {"now": now, "id": enrolment.id},
                )
                if not spent:
                    raise ApplicationError("expired")
                for table in ("identity_passkeys", "identity_authenticators"):
                    await session.execute(
                        text(
                            f"UPDATE {table} SET removed_at = :now "  # noqa: S608 - fixed names
                            "WHERE actor_kind = 'staff' AND actor_id = :staff "
                            "AND removed_at IS NULL"
                        ),
                        {"now": now, "staff": enrolment.staff_id},
                    )
                await session.execute(
                    text(
                        "UPDATE identity_sessions SET revoked_at = :now, revoked_reason = "
                        "'enrolled' WHERE actor_kind = 'staff' AND actor_id = :staff "
                        "AND revoked_at IS NULL"
                    ),
                    {"now": now, "staff": enrolment.staff_id},
                )
                await add(session, now)
                await session.execute(
                    text(
                        "UPDATE identity_staff SET password_hash = :hash, status = 'active', "
                        "password_changed_at = :now WHERE id = :staff AND status <> 'disabled'"
                    ),
                    {"hash": hashed, "now": now, "staff": enrolment.staff_id},
                )
                # Everything the person signed in with before is gone, and this replaces it.
                await trail.note(
                    session,
                    self.clock,
                    actor,
                    "identity.staff.enrolled",
                    resource_type="staff",
                    resource_id=enrolment.staff_id,
                    details={"second_step": second_step},
                )
                issued = await self.identity.issue_in(session, actor, second_step, "cookie", now)
        except IntegrityError:
            raise ApplicationError("conflict") from None
        logger.info("staff_enrolled", staff_id=enrolment.staff_id, second_step=second_step)
        return issued


def check_enrolment_password(enrolment: Enrolment, password: str) -> None:
    problem = password_problem(password, (enrolment.email, enrolment.name))
    if problem is not None:
        raise ApplicationError(
            "leaked_password" if problem == "leaked_password" else "validation_failed",
            fields=[FieldProblem(field_path=["body", "password"], code=problem)],
        )


async def issue_enrolment(session: AsyncSession, clock: Clock, staff_id: str, actor: Actor) -> str:
    """Make a one-time link token in the caller's transaction, recorded as done by `actor`.

    An earlier link for the person stops working. The token is returned once and never stored.
    The command line issues a link as the person it is for; an approved team change issues it
    as the admin who approved it.
    """
    token = new_token()
    now = clock.now()
    known = await session.scalar(
        text("SELECT true FROM identity_staff WHERE id = :id AND status <> 'disabled'"),
        {"id": staff_id},
    )
    if not known:
        raise ApplicationError("not_found")
    await session.execute(
        text(
            "UPDATE identity_staff_enrolments SET consumed_at = :now "
            "WHERE staff_id = :staff AND consumed_at IS NULL"
        ),
        {"now": now, "staff": staff_id},
    )
    # A link lets its holder replace the person's password and second step, so every one
    # issued is on the record, and it says whether the person had already enrolled.
    enrolled = await session.scalar(
        text("SELECT status = 'active' FROM identity_staff WHERE id = :id"), {"id": staff_id}
    )
    await trail.note(
        session,
        clock,
        actor,
        "identity.staff.enrolment_link_issued",
        resource_type="staff",
        resource_id=staff_id,
        details={"replaces_existing_sign_in": bool(enrolled)},
    )
    await session.execute(
        text(
            "INSERT INTO identity_staff_enrolments (id, staff_id, token_hash, "
            "created_at, expires_at) VALUES (:id, :staff, :hash, :now, :expires)"
        ),
        {
            "id": push_id(clock),
            "staff": staff_id,
            "hash": token_digest(token),
            "now": now,
            "expires": now + ENROLMENT_LIFETIME,
        },
    )
    return token
