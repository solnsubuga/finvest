"""Proving a client's email address with a one-time link.

Statements and receipts go only to an address the client has proved. The link carries a random
token in its fragment, so it never reaches a server log; only the token's SHA-256 is stored. It
works once, for 24 hours, and only while the account still has the address it was sent to.
"""

from datetime import datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database import Database
from app.infrastructure.throttling import reserve_attempts, throttle_key
from app.modules.identity import trail
from app.modules.identity.domain.sessions import new_token, token_digest
from app.modules.notifications.service import queue_message
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

LINK_LIFETIME = timedelta(hours=24)
VERIFY_PATH = "/verify-email"
# Links one client may ask for in fifteen minutes.
LINKS_PER_CLIENT = 3

logger = structlog.get_logger()


class EmailCheckService:
    def __init__(self, database: Database, clock: Clock, app_origin: str | None) -> None:
        self.database = database
        self.clock = clock
        self.app_origin = app_origin

    async def send_link(self, client_id: str) -> bool:
        """Email a link to the client's address. False when it is already proved."""
        if not self.app_origin:
            # Without the client app's address there is no link to send.
            raise ApplicationError("unavailable")
        await reserve_attempts(
            self.database,
            self.clock,
            [(throttle_key("email-check", client_id), LINKS_PER_CLIENT)],
        )
        now = self.clock.now()
        token = new_token()
        check_id = push_id(self.clock)
        # Acting for the client, whose own links are the only ones its row policy shows.
        async with self.database.actor_transaction(Actor("client", client_id)) as session:
            account = (
                await session.execute(
                    text(
                        "SELECT email, email_verified_at FROM identity_clients "
                        "WHERE id = :id AND status = 'active'"
                    ),
                    {"id": client_id},
                )
            ).one_or_none()
            if account is None:
                raise ApplicationError("not_found")
            if account.email is None:
                raise ApplicationError(
                    "validation_failed",
                    fields=[FieldProblem(field_path=["email"], code="missing")],
                )
            if account.email_verified_at is not None:
                return False
            # A new link replaces any older one.
            await session.execute(
                text(
                    "UPDATE identity_email_checks SET consumed_at = :now "
                    "WHERE client_id = :id AND consumed_at IS NULL"
                ),
                {"now": now, "id": client_id},
            )
            await session.execute(
                text(
                    "INSERT INTO identity_email_checks (id, client_id, email, token_hash, "
                    "created_at, expires_at) VALUES (:id, :client, :email, :hash, :now, "
                    ":expires)"
                ),
                {
                    "id": check_id,
                    "client": client_id,
                    "email": account.email,
                    "hash": token_digest(token),
                    "now": now,
                    "expires": now + LINK_LIFETIME,
                },
            )
            await queue_message(
                session,
                channel="email",
                recipient=account.email,
                template="email_verification",
                variables={
                    "link": f"{self.app_origin}{VERIFY_PATH}#{token}",
                    "hours": int(LINK_LIFETIME / timedelta(hours=1)),
                },
                idempotency_key=f"email-check:{check_id}",
                now=now,
                deliver_within=LINK_LIFETIME,
            )
        logger.info("email_check_sent", client_id=client_id)
        return True

    async def confirm(self, token: str) -> None:
        """Spend the link and mark the address proved. Anything wrong is `expired`."""
        now = self.clock.now()
        try:
            client_id = await self._spend(token, now)
        except IntegrityError:
            # Another account proved this address first. An address signs in one account only.
            logger.info("email_check_refused", error_code="email_taken")
            raise ApplicationError("email_taken") from None
        if client_id is None:
            raise ApplicationError("expired")
        logger.info("email_verified", client_id=client_id)

    async def _spend(self, token: str, now: datetime) -> str | None:
        async with self.database.transaction() as session:
            client_id: str | None = await session.scalar(
                text("SELECT identity_verify_email(:hash, :now)"),
                {"hash": token_digest(token), "now": now},
            )
            if client_id is not None:
                await trail.note(
                    session,
                    self.clock,
                    Actor("client", client_id),
                    "identity.email.verified",
                    resource_type="client",
                    resource_id=client_id,
                )
        return client_id
