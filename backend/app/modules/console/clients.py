"""One client as staff see them: finding a client, and the client page.

Everything here is read through the functions the owning modules expose. Nothing is written.
"""

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.client_locks import (
    ClientAccount,
    LockChange,
    client_account,
    client_accounts,
    client_by_phone,
    lock_history,
)
from app.modules.identity.freezes import Freeze
from app.modules.identity.freezes import freezes as client_freezes
from app.modules.kyc.service import (
    CheckSummary,
    check_summary,
    client_for_account_number,
    identities,
)
from app.modules.kyc.service import verified_name as kyc_verified_name
from app.modules.ledger.statements import ClientOwed, client_owed_as_of
from app.modules.payouts.destinations import DestinationView, destinations_for_client
from app.modules.support.complaints import ComplaintView, complaints_for_client
from app.modules.support.conversations import ConversationView, conversation_for_client
from app.shared.push_ids import PUSH_ID_PATTERN

_PUSH_ID = re.compile(PUSH_ID_PATTERN)
_ACCOUNT_NUMBER = re.compile(r"\d{10}")


@dataclass(frozen=True, slots=True)
class ClientPage:
    account: ClientAccount
    # The name on the approved identity document. None until the check is approved.
    verified_name: str | None
    identity_check: CheckSummary | None
    # What Finvest owes the client at the end of today, per ledger account.
    owed: list[ClientOwed]
    destinations: list[DestinationView]
    locks: list[LockChange]
    # Each time the client froze their own account, newest first.
    freezes: list[Freeze]
    # None when the member of staff may not see support or complaints.
    conversation: ConversationView | None
    complaints: list[ComplaintView] | None


@dataclass(frozen=True, slots=True)
class ClientCard:
    """Who a client is, for a screen that holds only their ID, such as a chat or a complaint."""

    account: ClientAccount
    # The name on the approved identity document. None until the check is approved.
    verified_name: str | None
    # Issued when the identity check is approved.
    account_number: str | None


async def client_cards(session: AsyncSession, client_ids: list[str]) -> list[ClientCard]:
    """A card for each of these clients, in the order asked. An ID with no client is left out.

    Two queries whatever the number of IDs: the accounts, then their identity checks.
    """
    wanted = list(dict.fromkeys(client_ids))
    accounts = await client_accounts(session, wanted)
    checks = await identities(session, list(accounts))
    cards: list[ClientCard] = []
    for client_id in wanted:
        account = accounts.get(client_id)
        if account is None:
            continue
        check = checks.get(client_id)
        cards.append(
            ClientCard(
                account=account,
                verified_name=check.verified_name if check else None,
                account_number=check.account_number if check else None,
            )
        )
    return cards


async def find_client(session: AsyncSession, query: str) -> ClientAccount | None:
    """A client by ID, by ten-digit account number, or by mobile number."""
    cleaned = query.strip()
    if _PUSH_ID.fullmatch(cleaned):
        found = await client_account(session, cleaned)
        if found is not None:
            return found
    digits = re.sub(r"[\s-]", "", cleaned)
    if _ACCOUNT_NUMBER.fullmatch(digits) and not digits.startswith("0"):
        client_id = await client_for_account_number(session, digits)
        if client_id is not None:
            return await client_account(session, client_id)
    return await client_by_phone(session, cleaned)


async def client_page(
    session: AsyncSession,
    client_id: str,
    today: date,
    *,
    with_support: bool,
    with_complaints: bool,
) -> ClientPage | None:
    account = await client_account(session, client_id)
    if account is None:
        return None
    return ClientPage(
        account=account,
        verified_name=await kyc_verified_name(session, client_id),
        identity_check=await check_summary(session, client_id),
        owed=[
            item for item in await client_owed_as_of(session, client_id, today) if item.owed.minor
        ],
        destinations=await destinations_for_client(session, client_id),
        locks=await lock_history(session, client_id),
        freezes=await client_freezes(session, client_id),
        conversation=await conversation_for_client(session, client_id) if with_support else None,
        complaints=await complaints_for_client(session, client_id, today)
        if with_complaints
        else None,
    )
