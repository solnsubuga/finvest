"""Accounts, the first step of sign-in, and sessions.

Sign-in is two steps. `start_login` checks the mobile number (or staff email) and password and
returns a login challenge. It never opens a session. A second step, a passkey or for clients a
code, turns the challenge into a session with `open_session`.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.settings import Settings
from app.infrastructure.throttling import clear_attempts, reserve_attempts, throttle_key
from app.modules.identity import trail
from app.modules.identity.devices import current_device
from app.modules.identity.domain.accounts import InvalidIdentifier, normalize_email, normalize_phone
from app.modules.identity.domain.devices import UNKNOWN as UNKNOWN_DEVICE
from app.modules.identity.domain.devices import kampala_time
from app.modules.identity.domain.passwords import password_problem
from app.modules.identity.domain.permissions import Permission, Role, permissions_for
from app.modules.identity.domain.pins import is_locked
from app.modules.identity.domain.sessions import (
    LOGIN_CHALLENGE_LIFETIME,
    SecondStep,
    SessionPolicy,
    Transport,
    new_token,
    restricted_until,
    token_digest,
)
from app.modules.identity.hashing import PasswordHasher
from app.modules.notifications.service import Channel, queue_message
from app.modules.platform_settings import service as platform_settings
from app.modules.platform_settings.domain.catalogue import StaffIdleMinutes
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, ErrorCode, FieldProblem
from app.shared.push_ids import push_id

AccountKind = Literal["client", "staff"]

# Wrong guesses allowed in fifteen minutes: against one account from one address, against one
# account from anywhere, and from one address against any account. The first is the tightest so
# that someone guessing from elsewhere cannot easily lock the owner out.
ACCOUNT_FROM_ADDRESS_ATTEMPTS = 5
ACCOUNT_ATTEMPTS = 20
ADDRESS_ATTEMPTS = 50
TOUCH_INTERVAL = timedelta(minutes=1)
# Approving another person's change needs a second step this recent.
CONFIRMATION_WINDOW = timedelta(minutes=10)
# A sign-in with one of these confirms it is the client. A texted or emailed code, and the code
# that proved the number at sign up, never do: whoever holds the SIM could have them.
STRONG_STEPS: frozenset[SecondStep] = frozenset({"passkey", "authenticator"})

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class LoginChallenge:
    id: str
    methods: list[SecondStep]


@dataclass(frozen=True, slots=True)
class ClientSession:
    session_id: str
    client_id: str
    phone: str
    restricted_until: datetime | None
    signed_in_at: datetime
    email: str | None = None
    email_verified: bool = False
    transport: Transport = "cookie"
    # The phone app's PIN for this session. A locked session answers nothing but the PIN.
    pin_set: bool = False
    locked: bool = False
    # When the client last used a passkey or authenticator app on this session: signing in
    # with one, or confirming it is them again. None when only a texted or emailed code, or the
    # sign up code, was used. A change to how they sign in needs this in the last ten minutes.
    confirmed_at: datetime | None = None

    @property
    def actor(self) -> Actor:
        return Actor("client", self.client_id)


@dataclass(frozen=True, slots=True)
class StaffSession:
    session_id: str
    staff_id: str
    name: str
    role: Role
    permissions: list[Permission]
    # When this person last used their passkey or authenticator app on this session: signing
    # in, or confirming it is them again since.
    confirmed_at: datetime

    @property
    def actor(self) -> Actor:
        return Actor("staff", self.staff_id)


@dataclass(frozen=True, slots=True)
class DeviceSession:
    """One signed-in device, as the person sees it in their list."""

    id: str
    device: str
    transport: Transport
    second_step: SecondStep
    signed_in_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A new session. `token` is given to the caller once and never stored."""

    token: str
    transport: Transport
    idle_expires_at: datetime
    absolute_expires_at: datetime


