"""Let two members of staff retry or close a client the worker could not carry over.

Revision ID: 0076_migration_carry_resolutions
Revises: 0075_migration_carry_problems

A client noted in `migration_carry_problems` stays noted until staff resolve it. One member of
staff proposes what to do and why, and a different one approves or rejects it:

* `retry`: the worker tries the client again, once whatever refused them was put right by hand.
  A retry that is refused again is noted as the next attempt, so each attempt keeps its own note.
* `close`: the client is not carried over by the switch, and staff look after them by hand. The
  worker no longer waits for them, and the sealed sheets go once every client is done.

* `migration_carry_problems` gains `attempt`, so a client can be noted more than once.
* `migration_carry_resolutions` holds the proposal: the attempt it answers, the action, the
  reason and who proposed it.
* `migration_carry_resolution_decisions` holds the decision, by someone other than the proposer.

All three are insert-only.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0076_migration_carry_resolutions"
down_revision: str | None = "0075_migration_carry_problems"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
STAFF = "finvest_actor_kind() = 'staff'"
PROBLEMS = "migration_carry_problems"
TABLE_NAMES = ("migration_carry_resolutions", "migration_carry_resolution_decisions")
WRITERS = {
    "migration_carry_resolutions": "proposed_by",
    "migration_carry_resolution_decisions": "decided_by",
}

UPGRADE = [
    # Adding a column with a constant default rewrites no row, so no row trigger fires.
    f"ALTER TABLE {PROBLEMS} ADD COLUMN attempt smallint NOT NULL DEFAULT 1",
    f"ALTER TABLE {PROBLEMS} ALTER COLUMN attempt DROP DEFAULT",
    f"ALTER TABLE {PROBLEMS} ADD CONSTRAINT ck_{PROBLEMS}_attempt CHECK (attempt BETWEEN 1 AND 99)",
    f"ALTER TABLE {PROBLEMS} DROP CONSTRAINT pk_{PROBLEMS}",
    f"ALTER TABLE {PROBLEMS} ADD CONSTRAINT pk_{PROBLEMS} "
    "PRIMARY KEY (switch_id, legacy_client_ref, attempt)",
    """
CREATE TABLE migration_carry_resolutions (
    id push_id NOT NULL,
    switch_id push_id NOT NULL,
    legacy_client_ref varchar(64) NOT NULL,
    attempt smallint NOT NULL,
    action varchar(16) NOT NULL,
    reason varchar(500) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    CONSTRAINT pk_migration_carry_resolutions PRIMARY KEY (id),
    CONSTRAINT fk_migration_carry_resolutions_problem
        FOREIGN KEY (switch_id, legacy_client_ref, attempt)
        REFERENCES migration_carry_problems (switch_id, legacy_client_ref, attempt),
    CONSTRAINT ck_migration_carry_resolutions_action CHECK (action IN ('retry', 'close')),
    CONSTRAINT ck_migration_carry_resolutions_reason CHECK (length(btrim(reason)) > 0)
)
""",
    "CREATE INDEX ix_migration_carry_resolutions_problem "
    "ON migration_carry_resolutions (switch_id, legacy_client_ref, attempt)",
    """
CREATE TABLE migration_carry_resolution_decisions (
    resolution_id push_id NOT NULL,
    outcome varchar(16) NOT NULL,
    reason varchar(500),
    decided_by push_id NOT NULL,
    decided_at timestamptz NOT NULL,
    CONSTRAINT pk_migration_carry_resolution_decisions PRIMARY KEY (resolution_id),
    CONSTRAINT fk_migration_carry_resolution_decisions_resolution_id
        FOREIGN KEY (resolution_id) REFERENCES migration_carry_resolutions (id),
    CONSTRAINT ck_migration_carry_resolution_decisions_outcome
        CHECK (outcome IN ('approved', 'rejected')),
    CONSTRAINT ck_migration_carry_resolution_decisions_reason
        CHECK (length(btrim(reason)) > 0),
    CONSTRAINT ck_migration_carry_resolution_decisions_rejection_reason
        CHECK (outcome <> 'rejected' OR reason IS NOT NULL)
)
""",
    # Runs as the table owner so it reads the proposal whatever the caller may see.
    """
CREATE FUNCTION finvest_migration_resolution_second_person() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        proposer public.push_id;
    BEGIN
        SELECT proposed_by INTO proposer
        FROM public.migration_carry_resolutions WHERE id = NEW.resolution_id;
        IF proposer = NEW.decided_by THEN
            RAISE EXCEPTION 'a resolution is decided by a second person, not its proposer'
                USING ERRCODE = '23514', CONSTRAINT = 'migration_resolution_second_person';
        END IF;
        RETURN NEW;
    END;
    $$
""",
    "REVOKE ALL ON FUNCTION finvest_migration_resolution_second_person() FROM PUBLIC",
    "CREATE TRIGGER migration_carry_resolution_decisions_second_person "
    "BEFORE INSERT ON migration_carry_resolution_decisions "
    "FOR EACH ROW EXECUTE FUNCTION finvest_migration_resolution_second_person()",
    *(
        f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION finvest_migration_immutable()"
        for table in TABLE_NAMES
    ),
    *(
        f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION finvest_migration_immutable()"
        for table in TABLE_NAMES
    ),
    *(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" for table in TABLE_NAMES),
    *(f"REVOKE ALL ON {table} FROM PUBLIC" for table in TABLE_NAMES),
    *(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}" for table in TABLE_NAMES),
    *(f"GRANT SELECT ON {table} TO {WORKER_ROLE}" for table in TABLE_NAMES),
    *(
        f"CREATE POLICY {table}_worker_read ON {table} FOR SELECT TO {WORKER_ROLE} USING (true)"
        for table in TABLE_NAMES
    ),
    *(
        f"CREATE POLICY {table}_app_read ON {table} FOR SELECT TO {APP_ROLE} USING ({STAFF})"
        for table in TABLE_NAMES
    ),
    *(
        f"CREATE POLICY {table}_app_insert ON {table} FOR INSERT TO {APP_ROLE} "
        f"WITH CHECK ({STAFF} AND {writer} = finvest_actor_id())"
        for table, writer in WRITERS.items()
    ),
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM migration_carry_resolutions)
        OR EXISTS (SELECT FROM migration_carry_problems WHERE attempt > 1) THEN
        RAISE EXCEPTION 'refusing to drop how the switch resolved the clients it could not carry';
    END IF;
END
$$
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    for table in reversed(TABLE_NAMES):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION finvest_migration_resolution_second_person()")
    op.execute(f"ALTER TABLE {PROBLEMS} DROP CONSTRAINT pk_{PROBLEMS}")
    op.execute(
        f"ALTER TABLE {PROBLEMS} ADD CONSTRAINT pk_{PROBLEMS} "
        "PRIMARY KEY (switch_id, legacy_client_ref)"
    )
    op.execute(f"ALTER TABLE {PROBLEMS} DROP CONSTRAINT ck_{PROBLEMS}_attempt")
    op.execute(f"ALTER TABLE {PROBLEMS} DROP COLUMN attempt")
