"""Lock and unlock a client's account with two people.

Revision ID: 0152_client_locks
Revises: 0153_email_suppression_lifts

* `identity_client_locks` holds every proposal to lock or unlock a client's account. One member
  of staff proposes with a reason and a different one approves, and PostgreSQL enforces that.
* `identity_clients` gains a guard. Its status moves between `active` and `locked` only through
  a lock or unlock approved in the same transaction. `unclaimed` to `active` is left to the
  claim function (migration 0009), and nothing else moves, so never to or from `closed`, which
  account closure will own.
* The API still holds no UPDATE on a client's status (migration 0008). It applies an approved
  change through `identity_apply_client_lock`, a SECURITY DEFINER function that checks the
  approval was made in the same transaction.
"""

from alembic import op

revision = "0152_client_locks"
down_revision = "0153_email_suppression_lifts"
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
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' OR NOT EXISTS (
            SELECT FROM public.identity_staff
             WHERE id = public.finvest_actor_id() AND status = 'active') THEN
            RAISE EXCEPTION 'only active staff lock or unlock a client' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a lock starts as a proposal by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided lock never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
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
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
            RETURN NEW;
        END IF;
        -- A claim, made only by identity_claim_client (migration 0009). The API holds no
        -- UPDATE on status, so nothing else can make this move.
        IF OLD.status = 'unclaimed' AND NEW.status = 'active' THEN
            RETURN NEW;
        END IF;
        IF ((OLD.status = 'active' AND NEW.status = 'locked')
             OR (OLD.status = 'locked' AND NEW.status = 'active'))
           AND EXISTS (
            SELECT FROM public.identity_client_locks AS l
             WHERE l.client_id = NEW.id AND l.status = 'approved'
               AND l.decided_in = pg_current_xact_id()
               AND l.kind = CASE WHEN NEW.status = 'locked' THEN 'lock' ELSE 'unlock' END) THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'a client is locked or unlocked only by an approved change'
            USING ERRCODE = '42501';
    END;
    $$
"""

# The only way the API changes a client's status: apply a lock or unlock approved in this
# same transaction. It runs as the migrator, so the API itself never writes `status`.
APPLY = """
CREATE FUNCTION identity_apply_client_lock(p_change_id text) RETURNS boolean
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    WITH change AS (
        SELECT client_id, kind FROM public.identity_client_locks
        WHERE id = p_change_id AND status = 'approved' AND decided_in = pg_current_xact_id()
    ), applied AS (
        UPDATE public.identity_clients AS c
        SET status = CASE WHEN change.kind = 'lock' THEN 'locked' ELSE 'active' END
        FROM change
        WHERE c.id = change.client_id
          AND c.status = CASE WHEN change.kind = 'lock' THEN 'active' ELSE 'locked' END
        RETURNING 1
    )
    SELECT EXISTS (SELECT FROM applied)
    $$
"""

# Named to run after 0009's tr_identity_clients_never_unclaimed_again (triggers fire in name
# order), so a move back to unclaimed is refused with that trigger's own reason.
RULES = [
    "REVOKE ALL ON FUNCTION finvest_identity_client_locks_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_identity_clients_status_guard() FROM PUBLIC",
    "CREATE TRIGGER identity_client_locks_guard BEFORE INSERT OR UPDATE ON identity_client_locks "
    "FOR EACH ROW EXECUTE FUNCTION finvest_identity_client_locks_guard()",
    "CREATE TRIGGER tr_identity_clients_status_guard BEFORE UPDATE OF status ON identity_clients "
    "FOR EACH ROW EXECUTE FUNCTION finvest_identity_clients_status_guard()",
    "REVOKE ALL ON identity_client_locks FROM PUBLIC",
    # No DELETE for anyone: why an account was shut, and by whom, is part of the record.
    "GRANT SELECT, INSERT ON identity_client_locks TO finvest_app",
    "GRANT UPDATE (status, decided_by, decided_at, decision_reason, decided_in) "
    "ON identity_client_locks TO finvest_app",
    "REVOKE ALL ON FUNCTION identity_apply_client_lock(text) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION identity_apply_client_lock(text) TO finvest_app",
]


def upgrade() -> None:
    for statement in TABLES.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    op.execute(LOCKS_GUARD)
    op.execute(CLIENTS_GUARD)
    op.execute(APPLY)
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
    op.execute("DROP FUNCTION identity_apply_client_lock(text)")
    op.execute("DROP TRIGGER tr_identity_clients_status_guard ON identity_clients")
    op.execute("DROP TABLE identity_client_locks")
    op.execute("DROP FUNCTION finvest_identity_clients_status_guard()")
    op.execute("DROP FUNCTION finvest_identity_client_locks_guard()")
