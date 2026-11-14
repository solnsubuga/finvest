"""A signed-in client changes their own password, mobile number and email address.

Each change needs a passkey or authenticator app used on this session in the last ten minutes
(`current_confirmed_client`), never only a texted or emailed code, because whoever holds the SIM
could have that. The owner chose this check on a decision card. A password or number change
also needs a live selfie matched to the face on file, when there is one; the routes check it
(app/modules/kyc/face_confirmations_api.py, docs/sign-in.md#changing-how-you-sign-in).

* **Password.** The current password as well, then the usual rules for a new one. Every other
  session ends. The client is told by text and email.
* **Mobile number.** A code to the new number proves the client holds it. The number then moves
  through `identity_change_own_phone` (migration 0236), which ends every other session, voids
  codes already sent and limits withdrawals to known accounts for a day, as a number changed by
  staff does. The old number and the email on file are told.
* **Email address.** Set through `identity_change_own_email`. The new address is not proved
  until its link is opened, and the address it replaced is told.
"""

from datetime import datetime

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.identity import trail
from app.modules.identity.codes import CodeService
from app.modules.identity.domain.accounts import InvalidIdentifier, normalize_email, normalize_phone
from app.modules.identity.domain.devices import kampala_time
from app.modules.identity.domain.passwords import password_problem
from app.modules.identity.domain.sessions import RESTRICTION
from app.modules.identity.email_checks import EmailCheckService
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.service import ClientSession
from app.modules.notifications.service import Channel, queue_message
from app.shared.clock import Clock
from app.shared.errors import ApplicationError, FieldProblem
from app.shared.push_ids import push_id

logger = structlog.get_logger()


def _field(name: str, code: str = "invalid") -> ApplicationError:
    return ApplicationError(
        "validation_failed", fields=[FieldProblem(field_path=[name], code=code)]
    )


