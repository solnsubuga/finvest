"""What other modules name when they hand work to payouts. Imports nothing of payouts' own, so
a module that payouts reads from (screening) can import it without a cycle."""

from app.infrastructure.jobs import Consumer

RELEASE_SCREENED_KIND = "payouts.release_screened"
# The screening module names this consumer when it publishes `screening.standing_changed`.
RELEASE_SCREENED = Consumer(kind=RELEASE_SCREENED_KIND)
