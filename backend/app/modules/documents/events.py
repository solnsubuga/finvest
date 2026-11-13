"""What other modules name when they hand work to documents. Imports nothing of documents'
own, so a module that documents reads from (payments) can import it without a cycle."""

from app.infrastructure.jobs import Consumer

RECEIPT_KIND = "documents.receipt_from_deposit"
# The payments module names this consumer when it publishes `payments.deposit_confirmed`.
RECEIPT_ON_DEPOSIT = Consumer(kind=RECEIPT_KIND)
