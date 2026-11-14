"""Put together a copy of what Finvest holds about a client, for the client to download.

Everything is read through the functions the owning modules expose, inside the client's own
transaction, so row policies fence out anyone else. Only what the client may already see is
included: no provider answers, review flags, staff names or tax rates. Nothing is written but
the audit event that records the download.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.sealing import Sealer
from app.modules.audit import service as audit
from app.modules.audit.domain.events import AuditActor, AuditEvent
from app.modules.autoinvest.plans import client_plans
from app.modules.closures import service as closures
from app.modules.goals.service import client_goals
from app.modules.identity import client_locks
from app.modules.investments.holdings import client_investments
from app.modules.kyc import next_of_kin, risk
from app.modules.kyc.service import check_for_client
from app.modules.notifications.preferences import channels_for_client
from app.modules.payouts.destinations import destinations_for_client
from app.modules.payouts.payouts import payouts_for_client
from app.shared.actors import Actor
from app.shared.clock import Clock
from app.shared.errors import ApplicationError
from app.shared.money import Money

FORMAT = "finvest-client-data/1"
# The most recent this many investments and payouts are in the copy. When a list is this long,
# `truncated` says so, and the client can ask support for the rest.
MOST_RECENT = 100


def _money(amount: Money) -> dict[str, Any]:
    return {"amount_minor": amount.minor, "currency": amount.currency.value}


def _when(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


async def client_copy(
    session: AsyncSession, sealer: Sealer, client: Actor, clock: Clock
) -> dict[str, Any]:
    """Everything about the signed-in client, as plain JSON values, and the download recorded."""
    if client.kind != "client" or client.id is None:
        raise ApplicationError("forbidden")
    client_id = client.id
    account = await client_locks.client_account(session, client_id)
    if account is None:
        raise ApplicationError("not_found")
    check = await check_for_client(session, sealer, client)
    kin = await next_of_kin.next_of_kin(session, sealer, client_id)
    answers = await risk.history(session, client_id)
    investments = await client_investments(session, client, clock, limit=MOST_RECENT)
    payouts = await payouts_for_client(session, client_id, limit=MOST_RECENT)
    copy: dict[str, Any] = {
        "format": FORMAT,
        "made_at": clock.now().isoformat(),
        "account": {
            "id": account.id,
            "phone": account.phone,
            "email": account.email,
            "status": account.status,
            "created_at": _when(account.created_at),
            "has_passkey": account.has_passkey,
            "has_authenticator": account.has_authenticator,
        },
        "identity_check": None
        if check is None
        else {
            "status": check.status.value,
            "document_type": check.document_type.value,
            "document_country": check.document_country,
            "details": None
            if check.details is None
            else {
                "surname": check.details.surname,
                "given_names": check.details.given_names,
                "document_number": check.details.document_number,
                "date_of_birth": _when(check.details.date_of_birth),
                "document_expires_on": _when(check.details.document_expires_on),
                "tin": check.details.tin,
            },
            "submitted_at": _when(check.submitted_at),
            "decided_at": _when(check.decided_at),
            "account_number": check.account_number,
        },
        "next_of_kin": [
            {
                "full_name": p.full_name,
                "relationship": p.relationship.value,
                "phone": p.phone,
                "id_number": p.id_number,
                "share_bps": p.share_bps,
            }
            for p in kin.people
        ],
        "investing_questions": [
            {
                "answers": a.answers,
                "profile": a.profile.value,
                "answered_at": _when(a.answered_at),
            }
            for a in answers
        ],
        "investments": [
            {
                "id": i.id,
                "product": i.product_name,
                "status": i.status.value,
                "amount": _money(i.amount),
                "rate_percent_after_tax": i.rate_percent,
                "term_days": i.term_days,
                "start_date": _when(i.start_date),
                "maturity_date": _when(i.maturity_date),
                "interest_so_far": _money(i.interest_so_far),
                "value": _money(i.value),
            }
            for i in investments
        ],
        "goals": [
            {
                "id": g.id,
                "name": g.name,
                "category": g.category.value,
                "target": _money(g.target),
                "target_date": _when(g.target_date),
                "status": g.status.value,
                "created_at": _when(g.created_at),
            }
            for g in await client_goals(session, client, clock)
        ],
        "automatic_investing_plans": [
            {
                "id": p.id,
                "status": p.status.value,
                "amount": _money(p.amount),
                "product_code": p.product_code,
            }
            for p in await client_plans(session, client, include_stopped=True)
        ],
        "payout_accounts": [
            {
                "id": d.id,
                "kind": d.kind.value,
                "network": d.network,
                "bank_code": d.bank_code,
                "account": d.masked_account,
                "holder_name": d.holder_name,
                "status": d.status.value,
            }
            for d in await destinations_for_client(session, client_id)
        ],
        "payouts": [
            {
                "id": p.id,
                "amount": _money(p.amount),
                "status": p.status.value,
                "date": _when(p.business_date),
                "created_at": _when(p.created_at),
            }
            for p in payouts
        ],
        "notification_channels": {
            str(category): sorted(str(channel) for channel in channels)
            for category, channels in (await channels_for_client(session, client_id)).items()
        },
        "account_closure": None,
        "truncated": {
            "investments": len(investments) >= MOST_RECENT,
            "payouts": len(payouts) >= MOST_RECENT,
        },
    }
    closure = await closures.latest(session, client_id)
    if closure is not None:
        copy["account_closure"] = {
            "status": closure.status,
            "reason": closure.reason,
            "requested_at": _when(closure.requested_at),
            "decided_at": _when(closure.decided_at),
        }
    await audit.record(
        session,
        AuditEvent(
            category="access",
            action="client_data.exported",
            resource_type="client_data",
            resource_id=client_id,
            actor=AuditActor.client(client_id),
        ),
        clock,
    )
    return copy