class IdentityService:
    def __init__(
        self, database: Database, clock: Clock, settings: Settings, hasher: PasswordHasher
    ) -> None:
        self.database = database
        self.clock = clock
        self.hasher = hasher
        self._policies: dict[tuple[AccountKind, Transport], SessionPolicy] = {
            ("client", "cookie"): SessionPolicy(
                timedelta(minutes=settings.client_web_session_idle_minutes),
                timedelta(hours=settings.client_web_session_absolute_hours),
            ),
            ("client", "token"): SessionPolicy(
                timedelta(days=settings.client_app_session_idle_days),
                timedelta(days=settings.client_app_session_absolute_days),
            ),
            ("staff", "cookie"): SessionPolicy(
                timedelta(minutes=settings.staff_session_idle_minutes),
                timedelta(hours=settings.staff_session_absolute_hours),
            ),
        }

    # Accounts

    async def create_client(self, *, phone: str, email: str | None, password: str) -> str:
        """Create a client's sign-in. The caller has already proved the number is theirs."""
        try:
            phone = normalize_phone(phone)
            email = normalize_email(email) if email else None
        except InvalidIdentifier as error:
            raise _invalid(str(error), "invalid") from None
        problem = password_problem(password, (phone, email))
        if problem is not None:
            raise _invalid("password", problem)
        hashed = await self.hasher.hash(password)
        client_id = push_id(self.clock)
        now = self.clock.now()
        try:
            async with self.database.transaction() as session:
                await session.execute(
                    text(
                        "INSERT INTO identity_clients (id, phone, email, password_hash, "
                        "password_changed_at, created_at) "
                        "VALUES (:id, :phone, :email, :hash, :now, :now)"
                    ),
                    {"id": client_id, "phone": phone, "email": email, "hash": hashed, "now": now},
                )
        except IntegrityError:
            raise ApplicationError("phone_taken") from None
        logger.info("client_created", client_id=client_id)
        return client_id

    async def invite_staff(
        self, *, email: str, name: str, role: Role, created_by: str | None
    ) -> str:
        """Add a member of staff with no password yet. Enrolment sets it, with a second step."""
        try:
            email = normalize_email(email)
        except InvalidIdentifier:
            raise _invalid("email", "invalid") from None
        staff_id = push_id(self.clock)
        try:
            async with self.database.transaction() as session:
                await session.execute(
                    text(
                        "INSERT INTO identity_staff (id, email, name, role, created_at, "
                        "created_by) VALUES (:id, :email, :name, :role, :now, :created_by)"
                    ),
                    {
                        "id": staff_id,
                        "email": email,
                        "name": name.strip(),
                        "role": role,
                        "now": self.clock.now(),
                        "created_by": created_by,
                    },
                )
        except IntegrityError:
            raise ApplicationError("conflict") from None
        logger.info("staff_invited", staff_id=staff_id, role=role)
        return staff_id

    # Step one

    async def start_login(
        self,
        kind: AccountKind,
        identifier: str,
        password: str,
        *,
        address: str | None,
        by_email: bool = False,
    ) -> LoginChallenge:
        """Check the number or email and the password. Opens nothing.

        A client signs in with their mobile number, or with `by_email` and an email address they
        have proved. Staff always use their email. Every failure is the same
        `invalid_credentials`, after the same amount of work, whether the account is missing,
        inactive or the password is wrong.
        """
        email_login = kind == "staff" or by_email
        try:
            identifier = normalize_email(identifier) if email_login else normalize_phone(identifier)
        except InvalidIdentifier:
            identifier = ""  # still do the work below, then fail like any wrong password

        if kind == "client" and by_email:
            # Only a proved address signs a client in, and only one account can have proved it.
            query = (
                "SELECT id, phone, password_hash FROM identity_clients "
                "WHERE email = :identifier AND email_verified_at IS NOT NULL AND status = 'active'"
            )
        elif kind == "client":
            query = (
                "SELECT id, phone, password_hash FROM identity_clients "
                "WHERE phone = :identifier AND status = 'active'"
            )
        else:
            query = (
                "SELECT id, email AS phone, password_hash FROM identity_staff "
                "WHERE email = :identifier AND status = 'active'"
            )
        async with self.database.transaction() as session:
            account = (await session.execute(text(query), {"identifier": identifier})).one_or_none()

        # A client's attempts count against their number whichever way they sign in, so the
        # email gives no second budget of guesses. An unknown address counts against itself.
        counted = account.phone if account is not None and kind == "client" else identifier
        account_key = throttle_key(f"{kind}-account", counted)
        pair_key = throttle_key(f"{kind}-account-from", f"{counted}\x00{address}")
        keys = [(account_key, ACCOUNT_ATTEMPTS), (pair_key, ACCOUNT_FROM_ADDRESS_ATTEMPTS)]
        if address is not None:
            keys.append((throttle_key("address", address), ADDRESS_ATTEMPTS))
        await reserve_attempts(self.database, self.clock, keys)

        table = "identity_clients" if kind == "client" else "identity_staff"
        hashed = account.password_hash if account is not None else None
        if not await self.hasher.verify(password, hashed) or account is None:
            logger.info("login_refused", kind=kind, error_code="invalid_credentials")
            raise ApplicationError("invalid_credentials")

        now = self.clock.now()
        challenge_id = push_id(self.clock)
        async with self.database.transaction() as session:
            if self.hasher.needs_rehash(account.password_hash):
                await session.execute(
                    text(f"UPDATE {table} SET password_hash = :hash WHERE id = :id"),  # noqa: S608
                    {"hash": await self.hasher.hash(password), "id": account.id},
                )
            methods = await _devices_held(session, Actor(kind, account.id))
            if kind == "client":
                # A texted or emailed code is the backup for clients. Staff never have one.
                methods.append("code")
            if not methods:
                # Staff must hold a second step. Without one, nothing can finish a sign-in.
                raise ApplicationError("second_step_missing")
            await session.execute(
                text(
                    "INSERT INTO identity_login_challenges "
                    "(id, actor_kind, actor_id, created_at, expires_at) "
                    "VALUES (:id, :kind, :actor, :now, :expires)"
                ),
                {
                    "id": challenge_id,
                    "kind": kind,
                    "actor": account.id,
                    "now": now,
                    "expires": now + LOGIN_CHALLENGE_LIFETIME,
                },
            )
        # The right password clears the wrong-guess counts for this account, not for the address.
        await clear_attempts(self.database, [account_key, pair_key])
        logger.info("login_started", kind=kind, login_id=challenge_id)
        # In the order to offer them. The person finishes with any one.
        return LoginChallenge(challenge_id, methods)

    # Sessions

    async def open_session(
        self, login_id: str, kind: AccountKind, *, second_step: SecondStep, transport: Transport
    ) -> IssuedSession:
        """Turn a login challenge into a session. The caller has verified the second step.

        A challenge opens one session, once, before it expires. Anything else is `expired`.
        """
        now = self.clock.now()
        async with self.database.transaction() as session:
            actor_id = await session.scalar(
                text(
                    "UPDATE identity_login_challenges SET consumed_at = :now "
                    "WHERE id = :id AND actor_kind = :kind AND consumed_at IS NULL "
                    "AND expires_at > :now RETURNING actor_id"
                ),
                {"id": login_id, "kind": kind, "now": now},
            )
            if actor_id is None:
                raise ApplicationError("expired")
            limited = restricted_until(second_step, now)
            if kind == "client" and limited is not None:
                await session.execute(
                    text(
                        "UPDATE identity_clients SET restricted_until = "
                        "greatest(coalesce(restricted_until, :until), :until) WHERE id = :id"
                    ),
                    {"until": limited, "id": actor_id},
                )
            return await self.issue_in(session, Actor(kind, actor_id), second_step, transport, now)

    async def issue_session(
        self, actor: Actor, *, second_step: SecondStep, transport: Transport
    ) -> IssuedSession:
        """Open a session directly, for a flow that has just proved who the person is."""
        async with self.database.transaction() as session:
            return await self.issue_in(session, actor, second_step, transport, self.clock.now())

    async def issue_in(
        self,
        session: AsyncSession,
        actor: Actor,
        second_step: SecondStep,
        transport: Transport,
        now: datetime,
    ) -> IssuedSession:
        """Open a session inside the caller's transaction, so it commits with what proved it."""
        if actor.kind not in ("client", "staff"):
            raise ValueError("Only a client or a staff member has a session")
        if (actor.kind, transport) not in self._policies:
            raise ApplicationError("validation_failed")  # staff use the web console only
        policy = await self._policy(session, actor.kind, transport, now)
        idle, absolute = policy.expiry(now, now)
        token = new_token()
        session_id = push_id(self.clock)
        device = current_device.get() or UNKNOWN_DEVICE
        await self._tell_of_a_new_device(session, actor, device, session_id, now)
        await session.execute(
            text(
                "INSERT INTO identity_sessions (id, actor_kind, actor_id, token_hash, "
                "second_step, transport, created_at, last_seen_at, idle_expires_at, "
                "absolute_expires_at, device) VALUES (:id, :kind, :actor, :hash, :step, "
                ":transport, :now, :now, :idle, :absolute, :device)"
            ),
            {
                "device": device,
                "id": session_id,
                "kind": actor.kind,
                "actor": actor.id,
                "hash": token_digest(token),
                "step": second_step,
                "transport": transport,
                "now": now,
                "idle": idle,
                "absolute": absolute,
            },
        )
        await trail.note(
            session,
            self.clock,
            actor,
            "identity.session.opened",
            resource_type="session",
            resource_id=session_id,
            details={"second_step": second_step, "transport": transport},
        )
        logger.info("session_opened", kind=actor.kind, second_step=second_step)
        return IssuedSession(token, transport, idle, absolute)

    async def _tell_of_a_new_device(
        self, session: AsyncSession, actor: Actor, device: str, session_id: str, now: datetime
    ) -> None:
        """Message the person when they sign in on a kind of device they have not used before.

        Nothing is sent for their very first session, which is the signup, claim or set-up.
        """
        seen = (
            await session.execute(
                text(
                    "SELECT count(*) AS sessions, count(*) FILTER (WHERE device = :device) "
                    "AS here FROM identity_sessions WHERE actor_kind = :kind AND actor_id = :id"
                ),
                {"device": device, "kind": actor.kind, "id": actor.id},
            )
        ).one()
        if seen.sessions == 0 or seen.here > 0:
            return
        if actor.kind == "client":
            contact = (
                await session.execute(
                    text(
                        "SELECT phone, CASE WHEN email_verified_at IS NOT NULL THEN email END "
                        "AS email FROM identity_clients WHERE id = :id"
                    ),
                    {"id": actor.id},
                )
            ).one_or_none()
            # The phone is the client's proved contact, so the notice always goes there. An
            # email goes only to an address they proved; the worker skips a suppressed one.
            sends: list[tuple[Channel, str | None]] = (
                [("sms", contact.phone), ("email", contact.email)] if contact else []
            )
        else:
            email = await session.scalar(
                text("SELECT email FROM identity_staff WHERE id = :id"), {"id": actor.id}
            )
            sends = [("email", email)]
        for channel, recipient in sends:
            if not recipient:
                continue
            await queue_message(
                session,
                channel=channel,
                recipient=recipient,
                template="new_sign_in",
                variables={"device": device, "when": kampala_time(now)},
                idempotency_key=f"new-sign-in:{session_id}:{channel}",
                now=now,
            )

    async def _policy(
        self, session: AsyncSession, kind: AccountKind, transport: Transport, now: datetime
    ) -> SessionPolicy:
        """The lifetime of a session. A staff session's idle time is set on Settings once two
        people have approved one; until then it is the deploy's `staff_session_idle_minutes`."""
        policy = self._policies[(kind, transport)]
        if kind != "staff":
            return policy
        approved = await platform_settings.approved(session, "staff_idle_minutes")
        if not isinstance(approved, StaffIdleMinutes):
            return policy
        return SessionPolicy(
            min(timedelta(minutes=approved.minutes), policy.absolute), policy.absolute
        )

    async def _touch(  # type: ignore[no-untyped-def]
        self, token: str, kind: AccountKind, address: str | None = None
    ):
        """Find a live session by token and push its idle expiry forward.

        A staff session is refused, untouched, from outside the office networks set on Settings.
        """
        now = self.clock.now()
        async with self.database.transaction() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, actor_id, transport, created_at, last_seen_at, "
                        "idle_expires_at, reverified_at, second_step, "
                        "EXISTS (SELECT FROM identity_app_pins "
                        "WHERE session_id = identity_sessions.id) AS pin_set "
                        "FROM identity_sessions "
                        "WHERE token_hash = :hash AND actor_kind = :kind AND revoked_at IS NULL "
                        "AND idle_expires_at > :now AND absolute_expires_at > :now"
                    ),
                    {"hash": token_digest(token), "kind": kind, "now": now},
                )
            ).one_or_none()
            if row is None:
                return None, None
            policy = await self._policy(session, kind, row.transport, now)
            if row.last_seen_at + policy.idle <= now:
                # Idle for longer than a shorter idle time approved since it was last used.
                return None, None
            if kind == "staff":
                networks = await platform_settings.office_networks(session, now)
                if not networks.allows(address):
                    raise ApplicationError(
                        "forbidden",
                        fields=[
                            FieldProblem(field_path=["network"], code="outside_office_network")
                        ],
                    )
            idle, _ = policy.expiry(row.created_at, now)
            # Judged before this request counts as use. A locked session stays locked, and its
            # idle time keeps running, until the PIN opens it.
            locked = is_locked(pin_set=row.pin_set, last_seen_at=row.last_seen_at, now=now)
            # A busy page makes many requests. One write a minute keeps the session alive.
            if not locked and idle - row.idle_expires_at >= TOUCH_INTERVAL:
                await session.execute(
                    text(
                        "UPDATE identity_sessions SET last_seen_at = :now, "
                        "idle_expires_at = :idle WHERE id = :id AND revoked_at IS NULL"
                    ),
                    {"now": now, "idle": idle, "id": row.id},
                )
            table = "identity_clients" if kind == "client" else "identity_staff"
            account = (
                await session.execute(
                    text(f"SELECT * FROM {table} WHERE id = :id AND status = 'active'"),  # noqa: S608
                    {"id": row.actor_id},
                )
            ).one_or_none()
            return row, account

    async def client_session(self, token: str) -> ClientSession | None:
        row, account = await self._touch(token, "client")
        if row is None or account is None:
            return None
        limited = account.restricted_until
        if limited is not None and limited <= self.clock.now():
            limited = None
        return ClientSession(
            row.id,
            account.id,
            account.phone,
            limited,
            row.created_at,
            email=account.email,
            email_verified=account.email_verified_at is not None,
            transport=row.transport,
            pin_set=row.pin_set,
            locked=is_locked(
                pin_set=row.pin_set, last_seen_at=row.last_seen_at, now=self.clock.now()
            ),
            confirmed_at=row.reverified_at
            or (row.created_at if row.second_step in STRONG_STEPS else None),
        )

    async def staff_session(self, token: str, address: str | None = None) -> StaffSession | None:
        """The signed-in member of staff, or None. `address` is where the request came from,
        checked against the office networks: outside them, or unknown while networks are set,
        is `forbidden`."""
        row, account = await self._touch(token, "staff", address)
        if row is None or account is None:
            return None
        return StaffSession(
            row.id,
            account.id,
            account.name,
            account.role,
            permissions_for(account.role),
            row.reverified_at or row.created_at,
        )

    async def staff_second_steps(self, staff: StaffSession) -> list[SecondStep]:
        """The passkeys and authenticator app this person can confirm it is them with."""
        async with self.database.transaction() as session:
            return await _devices_held(session, staff.actor)

    async def confirm_staff(self, staff: StaffSession, second_step: SecondStep) -> datetime:
        """Note that this person just used their second step again on this session."""
        now = self.clock.now()
        async with self.database.transaction() as session:
            confirmed = await session.scalar(
                text(
                    "UPDATE identity_sessions SET reverified_at = :now WHERE id = :id "
                    "AND actor_kind = 'staff' AND actor_id = :staff AND revoked_at IS NULL "
                    "RETURNING true"
                ),
                {"now": now, "id": staff.session_id, "staff": staff.staff_id},
            )
            if not confirmed:
                raise ApplicationError("unauthorized")
            await trail.note(
                session,
                self.clock,
                staff.actor,
                "identity.session.reconfirmed",
                resource_type="session",
                resource_id=staff.session_id,
                details={"second_step": second_step},
            )
        logger.info("staff_reconfirmed", second_step=second_step)
        return now + CONFIRMATION_WINDOW

    async def confirm_client(self, client: ClientSession, second_step: SecondStep) -> datetime:
        """Note that the client just used a passkey or their authenticator app on this session."""
        now = self.clock.now()
        async with self.database.transaction() as session:
            confirmed = await session.scalar(
                text(
                    "UPDATE identity_sessions SET reverified_at = :now WHERE id = :id "
                    "AND actor_kind = 'client' AND actor_id = :client AND revoked_at IS NULL "
                    "RETURNING true"
                ),
                {"now": now, "id": client.session_id, "client": client.client_id},
            )
            if not confirmed:
                raise ApplicationError("unauthorized")
            await trail.note(
                session,
                self.clock,
                client.actor,
                "identity.session.reconfirmed",
                resource_type="session",
                resource_id=client.session_id,
                details={"second_step": second_step},
            )
        logger.info("client_reconfirmed", second_step=second_step)
        return now + CONFIRMATION_WINDOW

    async def sign_out(self, token: str, kind: AccountKind) -> None:
        async with self.database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE identity_sessions SET revoked_at = :now, revoked_reason = 'signed_out' "
                    "WHERE token_hash = :hash AND actor_kind = :kind AND revoked_at IS NULL"
                ),
                {"now": self.clock.now(), "hash": token_digest(token), "kind": kind},
            )

    async def live_sessions(self, actor: Actor) -> list[DeviceSession]:
        """The person's sessions that still work, most recently used first."""
        now = self.clock.now()
        async with self.database.transaction() as session:
            rows = await session.execute(
                text(
                    "SELECT id, device, transport, second_step, created_at, last_seen_at "
                    "FROM identity_sessions WHERE actor_kind = :kind AND actor_id = :id "
                    "AND revoked_at IS NULL AND idle_expires_at > :now "
                    "AND absolute_expires_at > :now ORDER BY last_seen_at DESC LIMIT 100"
                ),
                {"kind": actor.kind, "id": actor.id, "now": now},
            )
            return [
                DeviceSession(
                    row.id,
                    row.device or UNKNOWN_DEVICE,
                    row.transport,
                    row.second_step,
                    row.created_at,
                    row.last_seen_at,
                )
                for row in rows
            ]

    async def sign_out_device(self, actor: Actor, session_id: str) -> None:
        """End one of the person's own sessions. Another person's is `not_found`."""
        async with self.database.transaction() as session:
            ended = await session.scalar(
                text(
                    "UPDATE identity_sessions SET revoked_at = :now, "
                    "revoked_reason = 'signed_out_elsewhere' WHERE id = :sid "
                    "AND actor_kind = :kind AND actor_id = :id AND revoked_at IS NULL "
                    "RETURNING true"
                ),
                {"now": self.clock.now(), "sid": session_id, "kind": actor.kind, "id": actor.id},
            )
            if not ended:
                raise ApplicationError("not_found")
            await trail.note(
                session,
                self.clock,
                actor,
                "identity.session.signed_out_elsewhere",
                resource_type="session",
                resource_id=session_id,
            )

    async def sign_out_other_devices(self, actor: Actor, keep_session_id: str) -> int:
        """End every session of the person except the one making the request."""
        async with self.database.transaction() as session:
            rows = await session.execute(
                text(
                    "UPDATE identity_sessions SET revoked_at = :now, "
                    "revoked_reason = 'signed_out_elsewhere' WHERE actor_kind = :kind "
                    "AND actor_id = :id AND id <> :keep AND revoked_at IS NULL RETURNING id"
                ),
                {
                    "now": self.clock.now(),
                    "kind": actor.kind,
                    "id": actor.id,
                    "keep": keep_session_id,
                },
            )
            ended = len(rows.all())
            await trail.note(
                session,
                self.clock,
                actor,
                "identity.session.signed_out_other_devices",
                resource_type="session",
                resource_id=keep_session_id,
                details={"ended": ended},
            )
            return ended

    async def revoke_sessions(self, actor: Actor, reason: str) -> int:
        """End every session of one person, for a password change or a disabled account."""
        async with self.database.transaction() as session:
            rows = await session.execute(
                text(
                    "UPDATE identity_sessions SET revoked_at = :now, revoked_reason = :reason "
                    "WHERE actor_kind = :kind AND actor_id = :id AND revoked_at IS NULL "
                    "RETURNING id"
                ),
                {"now": self.clock.now(), "reason": reason, "kind": actor.kind, "id": actor.id},
            )
            return len(rows.all())


