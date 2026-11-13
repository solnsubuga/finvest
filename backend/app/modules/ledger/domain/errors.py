"""What can go wrong when posting to the ledger. Pure."""


class LedgerError(Exception):
    """Base for ledger failures. `code` is safe to log and to return in an error envelope."""

    code = "ledger_error"


class InsufficientFunds(LedgerError):
    """The entry would take an account's balance below zero."""

    code = "ledger_insufficient_funds"


class IdempotencyKeyReused(LedgerError):
    """The idempotency key was already used for a different entry."""

    code = "ledger_idempotency_key_reused"


class UnknownAccount(LedgerError):
    """A line names an account that does not exist in that currency."""

    code = "ledger_unknown_account"


class UnknownEntry(LedgerError):
    """The entry does not exist, or is not visible to this transaction."""

    code = "ledger_unknown_entry"


class AlreadyReversed(LedgerError):
    """The entry was already reversed, or is itself a reversal."""

    code = "ledger_already_reversed"


class AccountMismatch(LedgerError):
    """An account with this identity already exists with a different kind."""

    code = "ledger_account_mismatch"


class UnknownCorrection(LedgerError):
    """The correction proposal does not exist, or is not visible to this transaction."""

    code = "ledger_unknown_correction"


class CorrectionAlreadyDecided(LedgerError):
    """The proposal was already approved or rejected."""

    code = "ledger_correction_already_decided"


class SecondPersonRequired(LedgerError):
    """A correction must be decided by a staff member other than the one who proposed it."""

    code = "ledger_second_person_required"


class DayClosed(LedgerError):
    """The entry is dated on or before the day the ledger is closed through."""

    code = "ledger_day_closed"


class DayNotEnded(LedgerError):
    """Only a day that has ended in Kampala can be closed."""

    code = "ledger_day_not_ended"


class StaffSourceNotAllowed(LedgerError):
    """A staff transaction may post only the source types listed in the database."""

    code = "ledger_staff_source_not_allowed"


class MixedAccountKinds(LedgerError):
    """Accounts that share a code are of more than one kind, so their balances cannot be added."""

    code = "ledger_mixed_account_kinds"
