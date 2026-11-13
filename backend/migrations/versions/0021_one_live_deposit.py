"""One live deposit per investment.

Revision ID: 0021_one_live_deposit
Revises: 0032_payout_destination_guards

An investment is paid for once. Without this, a client whose first answer was lost could start
a second deposit with a new idempotency key, get a second prompt and pay twice. The service
checks first, and this index is what holds when two requests race.

"Live" is every status but `failed`: a deposit that is being sent, waiting for approval,
uncertain or confirmed. A failed deposit moved no money, so it frees the investment for another
try. Goals are not covered: a goal takes many deposits over its life.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021_one_live_deposit"
down_revision: str | None = "0032_payout_destination_guards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_payment_deposits_live_investment "
        "ON payment_deposits (purpose_id) "
        "WHERE purpose_type = 'investment' AND status <> 'failed'"
    )


def downgrade() -> None:
    # Drops a rule and no data.
    op.execute("DROP INDEX uq_payment_deposits_live_investment")