@dataclass(frozen=True, slots=True)
class ClientContact:
    """Where to reach a client. `email` is None when they gave none.

    `claimed` is false for an account carried over from Finvest 1.0 that its owner has not
    claimed yet. Send such a client only a reminder to claim, never their statements.
    """

    phone: str
    email: str | None
    claimed: bool
    # True only while `email` is an address the client proved with a link. Statements and
    # receipts go only to a proved address.
    email_verified: bool = False


async def client_contact(session: AsyncSession, client_id: str) -> ClientContact | None:
    """The phone and email of a client, for the worker to send them a message.

    Other modules call this instead of reading identity tables. It answers for an active
    client and for one carried over from Finvest 1.0 who has not claimed the account yet, who
    still needs reminders. It answers nothing for a client who is locked or closed, so nobody
    is messaged after closing their account. The worker role may read only these columns.
    """
    row = (
        await session.execute(
            text(
                "SELECT phone, email, status, email_verified_at FROM identity_clients "
                "WHERE id = :id AND status IN ('active', 'unclaimed')"
            ),
            {"id": client_id},
        )
    ).one_or_none()
    if row is None:
        return None
    return ClientContact(
        row.phone,
        row.email,
        claimed=row.status == "active",
        email_verified=row.email_verified_at is not None,
    )


