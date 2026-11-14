"""Requests from people who cannot get into their account, and staff closing them.

Anyone can ask, without signing in, so the answer is the same whether or not the number has an
account, and asking is limited per caller address and per number. A request for a number with an
account tells the account's own number (and proved email) that someone asked, so an owner who
still has their phone hears of a stranger trying. An account has one open request at a time,
and it is told only when that request opens, so knowing a client's number is not a way to send
them a stream of texts.

Known limit: an ask for a number with an account does a little more work (a read and two
queued messages) than one without, so its response time can hint at whether the number has an
account. The limits per number and per caller address keep that slow to use.

Staff call the person back on the number they gave, check who they are, and change the number
through the two-person phone change.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import structlog
from sqlalchemy import text

from app.infrastructure.database import Database
from app.infrastructure.throttling import reserve_attempts, throttle_key
from app.modules.identity import trail
from app.modules.identity.domain.accounts import InvalidIdentifier, normalize_phone
from app.modules.identity.domain.devices import kampala_time
from app.modules.notifications.service import queue_message
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

Reason = Literal["lost_phone", "new_number", "no_codes", "other"]
Outcome = Literal["number_changed", "helped", "not_the_client"]

# Requests one caller address may send, and requests for one number, every fifteen minutes.
REQUESTS_PER_ADDRESS = 5
REQUESTS_PER_NUMBER = 2

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class RecoveryRequest:
    id: str
    client_id: str
    # The number on the account, and the one the person asked to be called on.
    account_phone: str
    contact_phone: str
    reason: Reason
    created_at: datetime


def _phone(value: str, field: str) -> str:
    try:
        return normalize_phone(value)
    except InvalidIdentifier:
        raise ApplicationError(
            "validation_failed", fields=[FieldProblem(field_path=["body", field], code="invalid")]
        ) from None


class RecoveryService:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    async def request(
        self, *, phone: str, contact_phone: str, reason: Reason, address: str | None
    ) -> None:
        """Record a request. Says nothing about whether `phone` has an account.

        The account is told only when this opens its request: while one is already open, the
        database records nothing new and answers with no account.
        """
        phone = _phone(phone, "phone")
        contact_phone = _phone(contact_phone, "contact_phone")
        keys = [(throttle_key("recovery-number", phone), REQUESTS_PER_NUMBER)]
        if address is not None:
            keys.append((throttle_key("recovery-from", address), REQUESTS_PER_ADDRESS))
        await reserve_attempts(self.database, self.clock, keys)

        now = self.clock.now()
        request_id = push_id(self.clock)
        async with self.database.transaction() as session:
            client_id = await session.scalar(
                text("SELECT identity_request_recovery(:id, :phone, :contact, :reason, :now)"),
                {
                    "id": request_id,
                    "phone": phone,
                    "contact": contact_phone,
                    "reason": reason,
                    "now": now,
                },
            )
            if client_id is not None:
                email = await session.scalar(
                    text(
                        "SELECT CASE WHEN email_verified_at IS NOT NULL THEN email END "
                        "FROM identity_clients WHERE id = :id"
                    ),
                    {"id": client_id},
                )
                for channel, recipient in (("sms", phone), ("email", email)):
                    if recipient is None:
                        continue
                    await queue_message(
                        session,
                        channel=channel,  # type: ignore[arg-type]
                        recipient=recipient,
                        template="recovery_requested",
                        variables={"when": kampala_time(now)},
                        idempotency_key=f"recovery:{request_id}:{channel}",
                        now=now,
                    )
        logger.info("recovery_requested", request_id=request_id)

    async def open_requests(self, actor: Actor) -> list[RecoveryRequest]:
        """Requests for an account that staff have not closed, oldest first."""
        async with self.database.actor_transaction(actor) as session:
            rows = await session.execute(
                text(
                    "SELECT r.id, r.client_id, c.phone AS account_phone, r.contact_phone, "
                    "r.reason, r.created_at FROM identity_recovery_requests r "
                    "JOIN identity_clients c ON c.id = r.client_id "
                    "WHERE r.status = 'open' ORDER BY r.created_at, r.id LIMIT 200"
                )
            )
            return [RecoveryRequest(*row) for row in rows]

    async def close(self, actor: Actor, request_id: str, outcome: Outcome) -> None:
        """Close a request once it has been followed up."""
        now = self.clock.now()
        async with self.database.actor_transaction(actor) as session:
            client_id = await session.scalar(
                text(
                    "UPDATE identity_recovery_requests SET status = 'closed', closed_at = :now, "
                    "closed_by = :staff, outcome = :outcome "
                    "WHERE id = :id AND status = 'open' RETURNING client_id"
                ),
                {"now": now, "staff": actor.id, "outcome": outcome, "id": request_id},
            )
            if client_id is None:
                raise ApplicationError("not_found")
            await trail.note(
                session,
                self.clock,
                actor,
                "identity.client.recovery_closed",
                resource_type="client",
                resource_id=client_id,
                details={"request_id": request_id, "outcome": outcome},
            )
        logger.info("recovery_closed", request_id=request_id, outcome=outcome)
