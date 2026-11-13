"""Let the day close pair what another module already recorded.

Revision ID: 0043_reconciliation_recorded
Revises: 0035_payout_account_two_people

A match is now made one of three ways: by the same reference on both sides, by a person with a
reason, or `recorded`: from what another module recorded about the movement. Two such records
exist. A bank transfer's approved review names the statement line it arrived on, and the
dealing desk gives the reference the bank shows for a settlement. A recorded match carries the
reason it was made, and the guard from 0041 still insists on the same pot, amount and
direction.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0043_reconciliation_recorded"
down_revision: str | None = "0035_payout_account_two_people"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE reconciliation_matches DROP CONSTRAINT ck_reconciliation_matches_how")
    op.execute(
        "ALTER TABLE reconciliation_matches ADD CONSTRAINT ck_reconciliation_matches_how "
        "CHECK (how IN ('reference', 'staff', 'recorded'))"
    )


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM reconciliation_matches WHERE how = 'recorded') THEN
        RAISE EXCEPTION 'refusing to drop recorded matches';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("ALTER TABLE reconciliation_matches DROP CONSTRAINT ck_reconciliation_matches_how")
    op.execute(
        "ALTER TABLE reconciliation_matches ADD CONSTRAINT ck_reconciliation_matches_how "
        "CHECK (how IN ('reference', 'staff'))"
    )