async def active_client_ids(session: AsyncSession, *, after: str | None, limit: int) -> list[str]:
    """Active clients in ID order after `after`, for the worker to tell every one of them.

    Other modules call this instead of reading identity tables. It leaves out locked and
    closed clients, and those carried over from Finvest 1.0 who have not claimed the account.
    """
    rows = await session.execute(
        text(
            "SELECT id FROM identity_clients WHERE status = 'active' "
            "AND (CAST(:after AS text) IS NULL OR id > :after) ORDER BY id LIMIT :limit"
        ),
        {"after": after, "limit": limit},
    )
    return [row.id for row in rows]


async def count_active_clients(session: AsyncSession, among: Sequence[str] | None = None) -> int:
    """How many clients `active_client_ids` would list, of all or of `among` only.

    Other modules call this instead of reading identity tables, to say before a message to every
    client how many it reaches.
    """
    return int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM identity_clients WHERE status = 'active' "
                    "AND (CAST(:among AS text[]) IS NULL OR id = ANY(CAST(:among AS text[])))"
                ),
                {"among": None if among is None else list(among)},
            )
        ).scalar_one()
    )


def _staff_only(actor: Actor) -> None:
    if actor.kind != "staff":
        raise ValueError("Only a member of staff does this")


async def create_unclaimed_client(
    session: AsyncSession, clock: Clock, actor: Actor, *, phone: str, email: str | None
) -> str:
    """Create the sign-in of a client carried over from Finvest 1.0, in the caller's transaction.

    The account has no password, so nothing signs in as it until the client claims it with
    `CodeService.claim_start` and `claim_complete`. `actor` is the member of staff running the
    import, and the transaction must be acting as them. Raises `phone_taken` and leaves the
    transaction usable when the number already has an account.
    """
    _staff_only(actor)
    try:
        phone = normalize_phone(phone)
        email = normalize_email(email) if email else None
    except InvalidIdentifier as error:
        raise _invalid(str(error), "invalid") from None
    client_id = push_id(clock)
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO identity_clients (id, phone, email, status, created_at) "
                    "VALUES (:id, :phone, :email, 'unclaimed', :now)"
                ),
                {"id": client_id, "phone": phone, "email": email, "now": clock.now()},
            )
    except IntegrityError:
        raise ApplicationError("phone_taken") from None
    await trail.note(
        session,
        clock,
        actor,
        "identity.client.imported",
        resource_type="client",
        resource_id=client_id,
    )
    logger.info("client_imported", client_id=client_id)
    return client_id


