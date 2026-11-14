"""What other modules name when they hand work to screening. Imports nothing of screening's
own, so a module that screening reads from (payments) can import it without a cycle."""

from app.infrastructure.jobs import Consumer

MONITOR_DEPOSIT_KIND = "screening.monitor_deposit"
# The payments module names this consumer when it publishes `payments.deposit_confirmed`.
MONITOR_DEPOSIT = Consumer(kind=MONITOR_DEPOSIT_KIND)
