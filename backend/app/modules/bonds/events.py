"""What other modules name when they hand work to bonds. Imports nothing of bonds' own, so a
module that bonds reads from (payments) can import it without a cycle."""

from app.infrastructure.jobs import Consumer

START_KIND = "bonds.start_from_deposit"
# The payments module names this consumer when it publishes `payments.deposit_confirmed`.
START_ON_DEPOSIT = Consumer(kind=START_KIND)