async def staff_names(session: AsyncSession, staff_ids: Collection[str]) -> dict[str, str]:
    """The names of these members of staff, for screens that show who did something.

    Other modules call this instead of reading identity tables. An ID with no account is
    left out.
    """
    if not staff_ids:
        return {}
    rows = await session.execute(
        text("SELECT id, name FROM identity_staff WHERE id = ANY(:ids)"),
        {"ids": sorted(set(staff_ids))},
    )
    return {row.id: row.name for row in rows.all()}


@dataclass(frozen=True, slots=True)
class StaffContact:
    """Where to reach a member of staff, and whether they may still be sent anything."""

    id: str
    email: str
    name: str
    role: Role
    active: bool


async def staff_contacts(
    session: AsyncSession, staff_ids: Collection[str]
) -> dict[str, StaffContact]:
    """The email address, name, role and status of these members of staff, for the worker that
    sends them their scheduled reports. An ID with no account is left out.

    The worker cannot read the staff table, which holds sign-in secrets, so this asks a database
    function that answers these columns and nothing else.
    """
    if not staff_ids:
        return {}
    rows = await session.execute(
        text("SELECT id, email, name, role, status FROM identity_staff_contacts(:ids)"),
        {"ids": sorted(set(staff_ids))},
    )
    return {
        row.id: StaffContact(
            id=row.id,
            email=row.email,
            name=row.name,
            role=row.role,
            active=row.status == "active",
        )
        for row in rows.all()
    }


