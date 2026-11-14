"""Routes for the bank account clients pay into by transfer, on the console's Settings screen.

One member of staff proposes the details and a different one approves them. Until a proposal is
approved, clients keep being shown the account in use, or none.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.infrastructure.database import Database
from app.modules.identity.api import require_confirmed, require_permission
from app.modules.identity.service import StaffSession
from app.modules.payments import bank_accounts
from app.modules.payments.domain.bank_accounts import MAX_BRANCH, MAX_NAME, BankAccount
from app.shared.clock import Clock
from app.shared.contracts import Contract, PushId
from app.shared.errors import ErrorEnvelope
from app.shared.money import Currency

_REFUSALS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
}

staff_router = APIRouter(prefix="/staff", tags=["staff bank accounts"], responses=_REFUSALS)
routers = [staff_router]

# The account sits on the console's Settings screen. Opening it shows the account. Changing
# where every client sends money is a permission of its own, so read-only access to Settings
# can never be one of the two people a change needs.
SettingsStaff = Annotated[StaffSession, Depends(require_permission("settings.view"))]
AccountStaff = Annotated[StaffSession, Depends(require_permission("bank_accounts.change"))]
# Deciding needs a passkey or app code from the last few minutes.
AccountDecider = Annotated[StaffSession, Depends(require_confirmed("bank_accounts.change"))]
Reason = Annotated[str, Field(min_length=1, max_length=500)]
CurrencyName = Literal["UGX", "USD"]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


Db = Annotated[Database, Depends(_database)]
Time = Annotated[Clock, Depends(_clock)]


class BankAccountText(Contract):
    bank_name: Annotated[str, Field(min_length=1, max_length=MAX_NAME)]
    account_name: Annotated[str, Field(min_length=1, max_length=MAX_NAME)]
    # Digits. Spaces and dashes are taken out before it is checked.
    account_number: Annotated[str, Field(min_length=1, max_length=40)]
    branch: Annotated[str, Field(max_length=MAX_BRANCH)] | None = None


class BankAccountProposalView(Contract):
    id: PushId
    currency: CurrencyName
    account: BankAccountText
    reason: str
    proposed_by: PushId
    proposed_at: datetime
    # The proposer can withdraw it. Anyone else who may change the account can decide it.
    yours: bool


class CurrencyBankAccountView(Contract):
    currency: CurrencyName
    # None while no account is approved: clients cannot pay this currency by transfer.
    in_use: BankAccountText | None
    changed_at: datetime | None
    proposal: BankAccountProposalView | None


class BankAccountList(Contract):
    accounts: list[CurrencyBankAccountView]


class ProposeBankAccount(Contract):
    account: BankAccountText
    reason: Reason


class DecideBankAccount(Contract):
    decision: Literal["approved", "rejected", "withdrawn"]
    reason: Reason


def _text(account: BankAccount) -> BankAccountText:
    return BankAccountText(
        bank_name=account.bank_name,
        account_name=account.account_name,
        account_number=account.account_number,
        branch=account.branch,
    )


def _proposal(proposal: bank_accounts.Proposal, staff: StaffSession) -> BankAccountProposalView:
    return BankAccountProposalView(
        id=proposal.id,
        currency=proposal.currency.value,
        account=_text(proposal.account),
        reason=proposal.reason,
        proposed_by=proposal.proposed_by,
        proposed_at=proposal.proposed_at,
        yours=proposal.proposed_by == staff.staff_id,
    )


@staff_router.get("/payments/bank-accounts")
async def list_bank_accounts(staff: SettingsStaff, database: Db) -> BankAccountList:
    """The account clients pay each currency into, and any change waiting for a decision."""
    async with database.actor_transaction(staff.actor) as session:
        listed = await bank_accounts.list_accounts(session)
    return BankAccountList(
        accounts=[
            CurrencyBankAccountView(
                currency=item.currency.value,
                in_use=_text(item.in_use) if item.in_use else None,
                changed_at=item.changed_at,
                proposal=_proposal(item.proposal, staff) if item.proposal else None,
            )
            for item in listed
        ]
    )


@staff_router.post("/payments/bank-accounts/{currency}/proposals", status_code=201)
async def propose_bank_account(
    currency: CurrencyName,
    body: ProposeBankAccount,
    staff: AccountStaff,
    database: Db,
    clock: Time,
) -> BankAccountProposalView:
    """Put new account details forward. A different member of staff must approve them.

    409 if a change for this currency is already waiting.
    """
    async with database.actor_transaction(staff.actor) as session:
        proposal = await bank_accounts.propose(
            session,
            staff.actor,
            Currency(currency),
            bank_name=body.account.bank_name,
            account_name=body.account.account_name,
            account_number=body.account.account_number,
            branch=body.account.branch,
            reason=body.reason,
            clock=clock,
        )
    return _proposal(proposal, staff)


@staff_router.post("/payments/bank-accounts/proposals/{proposal_id}/decision")
async def decide_bank_account(
    proposal_id: PushId, body: DecideBankAccount, staff: AccountDecider, database: Db, clock: Time
) -> BankAccountProposalView:
    """Approve or reject another person's proposal, or withdraw your own.

    Approved details are what clients are shown from the next time they open the transfer
    screen.
    """
    async with database.actor_transaction(staff.actor) as session:
        proposal = await bank_accounts.decide(
            session, staff.actor, proposal_id, body.decision, body.reason, clock
        )
    return _proposal(proposal, staff)
