"""Changing a client's mobile number: one member of staff proposes, a different one approves.

The number is how a client proves who they are with a code, so one person alone must never be
able to point it at a phone they hold. The database enforces the two people and makes the
change itself (migration 0160). When the number changes, the client's sessions end, codes
already sent stop working, withdrawals go only to known accounts for 24 hours, and the old
number and the email on file are told.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database import Database
from app.modules.identity import trail
from app.modules.identity.domain.accounts import InvalidIdentifier, normalize_phone
from app.modules.identity.domain.sessions import RESTRICTION
from app.modules.notifications.service import Channel, queue_message
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

# A proposal nobody decides in this time lapses, so an old one cannot be approved by surprise.
PROPOSAL_LIFETIME = timedelta(hours=24)

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class PhoneChange:
    id: str
    client_id: str
    new_phone: str
    reason: str
    proposed_by: str
    proposed_at: datetime
    expires_at: datetime


def _staff(actor: Actor) -> str:
    if actor.kind != "staff" or actor.id is None:
        raise ValueError("Only a member of staff changes a client's number")
    return actor.id


class PhoneChangeService:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    async def propose(self, actor: Actor, client_id: str, phone: str, reason: str) -> str:
        """Propose a new number. Nothing changes until someone else approves it."""
        staff_id = _staff(actor)
        try:
            phone = normalize_phone(phone)
        except InvalidIdentifier:
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["body", "phone"], code="invalid")],
            ) from None
        reason = reason.strip()
        if not reason:
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["body", "reason"], code="required")],
            )
        now = self.clock.now()
        change_id = push_id(self.clock)
        try:
            async with self.database.actor_transaction(actor) as session:
                client = (
                    await session.execute(
                        text(
                            "SELECT phone FROM identity_clients "
                            "WHERE id = :id AND status <> 'closed'"
                        ),
                        {"id": client_id},
                    )
                ).one_or_none()
                if client is None:
                    raise ApplicationError("not_found")
                taken = await session.scalar(
                    text("SELECT true FROM identity_clients WHERE phone = :phone"),
                    {"phone": phone},
                )
                if taken:
                    raise ApplicationError("phone_taken")
                await session.execute(
                    text(
                        "INSERT INTO identity_phone_changes (id, client_id, new_phone, reason, "
                        "proposed_by, proposed_at, expires_at) VALUES (:id, :client, :phone, "
                        ":reason, :staff, :now, :expires)"
                    ),
                    {
                        "id": change_id,
                        "client": client_id,
                        "phone": phone,
                        "reason": reason,
                        "staff": staff_id,
                        "now": now,
                        "expires": now + PROPOSAL_LIFETIME,
                    },
                )
                await trail.note(
                    session,
                    self.clock,
                    actor,
                    "identity.client.phone_change_proposed",
                    resource_type="client",
                    resource_id=client_id,
                    details={"change_id": change_id},
                )
        except IntegrityError:
            # Another change for this client is already waiting.
            raise ApplicationError("conflict") from None
        logger.info("phone_change_proposed", change_id=change_id)
        return change_id

    async def pending(self) -> list[PhoneChange]:
        """Proposals waiting for a decision, newest first."""
        async with self.database.transaction() as session:
            rows = await session.execute(
                text(
                    "SELECT id, client_id, new_phone, reason, proposed_by, proposed_at, "
                    "expires_at FROM identity_phone_changes WHERE status = 'proposed' "
                    "AND expires_at > :now ORDER BY proposed_at DESC LIMIT 200"
                ),
                {"now": self.clock.now()},
            )
            return [PhoneChange(*row) for row in rows]

    async def approve(self, actor: Actor, change_id: str) -> None:
        """Approve someone else's proposal. The number changes in the same transaction."""
        staff_id = _staff(actor)
        now = self.clock.now()
        try:
            async with self.database.actor_transaction(actor) as session:
                change = await self._decide(session, change_id, staff_id, "approved", None, now)
                old_phone = await session.scalar(
                    text("SELECT identity_apply_phone_change(:id, :until)"),
                    {"id": change_id, "until": now + RESTRICTION},
                )
                email = await session.scalar(
                    text("SELECT email FROM identity_clients WHERE id = :id"),
                    {"id": change.client_id},
                )
                variables = {"ending": change.new_phone[-3:]}
                sends: tuple[tuple[Channel, str | None], ...] = (
                    ("sms", old_phone),
                    ("email", email),
                )
                for channel, recipient in sends:
                    if recipient:
                        await queue_message(
                            session,
                            channel=channel,
                            recipient=recipient,
                            template="phone_changed",
                            variables=variables,
                            idempotency_key=f"phone-change:{change_id}:{channel}",
                            now=now,
                        )
                await trail.note(
                    session,
                    self.clock,
                    actor,
                    "identity.client.phone_changed",
                    resource_type="client",
                    resource_id=change.client_id,
                    details={"change_id": change_id},
                )
        except IntegrityError:
            # The number was given to another account after the proposal.
            raise ApplicationError("phone_taken") from None
        logger.info("phone_change_approved", change_id=change_id)

    async def reject(self, actor: Actor, change_id: str, note: str) -> None:
        """Turn down someone else's proposal, with the reason."""
        staff_id = _staff(actor)
        note = note.strip()
        if not note:
            raise ApplicationError(
                "validation_failed",
                fields=[FieldProblem(field_path=["body", "note"], code="required")],
            )
        async with self.database.actor_transaction(actor) as session:
            change = await self._decide(
                session, change_id, staff_id, "rejected", note, self.clock.now()
            )
            await trail.note(
                session,
                self.clock,
                actor,
                "identity.client.phone_change_rejected",
                resource_type="client",
                resource_id=change.client_id,
                details={"change_id": change_id},
            )
        logger.info("phone_change_rejected", change_id=change_id)

    async def _decide(  # type: ignore[no-untyped-def]
        self, session, change_id: str, staff_id: str, status: str, note: str | None, now: datetime
    ):
        row = (
            await session.execute(
                text(
                    "SELECT client_id, new_phone, proposed_by, expires_at "
                    "FROM identity_phone_changes WHERE id = :id AND status = 'proposed' FOR UPDATE"
                ),
                {"id": change_id},
            )
        ).one_or_none()
        if row is None:
            raise ApplicationError("not_found")
        if row.proposed_by == staff_id:
            # The database refuses it too. Say why, rather than fail on the constraint.
            raise ApplicationError("forbidden")
        if row.expires_at <= now:
            raise ApplicationError("expired")
        await session.execute(
            text(
                "UPDATE identity_phone_changes SET status = :status, decided_by = :staff, "
                "decided_at = :now, decision_note = :note WHERE id = :id"
            ),
            {"status": status, "staff": staff_id, "now": now, "note": note, "id": change_id},
        )
        return row
