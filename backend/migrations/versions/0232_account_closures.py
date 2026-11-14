"""Close a client's account at their request, with two members of staff.

Revision ID: 0232_account_closures
Revises: 0231_kyc_risk_answers

* `closure_requests` holds every request a client makes to close their account. The client asks
  and may cancel. One member of staff prepares it, a different one closes it, and either may
  decline it with a note. PostgreSQL enforces who may make each move, that the closer is never
  the preparer, and that a request that ended never changes.
* The guard on `identity_clients` (migration 0152) now also lets an active client become
  `closed`, but only through a closure approved in the same transaction. Nothing moves a client
  out of `closed`.
* The API still holds no UPDATE on a client's status. It applies a closure through
  `identity_close_client`, a SECURITY DEFINER function that checks the approval was made in the
  same transaction.

See docs/account-closure.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0232_account_closures"
down_revision: str | None = "0231_kyc_risk_answers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
NOTE = "btrim({column}) <> '' AND length({column}) <= 500"

TABLES = f"""
CREATE TABLE closure_requests (
    id push_id NOT NULL,
    -- The client's ID in the identity module. No foreign key: a module owns only its own tables.
    client_id push_id NOT NULL,
    reason text,
    requested_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'requested',
    prepared_by push_id,
    prepared_at timestamptz,
    prepared_note text,
    decided_by push_id,
    decided_at timestamptz,
    decision_note text,
    -- The transaction that closed it. The guard on identity_clients accepts it only there.
    decided_in xid8,
    cancelled_at timestamptz,
    CONSTRAINT pk_closure_requests PRIMARY KEY (id),
    CONSTRAINT ck_closure_requests_status
        CHECK (status IN ('requested', 'prepared', 'closed', 'declined', 'cancelled')),
    CONSTRAINT ck_closure_requests_reason
        CHECK (reason IS NULL OR ({NOTE.format(column="reason")})),
    CONSTRAINT ck_closure_requests_prepared CHECK (
        (prepared_by IS NULL) = (prepared_at IS NULL)
        AND (prepared_by IS NULL) = (prepared_note IS NULL)
        AND (prepared_note IS NULL OR ({NOTE.format(column="prepared_note")}))
        AND (status NOT IN ('prepared', 'closed') OR prepared_by IS NOT NULL)),
    CONSTRAINT ck_closure_requests_decided CHECK (
        (status IN ('closed', 'declined')) = (decided_by IS NOT NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (decided_by IS NULL) = (decision_note IS NULL)
        AND (decision_note IS NULL OR ({NOTE.format(column="decision_note")}))
        AND (status = 'closed') = (decided_in IS NOT NULL)),
    CONSTRAINT ck_closure_requests_cancelled CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL)),
    CONSTRAINT ck_closure_requests_two_people CHECK (status <> 'closed' OR decided_by <> prepared_by)
);
-- One open request per client.
CREATE UNIQUE INDEX uq_closure_requests_open
    ON closure_requests (client_id) WHERE status IN ('requested', 'prepared');
CREATE INDEX ix_closure_requests_client ON closure_requests (client_id, requested_at DESC);
CREATE INDEX ix_closure_requests_open ON closure_requests (requested_at)
    WHERE status IN ('requested', 'prepared')
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001).
GUARD = """
CREATE FUNCTION finvest_closure_requests_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        v_kind text := public.finvest_actor_kind();
        v_actor text := public.finvest_actor_id();
        v_fixed text[] := ARRAY['id', 'client_id', 'reason', 'requested_at'];
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF v_kind IS DISTINCT FROM 'client' OR NEW.client_id IS DISTINCT FROM v_actor
               OR NEW.status <> 'requested' THEN
                RAISE EXCEPTION 'only the client asks to close their own account'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status NOT IN ('requested', 'prepared') THEN
            RAISE EXCEPTION 'a closure that ended never changes' USING ERRCODE = '42501';
        END IF;
        IF (to_jsonb(NEW) -> 'id', to_jsonb(NEW) -> 'client_id', to_jsonb(NEW) -> 'reason',
            to_jsonb(NEW) -> 'requested_at')
           IS DISTINCT FROM (to_jsonb(OLD) -> 'id', to_jsonb(OLD) -> 'client_id',
            to_jsonb(OLD) -> 'reason', to_jsonb(OLD) -> 'requested_at') THEN
            RAISE EXCEPTION 'what the client asked never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'cancelled' THEN
            IF v_kind IS DISTINCT FROM 'client' OR OLD.client_id IS DISTINCT FROM v_actor THEN
                RAISE EXCEPTION 'only the client cancels their request' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF v_kind IS DISTINCT FROM 'staff' OR NOT EXISTS (
            SELECT FROM public.identity_staff WHERE id = v_actor AND status = 'active') THEN
            RAISE EXCEPTION 'only active staff prepare, close or decline a closure'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'prepared' THEN
            IF OLD.status <> 'requested' OR NEW.prepared_by IS DISTINCT FROM v_actor
               OR NEW.decided_by IS NOT NULL THEN
                RAISE EXCEPTION 'a request is prepared once, by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF (NEW.prepared_by, NEW.prepared_at, NEW.prepared_note)
           IS DISTINCT FROM (OLD.prepared_by, OLD.prepared_at, OLD.prepared_note) THEN
            RAISE EXCEPTION 'the preparation never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'closed' THEN
            IF OLD.status <> 'prepared' OR NEW.decided_by IS DISTINCT FROM v_actor
               OR NEW.decided_by = OLD.prepared_by
               OR NEW.decided_in IS DISTINCT FROM pg_current_xact_id() THEN
                RAISE EXCEPTION 'a prepared closure is approved by a second member of staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.status = 'declined' AND NEW.decided_by IS NOT DISTINCT FROM v_actor
           AND NEW.decided_in IS NULL THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'that is not a move a closure makes' USING ERRCODE = '42501';
    END;
    $$
"""

