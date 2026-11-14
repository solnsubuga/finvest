"""A client freezes their own account.

Revision ID: 0235_client_freezes
Revises: 0354_staff_withdrawals

* `identity_client_freezes` records each time a client froze their own account, for example
  after losing their phone. Nobody updates or deletes a row: it is part of the record.
* `identity_freeze_client` is the only way in. It is a SECURITY DEFINER function that takes the
  acting client from the actor settings, so a client freezes only themselves. It records the
  freeze and moves the client from `active` to `locked` in the same transaction.
* The guard on `identity_clients` from migrations 0152 and 0232 gains one move: `active` to
  `locked` by a freeze recorded in this same transaction. Unfreezing is the existing two-person
  unlock (migration 0152), so nothing here moves a client out of `locked`.
* The API reads freezes for the client themselves and for staff. The worker sees nothing.

See docs/client-profile.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0235_client_freezes"
down_revision: str | None = "0354_staff_withdrawals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
# Adds a table, a function and one more allowed move to a guard: the previous release keeps
# working.
zero_downtime = True

APP_ROLE = "finvest_app"

TABLE = """
CREATE TABLE identity_client_freezes (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    reason text,
    frozen_at timestamptz NOT NULL,
    -- The transaction that froze the account. The guard on identity_clients accepts it only there.
    frozen_in xid8 NOT NULL,
    CONSTRAINT pk_identity_client_freezes PRIMARY KEY (id),
    CONSTRAINT fk_identity_client_freezes_client_id_identity_clients
        FOREIGN KEY (client_id) REFERENCES identity_clients (id),
    CONSTRAINT ck_identity_client_freezes_reason
        CHECK (reason IS NULL OR (btrim(reason) <> '' AND length(reason) <= 500))
)
"""

INDEX = (
    "CREATE INDEX ix_identity_client_freezes_client "
    "ON identity_client_freezes (client_id, frozen_at DESC)"
)

# The actor settings are a fence against bugs, not a credential (see migration 0001).
FREEZE = """
CREATE FUNCTION identity_freeze_client(p_freeze_id text, p_reason text, p_at timestamptz)
    RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        v_client text := public.finvest_actor_id();
    BEGIN
        IF public.finvest_actor_kind() IS DISTINCT FROM 'client' THEN
            RAISE EXCEPTION 'only a client freezes their own account' USING ERRCODE = '42501';
        END IF;
        PERFORM FROM public.identity_clients WHERE id = v_client AND status = 'active'
            FOR UPDATE;
        IF NOT FOUND THEN
            RETURN false;
        END IF;
        INSERT INTO public.identity_client_freezes (id, client_id, reason, frozen_at, frozen_in)
        VALUES (p_freeze_id, v_client, p_reason, p_at, pg_current_xact_id());
        UPDATE public.identity_clients SET status = 'locked' WHERE id = v_client;
        RETURN true;
    END;
    $$
"""

# Migration 0232's guard, with one more move: active to locked, by a freeze made here.
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
        IF OLD.status = 'active' AND NEW.status = 'locked' AND EXISTS (
            SELECT FROM public.identity_client_freezes AS f
             WHERE f.client_id = NEW.id AND f.frozen_in = pg_current_xact_id()) THEN
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

# As migration 0232 left it.
OLD_CLIENTS_GUARD = """
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

RULES = [
    "REVOKE ALL ON identity_client_freezes FROM PUBLIC",
    # No INSERT, UPDATE or DELETE: a freeze is written only by identity_freeze_client.
    f"GRANT SELECT ON identity_client_freezes TO {APP_ROLE}",
    "ALTER TABLE identity_client_freezes ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY identity_client_freezes_read ON identity_client_freezes FOR SELECT "
    f"TO {APP_ROLE} USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    "REVOKE ALL ON FUNCTION identity_freeze_client(text, text, timestamptz) FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION identity_freeze_client(text, text, timestamptz) TO {APP_ROLE}",
]


def upgrade() -> None:
    op.execute(TABLE)
    op.execute(INDEX)
    op.execute(FREEZE)
    op.execute(CLIENTS_GUARD)
    for statement in RULES:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_client_freezes) THEN
        RAISE EXCEPTION 'refusing to drop the record of clients who froze their accounts';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(OLD_CLIENTS_GUARD)
    op.execute("DROP FUNCTION identity_freeze_client(text, text, timestamptz)")
    op.execute("DROP TABLE identity_client_freezes")
