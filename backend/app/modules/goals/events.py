"""What other modules name when they hand work to goals. Imports nothing of goals' own, so a
module that goals reads from (investments) can import it without a cycle."""

from app.infrastructure.jobs import Consumer

FOLLOW_ROLLOVER_KIND = "goals.follow_rollover"
# The investments module names this consumer when it publishes `investments.rolled_over`.
FOLLOW_ROLLOVER = Consumer(kind=FOLLOW_ROLLOVER_KIND)