# Migration 0152's guard, with one more move: active to closed, by a closure approved here.
CLIENTS_GUARD = """
CREATE OR REPLACE FUNCTION finvest_identity_clients_status_guard() RETURNS trigger
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
        IF OLD.status = 'active' AND NEW.status = 'closed' AND EXISTS (
            SELECT FROM public.closure_requests AS r
             WHERE r.client_id = NEW.id AND r.status = 'closed'
               AND r.decided_in = pg_current_xact_id()) THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'a client is locked, unlocked or closed only by an approved change'
            USING ERRCODE = '42501';
    END;
    $$
"""

OLD_CLIENTS_GUARD = """
CREATE OR REPLACE FUNCTION finvest_identity_clients_status_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
            RETURN NEW;
        END IF;
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

# The only way the API closes a client: apply a closure approved in this same transaction.
APPLY = """
CREATE FUNCTION identity_close_client(p_request_id text) RETURNS boolean
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    WITH request AS (
        SELECT client_id FROM public.closure_requests
        WHERE id = p_request_id AND status = 'closed' AND decided_in = pg_current_xact_id()
    ), applied AS (
        UPDATE public.identity_clients AS c SET status = 'closed'
        FROM request WHERE c.id = request.client_id AND c.status = 'active'
        RETURNING 1
    )
    SELECT EXISTS (SELECT FROM applied)
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_closure_requests_guard() FROM PUBLIC",
    "CREATE TRIGGER closure_requests_guard BEFORE INSERT OR UPDATE ON closure_requests "
    "FOR EACH ROW EXECUTE FUNCTION finvest_closure_requests_guard()",
    "REVOKE ALL ON closure_requests FROM PUBLIC",
    # No DELETE for anyone: why an account was closed, and by whom, is part of the record.
    f"GRANT SELECT, INSERT ON closure_requests TO {APP_ROLE}",
    "GRANT UPDATE (status, prepared_by, prepared_at, prepared_note, decided_by, decided_at, "
    f"decision_note, decided_in, cancelled_at) ON closure_requests TO {APP_ROLE}",
    "ALTER TABLE closure_requests ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY closure_requests_read ON closure_requests FOR SELECT TO {APP_ROLE} "
    "USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY closure_requests_insert ON closure_requests FOR INSERT TO {APP_ROLE} "
    "WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY closure_requests_update ON closure_requests FOR UPDATE TO {APP_ROLE} "
    "USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    "REVOKE ALL ON FUNCTION identity_close_client(text) FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION identity_close_client(text) TO {APP_ROLE}",
]


def upgrade() -> None:
    for statement in TABLES.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    op.execute(GUARD)
    op.execute(CLIENTS_GUARD)
    op.execute(APPLY)
    for statement in RULES:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM closure_requests) THEN
        RAISE EXCEPTION 'refusing to drop closure_requests while it holds requests';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP FUNCTION identity_close_client(text)")
    op.execute(OLD_CLIENTS_GUARD)
    op.execute("DROP TABLE closure_requests")
    op.execute("DROP FUNCTION finvest_closure_requests_guard()")
