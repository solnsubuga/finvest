"""What other modules name when they hand work to the dealing desk. Imports nothing of dealing's
own, so a module that dealing reads from (investments) can import it without a cycle."""

from app.infrastructure.jobs import Consumer

FUND_WITHDRAWAL_KIND = "dealing.fund_early_withdrawal"
# Investing names this consumer when it publishes `investments.withdrawn_early`. The payload
# carries `investment_id`, `currency` and `amount_minor`, the amount the client is paid.
FUND_EARLY_WITHDRAWAL = Consumer(kind=FUND_WITHDRAWAL_KIND)

# Funds the withdrawals waiting for a sale from what it raised. The desk queues it in the same
# transaction that settles a sale. The payload carries `sale`, the order's ID.
FUND_FROM_SALE_KIND = "dealing.fund_from_sale"
