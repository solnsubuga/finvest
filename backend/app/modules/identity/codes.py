"""One-time codes by SMS or email: signup, the sign-in fallback, password reset, claims and
adding an account for withdrawals.

A code is six digits, lasts ten minutes, allows five tries and works once. Only its digest is
stored here. The code itself travels through the notifications queue, which empties it once
the message is sent. No answer says whether a number has an account, except to someone who
has just proved the number is theirs.

A code proves less than a passkey or an authenticator app, because a number can be taken over.
So a code never signs anyone in without the password, and signing in or resetting a password
with one limits withdrawals to known accounts for 24 hours.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.infrastructure.throttling import reserve_attempts, throttle_key
from app.modules.identity import trail
from app.modules.identity.domain.accounts import InvalidIdentifier, normalize_email, normalize_phone
from app.modules.identity.domain.passwords import password_problem
from app.modules.identity.domain.sessions import RESTRICTION, Transport, new_token, token_digest
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.passkeys import PasskeyService
from app.modules.identity.service import IdentityService, IssuedSession
from app.modules.notifications.domain.templates import Template
from app.modules.notifications.service import Channel, queue_message
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

CODE_LIFETIME = timedelta(minutes=10)
TICKET_LIFETIME = timedelta(minutes=15)
MAX_TRIES = 5
# Codes sent in fifteen minutes: to one number, from one address, and for one sign-in.
SENDS_PER_NUMBER = 3
SENDS_PER_ADDRESS = 20
SENDS_PER_LOGIN = 3
# Tries at finishing one claim in fifteen minutes, counting weak passwords as well as codes.
CLAIM_COMPLETIONS = 10
Purpose = Literal["signup", "login", "password_reset", "claim", "payout_account", "phone_change"]
_TEMPLATES: dict[Purpose, Template] = {
    "signup": "signup_code",
    "login": "sign_in_code",
    "password_reset": "password_reset_code",
    "claim": "claim_code",
    "payout_account": "payout_account_code",
    "phone_change": "phone_change_code",
}
# Codes sent in fifteen minutes for one payout account, and for all of one client's accounts.
SENDS_PER_PAYOUT_ACCOUNT = 3
SENDS_PER_PAYOUT_CLIENT = 5
# Codes for new payout accounts that one client may be sent in a day.
PAYOUT_CODES_PER_DAY = 10
PAYOUT_CODE_DAY = timedelta(days=1)
# Codes to a new mobile number that one client may be sent in a day.
PHONE_CHANGE_CODES_PER_DAY = 5

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class SignupTicket:
    ticket: str
    phone: str


@dataclass(frozen=True, slots=True)
class Claimed:
    """The account a claim made active, and its first session."""

    client_id: str
    session: IssuedSession


def _code_digest(code_id: str, code: str) -> bytes:
    return hashlib.sha256(f"{code_id}:{code}".encode()).digest()


def _new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _field(name: str, code: str = "invalid") -> ApplicationError:
    return ApplicationError(
        "leaked_password" if code == "leaked_password" else "validation_failed",
        fields=[FieldProblem(field_path=["body", name], code=code)],
    )


class CodeService:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        identity: IdentityService,
        logins: PasskeyService,
        hasher: PasswordHasher,
    ) -> None:
        self.database = database
        self.clock = clock
        self.identity = identity
        self.logins = logins
        self.hasher = hasher

    # Making and checking a code

    async def _issue(
        self,
        session: AsyncSession,
        purpose: Purpose,
        channel: Channel,
        *,
        recipient: str | None,
        phone: str | None = None,
        email: str | None = None,
        actor_id: str | None = None,
        bound_to: str | None = None,
    ) -> str:
        """Store a new code and, when there is someone to send it to, queue its message.

        With no recipient the row is still written, with a digest nothing matches, so a
        request about an unknown number looks and behaves exactly like any other.
        """
        now = self.clock.now()
        code_id = push_id(self.clock)
        code = _new_code()
        digest = _code_digest(code_id, code) if recipient else secrets.token_bytes(32)
        if bound_to is not None:
            # A new code for the same sign-in replaces the one before it.
            await session.execute(
                text(
                    "UPDATE identity_codes SET consumed_at = :now WHERE bound_to = :bound "
                    "AND purpose = :purpose AND consumed_at IS NULL"
                ),
                {"now": now, "bound": bound_to, "purpose": purpose},
            )
        await session.execute(
            text(
                "INSERT INTO identity_codes (id, purpose, channel, phone, email, actor_id, "
                "bound_to, code_hash, created_at, expires_at) VALUES (:id, :purpose, :channel, "
                ":phone, :email, :actor, :bound, :hash, :now, :expires)"
            ),
            {
                "id": code_id,
                "purpose": purpose,
                "channel": channel,
                "phone": phone,
                "email": email,
                "actor": actor_id,
                "bound": bound_to,
                "hash": digest,
                "now": now,
                "expires": now + CODE_LIFETIME,
            },
        )
        if recipient:
            await queue_message(
                session,
                channel=channel,
                recipient=recipient,
                template=_TEMPLATES[purpose],
                variables={"code": code, "minutes": int(CODE_LIFETIME.total_seconds() // 60)},
                idempotency_key=f"code:{code_id}",
                now=now,
                deliver_within=CODE_LIFETIME,
            )
        return code_id

    async def _spend(  # type: ignore[no-untyped-def]
        self, session: AsyncSession, where: str, values: dict[str, object], code: str
    ):
        """Count a try against the code and spend it if `code` is right.

        The try is counted before the comparison, in the caller's transaction, so concurrent
        guesses cannot exceed the limit. Returns the row, or raises `expired` when there is no
        live code and `invalid_code` when the guess is wrong.
        """
        now = self.clock.now()
        row = (
            await session.execute(
                text(
                    f"UPDATE identity_codes SET attempts = attempts + 1 WHERE {where} "  # noqa: S608
                    "AND consumed_at IS NULL AND expires_at > :now AND attempts < :most "
                    "RETURNING id, code_hash, phone, email, actor_id"
                ),
                {**values, "now": now, "most": MAX_TRIES},
            )
        ).one_or_none()
        if row is None:
            raise ApplicationError("expired")
        digits = code.replace(" ", "")
        if not hmac.compare_digest(bytes(row.code_hash), _code_digest(row.id, digits)):
            return None
        await session.execute(
            text("UPDATE identity_codes SET consumed_at = :now WHERE id = :id"),
            {"now": now, "id": row.id},
        )
        return row

    def _wrong(self, purpose: Purpose) -> ApplicationError:
        logger.info("code_refused", purpose=purpose, error_code="invalid_code")
        return ApplicationError("invalid_code")

    async def _limit_sends(self, kind: str, number: str, address: str | None) -> None:
        keys = [(throttle_key(f"{kind}-number", number), SENDS_PER_NUMBER)]
        if address is not None:
            keys.append((throttle_key("code-address", address), SENDS_PER_ADDRESS))
        await reserve_attempts(self.database, self.clock, keys)

    # Signup

    async def signup_start(
        self, phone: str, email: str | None, *, address: str | None
    ) -> tuple[str, str]:
        """Text a code to prove the number. The answer is the same whoever owns the number."""
        try:
            phone = normalize_phone(phone)
        except InvalidIdentifier:
            raise _field("phone") from None
        try:
            email = normalize_email(email) if email else None
        except InvalidIdentifier:
            raise _field("email") from None
        await self._limit_sends("signup", phone, address)
        async with self.database.transaction() as session:
            code_id = await self._issue(
                session, "signup", "sms", recipient=phone, phone=phone, email=email
            )
        logger.info("signup_started", challenge_id=code_id)
        return code_id, phone

    async def signup_confirm(self, challenge_id: str, code: str) -> SignupTicket:
        """Check the texted code. Only now may the caller learn the number has an account."""
        ticket = new_token()
        now = self.clock.now()
        async with self.database.transaction() as session:
            row = await self._spend(
                session, "id = :id AND purpose = 'signup'", {"id": challenge_id}, code
            )
            if row is not None:
                taken = await session.scalar(
                    text("SELECT true FROM identity_clients WHERE phone = :phone"),
                    {"phone": row.phone},
                )
                if not taken:
                    await session.execute(
                        text(
                            "UPDATE identity_codes SET ticket_hash = :hash, "
                            "ticket_expires_at = :expires WHERE id = :id"
                        ),
                        {
                            "hash": token_digest(ticket),
                            "expires": now + TICKET_LIFETIME,
                            "id": row.id,
                        },
                    )
        # Raised after the transaction, so the counted try and the spent code are kept.
        if row is None:
            raise self._wrong("signup")
        if taken:
            raise ApplicationError("phone_taken")
        return SignupTicket(ticket, row.phone)

    async def signup_complete(
        self, ticket: str, password: str, transport: Transport
    ) -> IssuedSession:
        """Set the password, create the account and open its first session, all at once."""
        now = self.clock.now()
        async with self.database.transaction() as session:
            pending = (
                await session.execute(
                    text(
                        "SELECT id, phone, email FROM identity_codes WHERE ticket_hash = :hash "
                        "AND ticket_consumed_at IS NULL AND ticket_expires_at > :now"
                    ),
                    {"hash": token_digest(ticket), "now": now},
                )
            ).one_or_none()
        if pending is None:
            raise ApplicationError("expired")
        # A weak password leaves the ticket usable, so the person only picks another.
        problem = password_problem(password, (pending.phone, pending.email))
        if problem is not None:
            raise _field("password", problem)
        hashed = await self.hasher.hash(password)
        client_id = push_id(self.clock)
        try:
            async with self.database.transaction() as session:
                spent = await session.scalar(
                    text(
                        "UPDATE identity_codes SET ticket_consumed_at = :now WHERE id = :id "
                        "AND ticket_consumed_at IS NULL AND ticket_expires_at > :now "
                        "RETURNING true"
                    ),
                    {"now": now, "id": pending.id},
                )
                if not spent:
                    raise ApplicationError("expired")
                await session.execute(
                    text(
                        "INSERT INTO identity_clients (id, phone, email, password_hash, "
                        "password_changed_at, created_at) "
                        "VALUES (:id, :phone, :email, :hash, :now, :now)"
                    ),
                    {
                        "id": client_id,
                        "phone": pending.phone,
                        "email": pending.email,
                        "hash": hashed,
                        "now": now,
                    },
                )
                await trail.note(
                    session,
                    self.clock,
                    Actor("client", client_id),
                    "identity.client.created",
                    resource_type="client",
                    resource_id=client_id,
                )
                issued = await self.identity.issue_in(
                    session, Actor("client", client_id), "signup", transport, now
                )
        except IntegrityError:
            # Someone finished signing up with this number in the meantime.
            raise ApplicationError("phone_taken") from None
        logger.info("client_created", client_id=client_id)
        return issued

    # The sign-in fallback

    async def send_login_code(self, login_id: str, channel: Channel) -> None:
        """Send a code to finish a sign-in that the password step started."""
        actor = await self.logins.login_actor(login_id, "client")
        await reserve_attempts(
            self.database,
            self.clock,
            [(throttle_key("login-code-send", login_id), SENDS_PER_LOGIN)],
        )
        async with self.database.transaction() as session:
            account = (
                await session.execute(
                    text("SELECT phone, email FROM identity_clients WHERE id = :id"),
                    {"id": actor.id},
                )
            ).one()
            # Email only if the client gave an address. Otherwise the code is texted.
            by_email = channel == "email" and account.email is not None
            await self._issue(
                session,
                "login",
                "email" if by_email else "sms",
                recipient=account.email if by_email else account.phone,
                actor_id=actor.id,
                bound_to=login_id,
            )
        logger.info("login_code_sent", login_id=login_id)

    async def verify_login_code(
        self, login_id: str, code: str, transport: Transport
    ) -> IssuedSession:
        """Finish sign-in with the code. The account is then restricted for 24 hours."""
        await self.logins.login_actor(login_id, "client")
        async with self.database.transaction() as session:
            row = await self._spend(
                session, "bound_to = :bound AND purpose = 'login'", {"bound": login_id}, code
            )
        if row is None:
            raise self._wrong("login")
        return await self.identity.open_session(
            login_id, "client", second_step="code", transport=transport
        )

    # A payout account

    async def send_payout_account_code(
        self, session: AsyncSession, *, client_id: str, account_id: str, phone: str
    ) -> None:
        """Text a code to prove the client holds a new payout account, in the caller's
        transaction. `phone` is the mobile money number itself, or for a bank account the
        client's own number. A new code replaces the one before it.

        Sends are limited for the account, for the client and for the number texted, every
        fifteen minutes, and for the client every day.
        """
        now = self.clock.now()
        today = (
            await session.execute(
                text(
                    "SELECT count(*) AS sent, min(created_at) AS oldest FROM identity_codes "
                    "WHERE purpose = 'payout_account' AND actor_id = :actor AND created_at > :since"
                ),
                {"actor": client_id, "since": now - PAYOUT_CODE_DAY},
            )
        ).one()
        if today.sent >= PAYOUT_CODES_PER_DAY:
            wait = today.oldest + PAYOUT_CODE_DAY - now
            raise ApplicationError(
                "rate_limited", retry_after_seconds=max(1, int(wait.total_seconds()) + 1)
            )
        await reserve_attempts(
            self.database,
            self.clock,
            [
                (throttle_key("payout-account-code", account_id), SENDS_PER_PAYOUT_ACCOUNT),
                (throttle_key("payout-account-client", client_id), SENDS_PER_PAYOUT_CLIENT),
                (throttle_key("payout-account-number", phone), SENDS_PER_NUMBER),
            ],
        )
        await self._issue(
            session,
            "payout_account",
            "sms",
            recipient=phone,
            phone=phone,
            actor_id=client_id,
            bound_to=account_id,
        )

    async def spend_payout_account_code(
        self, session: AsyncSession, *, client_id: str, account_id: str, code: str
    ) -> bool:
        """Count a try and spend the code if it is right, in the caller's transaction.

        The caller must commit even when this returns False, so the try is counted. Raises
        `expired` when no live code is left.
        """
        row = await self._spend(
            session,
            "bound_to = :bound AND actor_id = :actor AND purpose = 'payout_account'",
            {"bound": account_id, "actor": client_id},
            code,
        )
        if row is None:
            logger.info("code_refused", purpose="payout_account", error_code="invalid_code")
            return False
        return True

    # A client moving their account to a new mobile number

    async def send_phone_change_code(
        self, session: AsyncSession, *, client_id: str, session_id: str, phone: str
    ) -> str:
        """Text a code to the new number, in the caller's transaction. Returns its ID.

        The code belongs to the client and the session that asked. A new one for the same
        session replaces the one before it. Sends are limited for the client and for the number
        every fifteen minutes, and for the client every day.
        """
        now = self.clock.now()
        today = (
            await session.execute(
                text(
                    "SELECT count(*) AS sent, min(created_at) AS oldest FROM identity_codes "
                    "WHERE purpose = 'phone_change' AND actor_id = :actor AND created_at > :since"
                ),
                {"actor": client_id, "since": now - PAYOUT_CODE_DAY},
            )
        ).one()
        if today.sent >= PHONE_CHANGE_CODES_PER_DAY:
            wait = today.oldest + PAYOUT_CODE_DAY - now
            raise ApplicationError(
                "rate_limited", retry_after_seconds=max(1, int(wait.total_seconds()) + 1)
            )
        await reserve_attempts(
            self.database,
            self.clock,
            [
                (throttle_key("phone-change-client", client_id), SENDS_PER_NUMBER),
                (throttle_key("phone-change-number", phone), SENDS_PER_NUMBER),
            ],
        )
        return await self._issue(
            session,
            "phone_change",
            "sms",
            recipient=phone,
            phone=phone,
            actor_id=client_id,
            bound_to=session_id,
        )

    async def spend_phone_change_code(
        self, session: AsyncSession, *, client_id: str, code_id: str, code: str
    ) -> bool:
        """Count a try and spend the code if it is right, in the caller's transaction.

        The caller must commit even when this returns False, so the try is counted. Raises
        `expired` when the code is spent, replaced or too old.
        """
        row = await self._spend(
            session,
            "id = :id AND actor_id = :actor AND purpose = 'phone_change'",
            {"id": code_id, "actor": client_id},
            code,
        )
        if row is None:
            logger.info("code_refused", purpose="phone_change", error_code="invalid_code")
            return False
        return True

    # Password reset

    async def reset_start(self, phone: str, *, address: str | None) -> str:
        """Text a reset code if the number has an account. The answer never says whether."""
        try:
            phone = normalize_phone(phone)
        except InvalidIdentifier:
            raise _field("phone") from None
        await self._limit_sends("reset", phone, address)
        async with self.database.transaction() as session:
            client_id = await session.scalar(
                text("SELECT id FROM identity_clients WHERE phone = :phone AND status = 'active'"),
                {"phone": phone},
            )
            code_id = await self._issue(
                session,
                "password_reset",
                "sms",
                recipient=phone if client_id else None,
                actor_id=client_id,
            )
        logger.info("password_reset_started", challenge_id=code_id)
        return code_id

    async def reset_complete(self, challenge_id: str, code: str, password: str) -> None:
        """Set a new password with the code. Every session ends and the account is restricted."""
        # The length rule is checked before a try is spent. The rest needs the account.
        problem = password_problem(password)
        if problem is not None:
            raise _field("password", problem)
        hashed = await self.hasher.hash(password)
        now = self.clock.now()
        async with self.database.transaction() as session:
            row = await self._spend(
                session, "id = :id AND purpose = 'password_reset'", {"id": challenge_id}, code
            )
            if row is not None and row.actor_id is not None:
                await session.execute(
                    text(
                        "UPDATE identity_clients SET password_hash = :hash, "
                        "password_changed_at = :now, restricted_until = "
                        "greatest(coalesce(restricted_until, :until), :until) WHERE id = :id"
                    ),
                    {"hash": hashed, "now": now, "until": now + RESTRICTION, "id": row.actor_id},
                )
                await session.execute(
                    text(
                        "UPDATE identity_sessions SET revoked_at = :now, revoked_reason = "
                        "'password_reset' WHERE actor_kind = 'client' AND actor_id = :id "
                        "AND revoked_at IS NULL"
                    ),
                    {"now": now, "id": row.actor_id},
                )
                await trail.note(
                    session,
                    self.clock,
                    Actor("client", row.actor_id),
                    "identity.password.reset",
                    resource_type="client",
                    resource_id=row.actor_id,
                    details={"channel": "sms"},
                )
        if row is None or row.actor_id is None:
            raise self._wrong("password_reset")
        logger.info("password_reset", client_id=row.actor_id)

    # Claiming an account carried over from Finvest 1.0

    async def claim_start(self, client_id: str, *, address: str | None) -> str:
        """Text a code to the number of an unclaimed account. Returns the challenge ID.

        The caller has checked the claim link, so it may learn whether the account is still
        unclaimed: `not_found` means it was claimed, closed or never existed.
        """
        await reserve_attempts(
            self.database,
            self.clock,
            [
                (throttle_key("claim-client", client_id), SENDS_PER_NUMBER),
                *(
                    [(throttle_key("code-address", address), SENDS_PER_ADDRESS)]
                    if address is not None
                    else []
                ),
            ],
        )
        async with self.database.transaction() as session:
            phone = await session.scalar(
                text("SELECT phone FROM identity_clients WHERE id = :id AND status = 'unclaimed'"),
                {"id": client_id},
            )
            if phone is None:
                raise ApplicationError("not_found")
            code_id = await self._issue(
                session, "claim", "sms", recipient=phone, actor_id=client_id, bound_to=client_id
            )
        logger.info("claim_started", challenge_id=code_id)
        return code_id

    async def claim_complete(
        self, challenge_id: str, code: str, password: str, transport: Transport, *, client_id: str
    ) -> Claimed:
        """Check the code, set the password and open the first session, all at once.

        `client_id` is the account the caller expects, from the claim link. A challenge for any
        other account is refused with `not_found` before anything changes.

        The account becomes active. A code proves only the number, so, as with a code sign-in,
        withdrawals are limited to known accounts for 24 hours.
        """
        async with self.database.transaction() as session:
            account = (
                await session.execute(
                    text(
                        "SELECT c.id, c.phone, c.email FROM identity_codes k "
                        "JOIN identity_clients c ON c.id = k.actor_id "
                        "WHERE k.id = :id AND k.purpose = 'claim'"
                    ),
                    {"id": challenge_id},
                )
            ).one_or_none()
        if account is None:
            raise ApplicationError("expired")
        if account.id != client_id:
            # A code for another account, sent with this account's link. Nothing changes.
            raise ApplicationError("not_found")
        # Hashing is slow on purpose. Limit how often one claim can make us do it.
        await reserve_attempts(
            self.database,
            self.clock,
            [(throttle_key("claim-complete", challenge_id), CLAIM_COMPLETIONS)],
        )
        # A weak password leaves the code unspent, so the person only picks another.
        problem = password_problem(password, (account.phone, account.email))
        if problem is not None:
            raise _field("password", problem)
        hashed = await self.hasher.hash(password)
        now = self.clock.now()
        client = Actor("client", account.id)
        async with self.database.transaction() as session:
            row = await self._spend(
                session, "id = :id AND purpose = 'claim'", {"id": challenge_id}, code
            )
            claimed = False
            if row is not None:
                # The API cannot change a status, so the claim goes through this function.
                claimed = bool(
                    await session.scalar(
                        text("SELECT identity_claim_client(:id, :code, :hash, :now, :until)"),
                        {
                            "id": client.id,
                            "code": row.id,
                            "hash": hashed,
                            "now": now,
                            "until": now + RESTRICTION,
                        },
                    )
                )
            if claimed:
                await trail.note(
                    session,
                    self.clock,
                    client,
                    "identity.client.claimed",
                    resource_type="client",
                    resource_id=client.id,
                )
                issued = await self.identity.issue_in(session, client, "code", transport, now)
        # Raised after the transaction, so the counted try and the spent code are kept.
        if row is None:
            raise self._wrong("claim")
        if not claimed:
            raise ApplicationError("expired")
        logger.info("client_claimed", client_id=client.id)
        return Claimed(account.id, issued)