class OwnChangeService:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        hasher: PasswordHasher,
        codes: CodeService,
        email_checks: EmailCheckService,
    ) -> None:
        self.database = database
        self.clock = clock
        self.hasher = hasher
        self.codes = codes
        self.email_checks = email_checks

    async def change_password(self, client: ClientSession, current: str, new: str) -> None:
        """Replace the password. A wrong current password is `invalid_credentials`."""
        async with self.database.actor_transaction(client.actor) as session:
            stored = await session.scalar(
                text("SELECT password_hash FROM identity_clients WHERE id = :id"),
                {"id": client.client_id},
            )
        if not await self.hasher.verify(current, stored):
            logger.info("password_change_refused", error_code="invalid_credentials")
            raise ApplicationError("invalid_credentials")
        problem = password_problem(new, (client.phone, client.email))
        if problem is not None:
            raise _field("new_password", problem)
        hashed = await self.hasher.hash(new)
        now = self.clock.now()
        change_id = push_id(self.clock)
        async with self.database.actor_transaction(client.actor) as session:
            await session.execute(
                text(
                    "UPDATE identity_clients SET password_hash = :hash, password_changed_at = :now "
                    "WHERE id = :id AND status = 'active'"
                ),
                {"hash": hashed, "now": now, "id": client.client_id},
            )
            await self._end_other_sessions(session, client, "password_changed", now)
            await trail.note(
                session,
                self.clock,
                client.actor,
                "identity.password.changed",
                resource_type="client",
                resource_id=client.client_id,
            )
            variables = {"when": kampala_time(now)}
            sends: tuple[tuple[Channel, str | None], ...] = (
                ("sms", client.phone),
                ("email", client.email),
            )
            for channel, recipient in sends:
                if recipient:
                    await queue_message(
                        session,
                        channel=channel,
                        recipient=recipient,
                        template="password_changed",
                        variables=variables,
                        idempotency_key=f"password-changed:{change_id}:{channel}",
                        now=now,
                    )
        logger.info("password_changed")

    async def start_phone_change(self, client: ClientSession, phone: str) -> str:
        """Text a code to the new number. Returns the ID the code is confirmed with."""
        try:
            phone = normalize_phone(phone)
        except InvalidIdentifier:
            raise _field("phone") from None
        if phone == client.phone:
            raise _field("phone", "same_as_now")
        async with self.database.actor_transaction(client.actor) as session:
            code_id = await self.codes.send_phone_change_code(
                session, client_id=client.client_id, session_id=client.session_id, phone=phone
            )
        logger.info("phone_change_started", challenge_id=code_id)
        return code_id

    async def confirm_phone_change(self, client: ClientSession, code_id: str, code: str) -> str:
        """Spend the code and move the account to the new number. Returns the new number."""
        now = self.clock.now()
        new_phone: str | None = None
        try:
            async with self.database.actor_transaction(client.actor) as session:
                # A wrong code returns False and the transaction commits, so the try counts.
                if await self.codes.spend_phone_change_code(
                    session,
                    client_id=client.client_id,
                    session_id=client.session_id,
                    code_id=code_id,
                    code=code,
                ):
                    new_phone = await session.scalar(
                        text("SELECT phone FROM identity_codes WHERE id = :id"), {"id": code_id}
                    )
                    await self._move_phone(session, client, code_id, str(new_phone), now)
        except IntegrityError:
            # The number already has an account. It was proved first, so saying so is safe.
            logger.info("phone_change_refused", error_code="phone_taken")
            raise ApplicationError("phone_taken") from None
        if new_phone is None:
            raise ApplicationError("invalid_code")
        logger.info("phone_changed_by_client")
        return new_phone

    async def _move_phone(
        self,
        session: AsyncSession,
        client: ClientSession,
        code_id: str,
        new_phone: str,
        now: datetime,
    ) -> None:
        old_phone = await session.scalar(
            text("SELECT identity_change_own_phone(:code, :session, :until)"),
            {"code": code_id, "session": client.session_id, "until": now + RESTRICTION},
        )
        await trail.note(
            session,
            self.clock,
            client.actor,
            "identity.phone.changed_by_client",
            resource_type="client",
            resource_id=client.client_id,
        )
        variables = {"ending": new_phone[-3:], "when": kampala_time(now)}
        sends: tuple[tuple[Channel, str | None], ...] = (
            ("sms", old_phone),
            ("email", client.email),
        )
        for channel, recipient in sends:
            if recipient:
                await queue_message(
                    session,
                    channel=channel,
                    recipient=recipient,
                    template="own_phone_changed",
                    variables=variables,
                    idempotency_key=f"own-phone-change:{code_id}:{channel}",
                    now=now,
                )

    async def change_email(self, client: ClientSession, email: str) -> str:
        """Set a new address and email it a link to prove it. Returns the address."""
        try:
            email = normalize_email(email)
        except InvalidIdentifier:
            raise _field("email") from None
        if email == client.email:
            raise _field("email", "same_as_now")
        now = self.clock.now()
        async with self.database.actor_transaction(client.actor) as session:
            old = await session.scalar(
                text("SELECT identity_change_own_email(:email)"), {"email": email}
            )
            await trail.note(
                session,
                self.clock,
                client.actor,
                "identity.email.changed",
                resource_type="client",
                resource_id=client.client_id,
            )
            if old:
                await queue_message(
                    session,
                    channel="email",
                    recipient=old,
                    template="email_changed",
                    variables={"when": kampala_time(now)},
                    idempotency_key=f"email-changed:{push_id(self.clock)}",
                    now=now,
                )
        # A new address is proved only by its link. Sent after the change is saved.
        await self.email_checks.send_link(client.client_id)
        logger.info("email_changed")
        return email

    async def _end_other_sessions(
        self, session: AsyncSession, client: ClientSession, reason: str, now: datetime
    ) -> None:
        await session.execute(
            text(
                "UPDATE identity_sessions SET revoked_at = :now, revoked_reason = :reason "
                "WHERE actor_kind = 'client' AND actor_id = :id AND revoked_at IS NULL "
                "AND id <> :session"
            ),
            {"now": now, "reason": reason, "id": client.client_id, "session": client.session_id},
        )
