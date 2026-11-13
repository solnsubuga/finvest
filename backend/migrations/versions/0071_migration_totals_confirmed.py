"""Hold an entry of the old system's totals to two people.

Revision ID: 0071_migration_totals_confirmed
Revises: 0063_investment_maturity

The totals finance enters decide whether a total line differs, and so whether it needs a
sign-off. One person entering the new system's own figures as "what the old system says" would
make a difference disappear with nobody else looking. So an entry counts only once a second
member of staff has confirmed it:

* A confirmation names the staff member whose transaction wrote it.
* The person who confirms is never the person who entered the totals.
* An entry is confirmed once, and the table is insert-only.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0071_migration_totals_confirmed"
down_revision: str | None = "0063_investment_maturity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
TABLE = "migration_control_totals_confirmations"
STAFF = "finvest_actor_kind() = 'staff'"

STATEMENTS = [
    f"""
CREATE TABLE {TABLE} (
    totals_id push_id NOT NULL,
    confirmed_by push_id NOT NULL,
    confirmed_at timestamptz NOT NULL,
    CONSTRAINT pk_{TABLE} PRIMARY KEY (totals_id),
    CONSTRAINT fk_{TABLE}_totals_id
        FOREIGN KEY (totals_id) REFERENCES migration_control_totals (id)
)
""",
    # Runs as the table owner so it reads the entry whatever the caller may see.
    """
CREATE FUNCTION finvest_migration_totals_second_person() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        enterer public.push_id;
    BEGIN
        SELECT entered_by INTO enterer
        FROM public.migration_control_totals WHERE id = NEW.totals_id;
        IF enterer = NEW.confirmed_by THEN
            RAISE EXCEPTION 'totals are confirmed by a second person, not the one who entered them'
                USING ERRCODE = '23514', CONSTRAINT = 'migration_totals_second_person';
        END IF;
        RETURN NEW;
    END;
    $$
""",
    "REVOKE ALL ON FUNCTION finvest_migration_totals_second_person() FROM PUBLIC",
    f"CREATE TRIGGER {TABLE}_immutable BEFORE UPDATE OR DELETE ON {TABLE} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_migration_immutable()",
    f"CREATE TRIGGER {TABLE}_no_truncate BEFORE TRUNCATE ON {TABLE} "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_migration_immutable()",
    f"CREATE TRIGGER {TABLE}_second_person BEFORE INSERT ON {TABLE} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_migration_totals_second_person()",
    f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY",
    f"REVOKE ALL ON {TABLE} FROM PUBLIC",
    f"GRANT SELECT, INSERT ON {TABLE} TO {APP_ROLE}",
    f"GRANT SELECT ON {TABLE} TO {WORKER_ROLE}",
    f"CREATE POLICY {TABLE}_worker_read ON {TABLE} FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY {TABLE}_app_read ON {TABLE} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY {TABLE}_app_insert ON {TABLE} FOR INSERT TO {APP_ROLE} "
    f"WITH CHECK ({STAFF} AND confirmed_by = finvest_actor_id())",
]

EMPTY_GUARD = f"""
DO $$
BEGIN
    IF EXISTS (SELECT FROM {TABLE}) THEN
        RAISE EXCEPTION 'refusing to drop confirmations of migration totals';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # Dropping the table would erase who checked the opening totals.
    op.execute(EMPTY_GUARD)
    op.execute(f"DROP TABLE {TABLE}")
    op.execute("DROP FUNCTION finvest_migration_totals_second_person()")
