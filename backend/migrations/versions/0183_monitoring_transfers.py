"""Transaction monitoring reads client-to-client transfers too.

Revision ID: 0183_monitoring_transfers
Revises: 0332_support_staff_opens_chat

* `monitoring_transactions.kind` gains `transfer_sent` and `transfer_received`: each finished
  investment transfer is recorded once for the sender, as money going out, and once for the
  recipient, as money in. The column widens to hold the longer names.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0183_monitoring_transfers"
down_revision: str | None = "0332_support_staff_opens_chat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE monitoring_transactions DROP CONSTRAINT ck_monitoring_transactions_kind"
    )
    op.execute("ALTER TABLE monitoring_transactions ALTER COLUMN kind TYPE varchar(20)")
    op.execute(
        "ALTER TABLE monitoring_transactions ADD CONSTRAINT ck_monitoring_transactions_kind "
        "CHECK (kind IN ('deposit', 'payout', 'transfer_sent', 'transfer_received'))"
    )


TRANSFERS_GUARD = """
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM monitoring_transactions WHERE kind IN ('transfer_sent', 'transfer_received')
    ) THEN
        RAISE EXCEPTION 'refusing to drop transfers from the monitoring record';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(TRANSFERS_GUARD)
    op.execute(
        "ALTER TABLE monitoring_transactions DROP CONSTRAINT ck_monitoring_transactions_kind"
    )
    op.execute("ALTER TABLE monitoring_transactions ALTER COLUMN kind TYPE varchar(10)")
    op.execute(
        "ALTER TABLE monitoring_transactions ADD CONSTRAINT ck_monitoring_transactions_kind "
        "CHECK (kind IN ('deposit', 'payout'))"
    )
