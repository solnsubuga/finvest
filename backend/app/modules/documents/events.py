"""What other modules name when they hand work to documents. Imports nothing of documents'
own, so a module that documents reads from (payments) can import it without a cycle."""

from app.infrastructure.jobs import Consumer

RECEIPT_KIND = "documents.receipt_from_deposit"
# The payments module names this consumer when it publishes `payments.deposit_confirmed`.
RECEIPT_ON_DEPOSIT = Consumer(kind=RECEIPT_KIND)

INVESTMENT_NOTE_KIND = "documents.note_from_investment"
# The investments module names this consumer when it publishes `investments.started`.
NOTE_ON_START = Consumer(kind=INVESTMENT_NOTE_KIND)

ENDED_NOTE_KIND = "documents.note_from_ended_investment"
# The investments module names this consumer when it publishes `investments.matured`,
# `investments.rolled_over` or `investments.withdrawn_early`. An investment ends once.
NOTE_ON_END = Consumer(kind=ENDED_NOTE_KIND)
