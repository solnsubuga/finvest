"""Accounts, the first step of sign-in, and sessions.

Sign-in is two steps. `start_login` checks the mobile number (or staff email) and password and
returns a login challenge. It never opens a session. A second step, a passkey or for clients a
code, turns the challenge into a session with `open_session`.
"""

from collections.abc import Collection
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
from app.modules.identity.domain.accounts import InvalidIdentifier, normalize_email, normalize_phone
from app.modules.identity.domain.passwords import password_problem
from app.modules.identity.domain.permissions import Permission, Role, permissions_for
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

    @property
    def actor(self) -> Actor:
        return Actor("staff", self.staff_id)


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
        self, kind: AccountKind, identifier: str, password: str, *, address: str | None
    ) -> LoginChallenge:
        """Check the number or email and the password. Opens nothing.

        Every failure is the same `invalid_credentials`, after the same amount of work, whether
        the account is missing, inactive or the password is wrong.
        """
        try:
            identifier = (
                normalize_phone(identifier) if kind == "client" else normalize_email(identifier)
            )
        except InvalidIdentifier:
            identifier = ""  # still do the work below, then fail like any wrong password
        account_key = throttle_key(f"{kind}-account", identifier)
        pair_key = throttle_key(f"{kind}-account-from", f"{identifier}\x00{address}")
        keys = [(account_key, ACCOUNT_ATTEMPTS), (pair_key, ACCOUNT_FROM_ADDRESS_ATTEMPTS)]
        if address is not None:
            keys.append((throttle_key("address", address), ADDRESS_ATTEMPTS))
        await reserve_attempts(self.database, self.clock, keys)

        table, column = (
            ("identity_clients", "phone") if kind == "client" else ("identity_staff", "email")
        )
        async with self.database.transaction() as session:
            account = (
                await session.execute(
                    text(
                        f"SELECT id, password_hash FROM {table} "  # noqa: S608 - fixed names
                        f"WHERE {column} = :identifier AND status = 'active'"
                    ),
                    {"identifier": identifier},
                )
            ).one_or_none()
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
            has_passkey = await session.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM identity_passkeys WHERE actor_kind = :kind "
                    "AND actor_id = :actor AND removed_at IS NULL)"
                ),
                {"kind": kind, "actor": account.id},
            )
            has_app = await session.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM identity_authenticators WHERE actor_kind = "
                    ":kind AND actor_id = :actor AND confirmed_at IS NOT NULL "
                    "AND removed_at IS NULL)"
                ),
                {"kind": kind, "actor": account.id},
            )
            methods: list[SecondStep] = ["passkey"] if has_passkey else []
            if has_app:
                methods.append("authenticator")
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
        policy = self._policies.get((actor.kind, transport))
        if policy is None:
            raise ApplicationError("validation_failed")  # staff use the web console only
        idle, absolute = policy.expiry(now, now)
        token = new_token()
        session_id = push_id(self.clock)
        await session.execute(
            text(
                "INSERT INTO identity_sessions (id, actor_kind, actor_id, token_hash, "
                "second_step, transport, created_at, last_seen_at, idle_expires_at, "
                "absolute_expires_at) VALUES (:id, :kind, :actor, :hash, :step, :transport, "
                ":now, :now, :idle, :absolute)"
            ),
            {
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

    async def _touch(self, token: str, kind: AccountKind):  # type: ignore[no-untyped-def]
        """Find a live session by token and push its idle expiry forward."""
        now = self.clock.now()
        async with self.database.transaction() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, actor_id, transport, created_at, idle_expires_at "
                        "FROM identity_sessions "
                        "WHERE token_hash = :hash AND actor_kind = :kind AND revoked_at IS NULL "
                        "AND idle_expires_at > :now AND absolute_expires_at > :now"
                    ),
                    {"hash": token_digest(token), "kind": kind, "now": now},
                )
            ).one_or_none()
            if row is None:
                return None, None
            idle, _ = self._policies[(kind, row.transport)].expiry(row.created_at, now)
            # A busy page makes many requests. One write a minute keeps the session alive.
            if idle - row.idle_expires_at >= TOUCH_INTERVAL:
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
        return ClientSession(row.id, account.id, account.phone, limited, row.created_at)

    async def staff_session(self, token: str) -> StaffSession | None:
        row, account = await self._touch(token, "staff")
        if row is None or account is None:
            return None
        return StaffSession(
            row.id, account.id, account.name, account.role, permissions_for(account.role)
        )

    async def sign_out(self, token: str, kind: AccountKind) -> None:
        async with self.database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE identity_sessions SET revoked_at = :now, revoked_reason = 'signed_out' "
                    "WHERE token_hash = :hash AND actor_kind = :kind AND revoked_at IS NULL"
                ),
                {"now": self.clock.now(), "hash": token_digest(token), "kind": kind},
            )

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
                "SELECT phone, email, status FROM identity_clients "
                "WHERE id = :id AND status IN ('active', 'unclaimed')"
            ),
            {"id": client_id},
        )
    ).one_or_none()
    if row is None:
        return None
    return ClientContact(row.phone, row.email, claimed=row.status == "active")


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


def _invalid(field: str, code: str) -> ApplicationError:
    error_code: ErrorCode = "leaked_password" if code == "leaked_password" else "validation_failed"
    return ApplicationError(
        error_code, fields=[FieldProblem(field_path=["body", field], code=code)]
    )
