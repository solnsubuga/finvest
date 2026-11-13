"""Add the append-only audit trail.

Revision ID: 0012_audit_events
Revises: 0011_account_numbers

Who did what, to what, when and why. The service roles can only INSERT (staff transactions can
also read), and a trigger refuses UPDATE, DELETE and TRUNCATE for every role, the table owner
included. Row policies stop a transaction recording an event in someone else's name.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_audit_events"
down_revision: str | None = "0011_account_numbers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

# Actor and resource IDs deliberately have no foreign keys: the trail must outlive, and must
# never block a change to, the person or record it describes.
TABLES = """
CREATE TABLE audit_events (
    id push_id NOT NULL,
    occurred_at timestamptz NOT NULL,
    category varchar(24) NOT NULL,
    action varchar(100) NOT NULL,
    outcome varchar(16) NOT NULL,
    actor_kind varchar(16) NOT NULL,
    actor_id push_id,
    resource_type varchar(64) NOT NULL,
    resource_id varchar(200),
    reason varchar(500),
    correlation_id varchar(64),
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT pk_audit_events PRIMARY KEY (id),
    CONSTRAINT ck_audit_events_category
        CHECK (category IN ('change', 'access', 'approval', 'operator_control', 'security')),
    CONSTRAINT ck_audit_events_action CHECK (action ~ '^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+$'),
    CONSTRAINT ck_audit_events_outcome CHECK (outcome IN ('succeeded', 'denied', 'failed')),
    CONSTRAINT ck_audit_events_actor CHECK (
        (actor_kind IN ('client', 'staff') AND actor_id IS NOT NULL)
        OR (actor_kind IN ('system', 'provider', 'anonymous') AND actor_id IS NULL)
    ),
    CONSTRAINT ck_audit_events_resource_type CHECK (resource_type ~ '^[a-z][a-z0-9_]{0,63}$'),
    CONSTRAINT ck_audit_events_resource_id CHECK (resource_id ~ '^[A-Za-z0-9._:-]{1,200}$'),
    CONSTRAINT ck_audit_events_reason CHECK (length(btrim(reason)) > 0),
    CONSTRAINT ck_audit_events_reason_required
        CHECK (category NOT IN ('approval', 'operator_control') OR reason IS NOT NULL),
    CONSTRAINT ck_audit_events_correlation_id CHECK (correlation_id ~ '^[A-Za-z0-9._:-]{1,64}$'),
    CONSTRAINT ck_audit_events_details
        CHECK (jsonb_typeof(details) = 'object' AND pg_column_size(details) <= 8192)
);
CREATE INDEX ix_audit_events_occurred_at ON audit_events (occurred_at DESC, id DESC);
CREATE INDEX ix_audit_events_resource
    ON audit_events (resource_type, resource_id, occurred_at DESC, id DESC);
CREATE INDEX ix_audit_events_actor_id
    ON audit_events (actor_id, occurred_at DESC, id DESC) WHERE actor_id IS NOT NULL;
CREATE INDEX ix_audit_events_correlation_id
    ON audit_events (correlation_id) WHERE correlation_id IS NOT NULL
"""

IMMUTABLE_FUNCTION = """
CREATE FUNCTION finvest_audit_events_immutable() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'audit events are append-only' USING ERRCODE = '42501';
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_audit_events_immutable() FROM PUBLIC",
    "CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events "
    "FOR EACH ROW EXECUTE FUNCTION finvest_audit_events_immutable()",
    "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_audit_events_immutable()",
    "ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON audit_events FROM PUBLIC",
    f"GRANT SELECT, INSERT ON audit_events TO {APP_ROLE}",
    f"GRANT INSERT ON audit_events TO {WORKER_ROLE}",
    # Only staff transactions read the trail. Which staff may is a permission checked in the
    # application.
    f"CREATE POLICY audit_events_app_read ON audit_events FOR SELECT TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'staff')",
    # An API transaction records only its own actor: a signed-in client or staff member as
    # themselves, and a request with nobody signed in as anonymous. Nothing running as the API
    # can write an event in another person's name, or in Finvest's own.
    f"CREATE POLICY audit_events_app_insert ON audit_events FOR INSERT TO {APP_ROLE} WITH CHECK ("
    "(finvest_actor_kind() IN ('client', 'staff') AND actor_kind = finvest_actor_kind() "
    "AND actor_id = finvest_actor_id()) "
    "OR (finvest_actor_kind() IS NULL AND actor_kind = 'anonymous'))",
    # Workers act for Finvest or relay what a provider reported, never as a person.
    f"CREATE POLICY audit_events_worker_insert ON audit_events FOR INSERT TO {WORKER_ROLE} "
    "WITH CHECK (actor_kind IN ('system', 'provider'))",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), IMMUTABLE_FUNCTION, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM audit_events) THEN
        RAISE EXCEPTION 'refusing to drop an audit trail that holds events';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Dropping the table would erase the trail.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE audit_events")
    op.execute("DROP FUNCTION finvest_audit_events_immutable()")