async def active_staff_roles(session: AsyncSession) -> list[tuple[str, str, Role]]:
    """Every active member of staff as (id, name, role), by name, for a screen that picks who
    may be sent something their role allows."""
    rows = await session.execute(
        text(
            "SELECT id, name, role FROM identity_staff WHERE status = 'active' "
            "ORDER BY lower(name), id"
        )
    )
    return [(row.id, row.name, row.role) for row in rows.all()]


async def active_staff_names(session: AsyncSession) -> dict[str, str]:
    """Every active member of staff by name, for a console screen that picks one."""
    rows = await session.execute(
        text("SELECT id, name FROM identity_staff WHERE status = 'active' ORDER BY lower(name), id")
    )
    return {row.id: row.name for row in rows.all()}


def _invalid(field: str, code: str) -> ApplicationError:
    error_code: ErrorCode = "leaked_password" if code == "leaked_password" else "validation_failed"
    return ApplicationError(
        error_code, fields=[FieldProblem(field_path=["body", field], code=code)]
    )


async def _devices_held(session: AsyncSession, actor: Actor) -> list[SecondStep]:
    """Which of passkey and authenticator app this person has set up, passkey first."""
    row = (
        await session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM identity_passkeys WHERE actor_kind = :kind "
                "AND actor_id = :actor AND removed_at IS NULL) AS passkey, "
                "EXISTS (SELECT 1 FROM identity_authenticators WHERE actor_kind = :kind "
                "AND actor_id = :actor AND confirmed_at IS NOT NULL "
                "AND removed_at IS NULL) AS app"
            ),
            {"kind": actor.kind, "actor": actor.id},
        )
    ).one()
    methods: list[SecondStep] = ["passkey"] if row.passkey else []
    if row.app:
        methods.append("authenticator")
    return methods
