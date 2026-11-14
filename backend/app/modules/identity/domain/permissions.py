"""Staff roles and what each may do. The API checks a permission on every staff route.

The names match the console's screens. The console hides what a role cannot open, but that is
display only: this catalogue is the authority.
"""

from typing import Literal, get_args

Role = Literal["admin", "operations", "finance", "compliance", "support"]
ROLES: tuple[Role, ...] = get_args(Role)

Permission = Literal[
    "overview.view",
    "review_queue.view",
    "identity_checks.view",
    # Deciding a check, as opposed to opening the screen.
    "identity_checks.review",
    "maturities.view",
    "support.view",
    # Writing to a client in the help chat, solving or filing a chat, and keeping saved replies.
    "support.reply",
    "complaints.view",
    # Logging a complaint for a client, assigning it, moving it and adding notes.
    "complaints.manage",
    # Recording the outcome and closing a complaint for good.
    "complaints.close",
    "clients.view",
    # Proposing or deciding to lock or unlock a client's account. Seeing clients is
    # `clients.view`. Not a screen.
    "clients.lock",
    # Preparing a client's request to close their account, approving someone else's
    # preparation, which closes it, or declining a request. Seeing clients is `clients.view`.
    # Closing always takes two people. Not a screen.
    "clients.close",
    "money_movement.view",
    "reconciliation.view",
    # Importing a statement, pairing a line with a movement by hand, preparing, approving or
    # sending back a day close, and proposing, approving, declining or withdrawing the
    # settlement of a payout the provider never answered for. Seeing reconciliation is
    # `reconciliation.view`. Every signature still takes a second person. Not a screen.
    "reconciliation.change",
    "investments.view",
    "automatic_investing.view",
    "goals.view",
    "dealing_desk.view",
    # Preparing, approving, sending back or withdrawing a dealing order, and recording,
    # confirming or rejecting its result, which settles it into the ledger. Also changing the
    # liquidity buffer's rules and moving Finvest's own money into or out of it. Seeing the desk
    # is `dealing_desk.view`. Every step still takes a second person.
    "dealing_orders.change",
    "finance.view",
    # Recording a payment of withholding tax to URA, approving someone else's, or declining it.
    # Approving posts it to the ledger. Seeing the tax screen is `finance.view`. Not a screen.
    "tax_remittances.change",
    "products.view",
    "rates.view",
    # Proposing a rate change, approving or sending back someone else's, and withdrawing your
    # own. Seeing rates is `rates.view`. A rate still takes effect only with a second person.
    # Not a screen: the console's own list gains it with the rates screen.
    "rates.change",
    "compliance.view",
    # Proposing how to close a compliance case (a possible sanctions or politically exposed
    # person match, or a transaction monitoring alert), approving or sending back someone
    # else's proposal, withdrawing your own, and asking for a client to be screened again.
    # Seeing cases is `compliance.view`. Closing a case always takes a second person.
    "compliance_cases.change",
    "team.view",
    # Proposing or deciding a change to the team: an invite, a new role, deactivating or
    # reactivating someone, or a new set-up link. Seeing the team is `team.view`. Admin only.
    "team.change",
    "reports.view",
    # Reading and exporting the audit trail: who did what, when and why, across the product.
    # A section of the Reports and audit screen, not a screen of its own.
    "audit.view",
    "settings.view",
    # Proposing or deciding a change to the words of a client message. Seeing them is
    # `settings.view`. Not a screen: the console's own list gains it with the wordings screen.
    "wordings.change",
    # Proposing or deciding the bank account clients pay into by transfer, on the Settings
    # screen. Seeing it is `settings.view`. Approving changes where every client's transfer
    # goes, so it always takes a second person. Not a screen.
    "bank_accounts.change",
    # Proposing or deciding to email again an address that bounced for good or complained.
    # Seeing the suppression list is `clients.view`. Not a screen.
    "email_suppressions.lift",
    # Proposing or deciding an announcement to every active client. A bigger act than changing
    # a wording, so it is a permission of its own and can be given to fewer people. Seeing
    # announcements is `settings.view`.
    "announcements.send",
    # Proposing or deciding the account code map of an accounting system: which of its accounts
    # each Finvest ledger code is posted to in the daily journal. Seeing the maps is
    # `settings.view`. A map takes effect only once a second person approves it.
    "account_codes.change",
    "migration.view",
    # Matching a client's bank transfer to the statement, refusing it, or approving someone
    # else's match: this confirms money. Seeing the transfers is `reconciliation.view`. Not a
    # screen: the console's own list gains it with the bank transfers screen.
    "bank_transfers.change",
    # Proposing to move a bank transfer credited to the wrong client to the right one,
    # approving or sending back someone else's proposal, or withdrawing your own. Approving
    # moves the money, so it always takes a second person. Seeing them is `reconciliation.view`.
    # Not a screen: a section of the bank transfers screen.
    "deposit_reassignments.change",
    # Checking a client's proof that a payout account is theirs, approving someone else's
    # check, refusing the proof, or removing an account. Seeing the queue is
    # `review_queue.view`. Not a screen.
    "payout_accounts.change",
    # Preparing a batch of bank payouts to pay from the trust account, approving or declining
    # someone else's batch, withdrawing your own, and proposing or deciding how a bank payout
    # ended from the trust statement. Approving moves money, so it always takes a second
    # person. Seeing them is `money_movement.view`. Not a screen.
    "bank_payouts.change",
    "migration.import",
    "migration.sign_off",
    # Proposing a new mobile number for a client, or approving someone else's proposal. The
    # number is how a client proves who they are with a code, so a change takes two people.
    "client_phones.change",
    # Proposing to undo an investment started by a mistaken deposit, or to refund money that
    # never started one, and approving or sending back someone else's. Seeing investments is
    # `investments.view`. Both move money, so each takes a second person. Not a screen.
    "investment_corrections.change",
    # Proposing to let through a transfer of an investment to another client that is held
    # because it is large, approving someone else's proposal, withdrawing your own, or
    # declining one. Letting one through always takes a second person. Seeing them is
    # `review_queue.view`. Not a screen.
    "investment_transfers.change",
    # Checking an application for a company or group account, approving someone else's check,
    # sending one back or rejecting it. Approving always takes a second person. Not a screen:
    # applications are in the review queue.
    "organisations.review",
    # Writing a staff note on a client's page. Notes are kept as written and only staff read
    # them. Seeing them is `clients.view`. One person: a note changes nothing. Not a screen.
    "client_notes.write",
    # Proposing or deciding to reset a client's app PIN, which signs the phone app out so the
    # client logs in again and chooses a new one. Always takes a second person. Not a screen.
    "client_pins.reset",
]
PERMISSIONS: tuple[Permission, ...] = get_args(Permission)

