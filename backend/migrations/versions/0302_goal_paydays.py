"""A goal's payday: the day of the month its client is nudged to save.

Revision ID: 0302_goal_paydays
Revises: 0341_withdrawal_limit_changes

A client may name the day they are paid. On that morning they are nudged to save towards the
goal, instead of three days before the day the goal was set up. It is a preference only: no
money is collected on it, so it needs no guard beyond its range. The goal's own guard (0110)
already refuses any change to a closed goal.
"""

from alembic import op

revision = "0302_goal_paydays"
down_revision = "0341_withdrawal_limit_changes"
branch_labels = None
depends_on = None

APP_ROLE = "finvest_app"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE goals ADD COLUMN payday smallint, "
        "ADD CONSTRAINT ck_goals_payday CHECK (payday BETWEEN 1 AND 31)"
    )
    op.execute(f"GRANT UPDATE (payday) ON goals TO {APP_ROLE}")


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM goals WHERE payday IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop the paydays clients named';
    END IF;
END
$$
"""


def downgrade() -> None:
    # A payday is a client's own choice; a downgrade never erases it.
    op.execute(EMPTY_GUARD)
    op.execute(f"REVOKE UPDATE (payday) ON goals FROM {APP_ROLE}")
    op.execute("ALTER TABLE goals DROP COLUMN payday")
