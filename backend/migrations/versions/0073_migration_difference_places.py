"""Keep where each differing investment is in the sheet, and its currency.

Revision ID: 0073_migration_difference_places
Revises: 0053_kyc_claim_checks

A trial keeps every investment that differs by its key and the figures a sign-off accepts.
Finance also needs the sheet row to find it in Finvest 1.0, and the currency to read its
figures. Both are kept with the trial, as written once; a trial run before this has none.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0073_migration_difference_places"
down_revision: str | None = "0053_kyc_claim_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM migration_trials WHERE difference_places <> '{}'::jsonb) THEN
        RAISE EXCEPTION 'refusing to drop the sheet rows of migration differences';
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute(
        "ALTER TABLE migration_trials ADD COLUMN difference_places jsonb NOT NULL "
        "DEFAULT '{}'::jsonb, ADD CONSTRAINT ck_migration_trials_difference_places "
        "CHECK (jsonb_typeof(difference_places) = 'object')"
    )


def downgrade() -> None:
    # A trial is a record of what was checked; dropping the column would change it.
    op.execute(EMPTY_GUARD)
    op.execute("ALTER TABLE migration_trials DROP COLUMN difference_places")
