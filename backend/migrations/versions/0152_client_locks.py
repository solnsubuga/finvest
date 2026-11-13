"""Lock and unlock a client's account with two people.

Revision ID: 0152_client_locks
Revises: 0111_goal_rollovers

* `identity_client_locks` holds every proposal to lock or unlock a client's account. One member
  of staff proposes with a reason and a different one approves, and PostgreSQL enforces that.
* `identity_clients` gains a guard. Its status moves between `active` and `locked` only through
  a lock or unlock approved in the same transaction, and never to or from `closed`, which
  account closure will own. Migration 0008 gave the API no UPDATE on a client's status; this
  grants it, behind the guard.
"""

from alembic import op

revision = "0152_client_locks"
down_revision = "0111_goal_rollovers"
branch_labels = None
depends_on = None

TABLES = """
CREATE TABLE identity_client_locks (
    id push_id NOT NULL,
    kind text NOT NULL,
    client_id push_id NOT NULL,
    reason text NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'proposed',
    decided_by push_id,
    decided_at timestamptz,
    decision_reason text,
    -- The transaction that approved it. The guard on identity_clients accepts it only there.
    decided_in xid8,
    CONSTRAINT pk_identity_client_locks PRIMARY KEY (id),
    CONSTRAINT fk_identity_client_locks_client_id_identity_clients
        FOREIGN KEY (client_id) REFERENCES identity_clients (id),
    CONSTRAINT fk_identity_client_locks_proposed_by_identity_staff
        FOREIGN KEY (proposed_by) REFERENCES identity_staff (id),
    CONSTRAINT fk_identity_client_locks_decided_by_identity_staff
        FOREIGN KEY (decided_by) REFERENCES identity_staff (id),
    CONSTRAINT ck_identity_client_locks_kind CHECK (kind IN ('lock', 'unlock')),
    CONSTRAINT ck_identity_client_locks_reason
        CHECK (btrim(reason) <> '' AND length(reason) <= 500),
    CONSTRAINT ck_identity_client_locks_status
        CHECK (status IN ('proposed', 'approved', 'rejected', 'withdrawn')),
    CONSTRAINT ck_identity_client_locks_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (decided_by IS NULL) = (decision_reason IS NULL)
        AND (status = 'approved') = (decided_in IS NOT NULL)),
    CONSTRAINT ck_identity_client_locks_decision_reason
        CHECK (decision_reason IS NULL OR (btrim(decision_reason) <> ''
        AND length(decision_reason) <= 500)),
    CONSTRAINT ck_identity_client_locks_two_people CHECK (
        status = 'proposed'
        OR (status = 'withdrawn' AND decided_by = proposed_by)
        OR (status IN ('approved', 'rejected') AND decided_by <> proposed_by))
);
-- One open proposal per client.
CREATE UNIQUE INDEX uq_identity_client_locks_open
    ON identity_client_locks (client_id) WHERE status = 'proposed';
CREATE INDEX ix_identity_client_locks_client
    ON identity_client_locks (client_id, proposed_at DESC)
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001).
LOCKS_GUARD = """
CREATE FUNCTION finvest_identity_client_locks_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF finvest_actor_kind() IS DISTINCT FROM 'staff' OR NOT EXISTS (
            SELECT FROM identity_staff WHERE id = finvest_actor_id() AND status = 'active') THEN
            RAISE EXCEPTION 'only active staff lock or unlock a client' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> finvest_actor_id() THEN
                RAISE EXCEPTION 'a lock starts as a proposal by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided lock never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM finvest_actor_id()
            OR (NEW.status = 'approved') <> (NEW.decided_in = pg_current_xact_id())
            OR (to_jsonb(NEW) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason',
                                      'decided_in'])
               <> (to_jsonb(OLD) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason',
                                         'decided_in']) THEN
            RAISE EXCEPTION 'a lock is decided once, by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

CLIENTS_GUARD = """
CREATE FUNCTION finvest_identity_clients_status_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
            RETURN NEW;
        END IF;
        IF NOT EXISTS (
            SELECT FROM identity_client_locks
             WHERE client_id = NEW.id AND status = 'approved'
               AND decided_in = pg_current_xact_id()
               AND ((kind = 'lock' AND OLD.status = 'active' AND NEW.status = 'locked')
                 OR (kind = 'unlock' AND OLD.status = 'locked' AND NEW.status = 'active'))) THEN
            RAISE EXCEPTION 'a client is locked or unlocked only by an approved change'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_identity_client_locks_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_identity_clients_status_guard() FROM PUBLIC",
    "CREATE TRIGGER identity_client_locks_guard BEFORE INSERT OR UPDATE ON identity_client_locks "
    "FOR EACH ROW EXECUTE FUNCTION finvest_identity_client_locks_guard()",
    "CREATE TRIGGER identity_clients_status_guard BEFORE UPDATE OF status ON identity_clients "
    "FOR EACH ROW EXECUTE FUNCTION finvest_identity_clients_status_guard()",
    "REVOKE ALL ON identity_client_locks FROM PUBLIC",
    # No DELETE for anyone: why an account was shut, and by whom, is part of the record.
    "GRANT SELECT, INSERT ON identity_client_locks TO finvest_app",
    "GRANT UPDATE (status, decided_by, decided_at, decision_reason, decided_in) "
    "ON identity_client_locks TO finvest_app",
    "GRANT UPDATE (status) ON identity_clients TO finvest_app",
]


def upgrade() -> None:
    for statement in TABLES.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    op.execute(LOCKS_GUARD)
    op.execute(CLIENTS_GUARD)
    for statement in RULES:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_client_locks) THEN
        RAISE EXCEPTION 'refusing to drop identity_client_locks while it holds proposals';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("REVOKE UPDATE (status) ON identity_clients FROM finvest_app")
    op.execute("DROP TRIGGER identity_clients_status_guard ON identity_clients")
    op.execute("DROP TABLE identity_client_locks")
    op.execute("DROP FUNCTION finvest_identity_clients_status_guard()")
    op.execute("DROP FUNCTION finvest_identity_client_locks_guard()")
