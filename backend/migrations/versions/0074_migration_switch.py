"""The switch from Finvest 1.0: a proposal, a second person's decision, and the accounts it made.

Revision ID: 0074_migration_switch
Revises: 0073_migration_difference_places

One member of staff proposes the switch with the two sheets of the trial that cleared every
check. A different member of staff approves or rejects it. An approval creates the account of
every client in the same transaction, and records which Finvest 1.0 client became which account.

* `migration_switches` holds the proposal: the trial, the fingerprint of each sheet and who
  proposed it. Nothing from the sheets.
* `migration_switch_files` holds the two sheets, sealed, for the approval and for the worker that
  carries the investments over. The worker deletes them once it is done. It is the only table here
  that is not insert-only.
* `migration_switch_decisions` holds the decision, by someone other than the proposer. At most one
  switch is ever approved.
* `migration_carried_clients` holds each client's Finvest 1.0 reference and new account ID.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0074_migration_switch"
down_revision: str | None = "0073_migration_difference_places"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
STAFF = "finvest_actor_kind() = 'staff'"

TABLES = """
CREATE TABLE migration_switches (
    id push_id NOT NULL,
    trial_id push_id NOT NULL,
    as_of date NOT NULL,
    clients_sha256 char(64) NOT NULL,
    investments_sha256 char(64) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    CONSTRAINT pk_migration_switches PRIMARY KEY (id),
    CONSTRAINT fk_migration_switches_trial_id
        FOREIGN KEY (trial_id) REFERENCES migration_trials (id),
    CONSTRAINT ck_migration_switches_hashes
        CHECK (clients_sha256 ~ '^[0-9a-f]{64}$' AND investments_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX ix_migration_switches_proposed_at ON migration_switches (proposed_at, id);

CREATE TABLE migration_switch_files (
    switch_id push_id NOT NULL,
    clients_csv bytea NOT NULL,
    investments_csv bytea NOT NULL,
    CONSTRAINT pk_migration_switch_files PRIMARY KEY (switch_id),
    CONSTRAINT fk_migration_switch_files_switch_id
        FOREIGN KEY (switch_id) REFERENCES migration_switches (id)
);

CREATE TABLE migration_switch_decisions (
    switch_id push_id NOT NULL,
    outcome varchar(16) NOT NULL,
    reason varchar(500),
    decided_by push_id NOT NULL,
    decided_at timestamptz NOT NULL,
    CONSTRAINT pk_migration_switch_decisions PRIMARY KEY (switch_id),
    CONSTRAINT fk_migration_switch_decisions_switch_id
        FOREIGN KEY (switch_id) REFERENCES migration_switches (id),
    CONSTRAINT ck_migration_switch_decisions_outcome
        CHECK (outcome IN ('approved', 'rejected')),
    CONSTRAINT ck_migration_switch_decisions_reason CHECK (length(btrim(reason)) > 0),
    CONSTRAINT ck_migration_switch_decisions_rejection_reason
        CHECK (outcome <> 'rejected' OR reason IS NOT NULL)
);
CREATE UNIQUE INDEX uq_migration_switch_decisions_one_approved
    ON migration_switch_decisions ((true)) WHERE outcome = 'approved';

CREATE TABLE migration_carried_clients (
    legacy_client_ref varchar(64) NOT NULL,
    switch_id push_id NOT NULL,
    client_id push_id NOT NULL,
    carried_by push_id NOT NULL,
    carried_at timestamptz NOT NULL,
    CONSTRAINT pk_migration_carried_clients PRIMARY KEY (legacy_client_ref),
    CONSTRAINT uq_migration_carried_clients_client_id UNIQUE (client_id),
    CONSTRAINT fk_migration_carried_clients_switch_id
        FOREIGN KEY (switch_id) REFERENCES migration_switches (id)
);
CREATE INDEX ix_migration_carried_clients_switch_id ON migration_carried_clients (switch_id)
"""

FUNCTIONS = [
    # Runs as the table owner so it reads the proposal whatever the caller may see.
    """
CREATE FUNCTION finvest_migration_switch_second_person() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        proposer public.push_id;
    BEGIN
        SELECT proposed_by INTO proposer
        FROM public.migration_switches WHERE id = NEW.switch_id;
        IF proposer = NEW.decided_by THEN
            RAISE EXCEPTION 'the switch is decided by a second person, not its proposer'
                USING ERRCODE = '23514', CONSTRAINT = 'migration_switch_second_person';
        END IF;
        RETURN NEW;
    END;
    $$
""",
    # A client is carried over only by the approval of their switch, in its own transaction,
    # by the person who approved it.
    """
CREATE FUNCTION finvest_migration_carried_by_approval() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT FROM public.migration_switch_decisions
            WHERE switch_id = NEW.switch_id AND outcome = 'approved'
                AND decided_by = NEW.carried_by AND xmin = pg_current_xact_id()::xid
        ) THEN
            RAISE EXCEPTION 'a client is carried over only by the approval of the switch'
                USING ERRCODE = '23514', CONSTRAINT = 'migration_carried_by_approval';
        END IF;
        RETURN NEW;
    END;
    $$
""",
]

INSERT_ONLY = ("migration_switches", "migration_switch_decisions", "migration_carried_clients")
TABLE_NAMES = (
    "migration_switches",
    "migration_switch_files",
    "migration_switch_decisions",
    "migration_carried_clients",
)
# Which column names the staff member whose transaction wrote the row.
WRITERS = {
    "migration_switches": "proposed_by",
    "migration_switch_decisions": "decided_by",
    "migration_carried_clients": "carried_by",
}

RULES = [
    *(
        f"REVOKE ALL ON FUNCTION {name}() FROM PUBLIC"
        for name in (
            "finvest_migration_switch_second_person",
            "finvest_migration_carried_by_approval",
        )
    ),
    *(
        f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION finvest_migration_immutable()"
        for table in INSERT_ONLY
    ),
    *(
        f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION finvest_migration_immutable()"
        for table in TABLE_NAMES
    ),
    # The sealed sheets are written once and only ever removed.
    "CREATE TRIGGER migration_switch_files_no_update BEFORE UPDATE ON migration_switch_files "
    "FOR EACH ROW EXECUTE FUNCTION finvest_migration_immutable()",
    "CREATE TRIGGER migration_switch_decisions_second_person "
    "BEFORE INSERT ON migration_switch_decisions "
    "FOR EACH ROW EXECUTE FUNCTION finvest_migration_switch_second_person()",
    "CREATE TRIGGER migration_carried_clients_by_approval "
    "BEFORE INSERT ON migration_carried_clients "
    "FOR EACH ROW EXECUTE FUNCTION finvest_migration_carried_by_approval()",
    *(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" for table in TABLE_NAMES),
    *(f"REVOKE ALL ON {table} FROM PUBLIC" for table in TABLE_NAMES),
    *(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}" for table in TABLE_NAMES),
    *(f"GRANT SELECT ON {table} TO {WORKER_ROLE}" for table in TABLE_NAMES),
    # The worker removes the sealed sheets once every client is carried over.
    f"GRANT DELETE ON migration_switch_files TO {WORKER_ROLE}",
    *(
        f"CREATE POLICY {table}_worker_read ON {table} FOR SELECT TO {WORKER_ROLE} USING (true)"
        for table in TABLE_NAMES
    ),
    "CREATE POLICY migration_switch_files_worker_delete ON migration_switch_files "
    f"FOR DELETE TO {WORKER_ROLE} USING (true)",
    *(
        f"CREATE POLICY {table}_app_read ON {table} FOR SELECT TO {APP_ROLE} USING ({STAFF})"
        for table in TABLE_NAMES
    ),
    *(
        f"CREATE POLICY {table}_app_insert ON {table} FOR INSERT TO {APP_ROLE} "
        f"WITH CHECK ({STAFF} AND {writer} = finvest_actor_id())"
        for table, writer in WRITERS.items()
    ),
    # The sheets go in with the proposal, by its proposer.
    "CREATE POLICY migration_switch_files_app_insert ON migration_switch_files "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF} AND EXISTS ("
    "SELECT FROM migration_switches AS s WHERE s.id = switch_id "
    "AND s.proposed_by = finvest_actor_id()))",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM migration_switches) THEN
        RAISE EXCEPTION 'refusing to drop the record of the switch';
    END IF;
END
$$
"""


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), *FUNCTIONS, *RULES]:
        op.execute(statement)


def downgrade() -> None:
    # Dropping the tables would erase who approved the switch and which account is whose.
    op.execute(EMPTY_GUARD)
    for table in reversed(TABLE_NAMES):
        op.execute(f"DROP TABLE {table}")
    for function in (
        "finvest_migration_carried_by_approval",
        "finvest_migration_switch_second_person",
    ):
        op.execute(f"DROP FUNCTION {function}()")
