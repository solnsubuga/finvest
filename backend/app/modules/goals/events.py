"""What other modules name when they hand work to goals. Imports nothing of goals' own, so a
module that goals reads from (investments) can import it without a cycle."""

from app.infrastructure.jobs import Consumer

FOLLOW_ROLLOVER_KIND = "goals.follow_rollover"
# The investments module names this consumer when it publishes `investments.rolled_over`.
FOLLOW_ROLLOVER = Consumer(kind=FOLLOW_ROLLOVER_KIND)

LINK_PLAN_RUN_KIND = "goals.link_plan_run"
# The autoinvest module names this consumer when it publishes `autoinvest.run_requested`. Goals
# links the run's investment to the goal whose monthly saving the plan is, and ignores any other
# plan. Its handler arrives with goal saving plans.
LINK_PLAN_RUN = Consumer(kind=LINK_PLAN_RUN_KIND)