# The working default recorded in docs/decisions.md, the same table as the console's
# `apps/console/src/auth/stub.ts`. The owner can change it at any time: change it here, in one
# place, and in the console's table.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    "admin": frozenset(PERMISSIONS),
    "operations": frozenset(
        {
            "overview.view",
            "review_queue.view",
            "identity_checks.view",
            "identity_checks.review",
            "maturities.view",
            "support.view",
            "support.reply",
            "complaints.view",
            "complaints.manage",
            "complaints.close",
            "clients.view",
            "email_suppressions.lift",
            "clients.lock",
            "clients.close",
            "money_movement.view",
            "reconciliation.view",
            "investments.view",
            "automatic_investing.view",
            "goals.view",
            "reports.view",
            "migration.view",
            "bank_transfers.change",
            "deposit_reassignments.change",
            "payout_accounts.change",
            "investment_transfers.change",
            "organisations.review",
            "migration.import",
            "client_phones.change",
            "client_notes.write",
            "client_pins.reset",
        }
    ),
    "finance": frozenset(
        {
            "overview.view",
            "review_queue.view",
            "maturities.view",
            "clients.view",
            "money_movement.view",
            "reconciliation.view",
            "reconciliation.change",
            "investments.view",
            "automatic_investing.view",
            "goals.view",
            "dealing_desk.view",
            "dealing_orders.change",
            "finance.view",
            "tax_remittances.change",
            "products.view",
            "rates.view",
            "rates.change",
            "reports.view",
            "bank_transfers.change",
            "deposit_reassignments.change",
            "bank_payouts.change",
            "migration.view",
            "migration.sign_off",
            "investment_corrections.change",
            "client_notes.write",
            # The trust account clients pay into is finance's to keep right. Settings is where
            # it is set, so finance can open Settings, read only apart from that account.
            "settings.view",
            "bank_accounts.change",
        }
    ),
    "compliance": frozenset(
        {
            "overview.view",
            "review_queue.view",
            "identity_checks.view",
            "identity_checks.review",
            "complaints.view",
            "complaints.manage",
            "complaints.close",
            "clients.view",
            "clients.lock",
            "clients.close",
            "compliance.view",
            "compliance_cases.change",
            "reports.view",
            "audit.view",
            "payout_accounts.change",
            "investment_transfers.change",
            "organisations.review",
            "client_notes.write",
            "client_pins.reset",
        }
    ),
    # Support answers clients and logs and works complaints. Closing one is for operations and
    # compliance, so the front line does not decide its own complaints.
    "support": frozenset(
        {
            "overview.view",
            "support.view",
            "support.reply",
            "complaints.view",
            "complaints.manage",
            "clients.view",
            "email_suppressions.lift",
            "client_notes.write",
            "client_pins.reset",
        }
    ),
}


def permissions_for(role: Role) -> list[Permission]:
    """The role's permissions in catalogue order, so a session always lists them the same way."""
    granted = ROLE_PERMISSIONS[role]
    return [permission for permission in PERMISSIONS if permission in granted]


def allows(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
