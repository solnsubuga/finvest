"""Limit what a staff transaction can post to the ledger.

Revision ID: 0016_ledger_staff_source_types
Revises: 0040_reconciliation_statements

Staff commands move money only in a few known ways, each with its own checks: a correction
approved by a second person, a payout review, a dealing result confirmed by a second person.
Without a limit, a faulty or careless staff command could call `post_entry` with anything.

PostgreSQL enforces this, not only the application:

* `ledger_staff_source_types` lists the source types a staff transaction may post.
* A journal entry written in a staff transaction must carry one of them. An entry with no
  source type, or any other, is refused. That includes a plain reversal, `ledger.entry`: staff
  undo an entry through a correction.
* The list is changed only by a migration, so adding a way for staff to move money is a
  reviewed change. No application role can read or write it.

Worker and client transactions are not limited by this list.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_ledger_staff_source_types"
down_revision: str | None = "0040_reconciliation_statements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CODE = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"

TABLE = f"""
CREATE TABLE ledger_staff_source_types (
    source_type varchar(64) NOT NULL,
    purpose varchar(200) NOT NULL,
    CONSTRAINT pk_ledger_staff_source_types PRIMARY KEY (source_type),
    CONSTRAINT ck_ledger_staff_source_types_source_type CHECK (source_type ~ '{CODE}'),
    CONSTRAINT ck_ledger_staff_source_types_purpose CHECK (length(btrim(purpose)) > 0)
)
"""

# Each row names the module that owns it. Add a row in that module's own migration.
SEED = """
INSERT INTO ledger_staff_source_types (source_type, purpose) VALUES
    ('ledger.correction', 'ledger: a correction a second staff member approved'),
    ('payout', 'payouts: releasing a held payout after a staff review'),
    ('dealing.order', 'dealing: an order result a second staff member confirmed')
"""

GUARD = """
CREATE FUNCTION finvest_ledger_staff_source() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF public.finvest_actor_kind() = 'staff' AND NOT EXISTS (
            SELECT FROM public.ledger_staff_source_types
            WHERE source_type = NEW.source_type
        ) THEN
            RAISE EXCEPTION 'staff cannot post a ledger entry with source type %',
                coalesce(NEW.source_type, 'none')
                USING ERRCODE = '23514', CONSTRAINT = 'ledger_staff_source_type';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON ledger_staff_source_types FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_ledger_staff_source() FROM PUBLIC",
    "CREATE TRIGGER ledger_journal_entries_staff_source BEFORE INSERT ON ledger_journal_entries "
    "FOR EACH ROW EXECUTE FUNCTION finvest_ledger_staff_source()",
]


def upgrade() -> None:
    for statement in [TABLE, SEED, GUARD, *RULES]:
        op.execute(statement)


def downgrade() -> None:
    # The list is configuration, not history: dropping it removes the limit and loses nothing.
    op.execute("DROP TRIGGER ledger_journal_entries_staff_source ON ledger_journal_entries")
    op.execute("DROP FUNCTION finvest_ledger_staff_source()")
    op.execute("DROP TABLE ledger_staff_source_types")
