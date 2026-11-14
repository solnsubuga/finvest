"""Act for a client with two people: one member of staff proposes, a different one approves.

Revision ID: 0351_console_client_actions
Revises: 0350_console_client_notes

`console_client_actions` holds every proposal staff make on the client page to act for a client.
The first kind is `pin_reset`: approving it ends every phone app session the client holds, so the
PIN on that phone goes with it and the client logs in again and chooses a new one (a PIN belongs
to one session, never to the account: see migration 0221). Later kinds add their own effect.

PostgreSQL enforces the two people: the approver is never the proposer, a decided proposal never
changes, and an approval records the transaction it was made in (`decided_in`), so a module that
applies the action can check it was approved in that same transaction.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0351_console_client_actions"
down_revision: str | None = "0350_console_client_notes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"

TABLE = """
CREATE TABLE console_client_actions (
    id push_id NOT NULL,
    client_id push_id NOT NULL,
    kind text NOT NULL,
    -- What the action does, in the shape its kind names. Never personal data.
    details jsonb NOT NULL DEFAULT '{}',
    reason varchar(500) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'proposed',
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    -- The transaction that approved it. A module applying the action accepts it only there.
    decided_in xid8,
    CONSTRAINT pk_console_client_actions PRIMARY KEY (id),
    CONSTRAINT fk_console_client_actions_client_id_identity_clients
        FOREIGN KEY (client_id) REFERENCES identity_clients (id),
    CONSTRAINT fk_console_client_actions_proposed_by_identity_staff
        FOREIGN KEY (proposed_by) REFERENCES identity_staff (id),
    CONSTRAINT fk_console_client_actions_decided_by_identity_staff
        FOREIGN KEY (decided_by) REFERENCES identity_staff (id),
    CONSTRAINT ck_console_client_actions_kind CHECK (kind IN ('pin_reset')),
    CONSTRAINT ck_console_client_actions_details CHECK (jsonb_typeof(details) = 'object'),
    CONSTRAINT ck_console_client_actions_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_console_client_actions_status
        CHECK (status IN ('proposed', 'approved', 'rejected', 'withdrawn')),
    CONSTRAINT ck_console_client_actions_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (decided_by IS NULL) = (decision_reason IS NULL)
        AND (status = 'approved') = (decided_in IS NOT NULL)),
    CONSTRAINT ck_console_client_actions_decision_reason
        CHECK (decision_reason IS NULL OR btrim(decision_reason) <> ''),
    CONSTRAINT ck_console_client_actions_two_people CHECK (
        status = 'proposed'
        OR (status = 'withdrawn' AND decided_by = proposed_by)
        OR (status IN ('approved', 'rejected') AND decided_by <> proposed_by))
)
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001).
GUARD = """
CREATE FUNCTION finvest_console_client_actions_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' OR NOT EXISTS (
            SELECT FROM public.identity_staff
             WHERE id = public.finvest_actor_id() AND status = 'active') THEN
            RAISE EXCEPTION 'only active staff act for a client' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> public.finvest_actor_id() THEN
                RAISE EXCEPTION 'an action starts as a proposal by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided action never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
            OR (NEW.status = 'approved') <> (NEW.decided_in = pg_current_xact_id())
            OR (to_jsonb(NEW) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason',
                                      'decided_in'])
               <> (to_jsonb(OLD) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason',
                                         'decided_in']) THEN
            RAISE EXCEPTION 'an action is decided once, by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

_STAFF = "finvest_actor_kind() = 'staff'"

RULES = [
    # One open proposal of each kind per client.
    "CREATE UNIQUE INDEX uq_console_client_actions_open "
    "ON console_client_actions (client_id, kind) WHERE status = 'proposed'",
    "CREATE INDEX ix_console_client_actions_client "
    "ON console_client_actions (client_id, proposed_at DESC, id DESC)",
    "CREATE INDEX ix_console_client_actions_waiting "
    "ON console_client_actions (proposed_at, id) WHERE status = 'proposed'",
    "REVOKE ALL ON FUNCTION finvest_console_client_actions_guard() FROM PUBLIC",
    "CREATE TRIGGER console_client_actions_guard BEFORE INSERT OR UPDATE ON console_client_actions "
    "FOR EACH ROW EXECUTE FUNCTION finvest_console_client_actions_guard()",
    "REVOKE ALL ON console_client_actions FROM PUBLIC",
    # No DELETE for anyone. Only the decision columns ever change.
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_reason, decided_in) "
    f"ON console_client_actions TO {APP_ROLE}",
    "ALTER TABLE console_client_actions ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY console_client_actions_staff ON console_client_actions TO {APP_ROLE} "
    f"USING ({_STAFF}) WITH CHECK ({_STAFF})",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM console_client_actions) THEN
        RAISE EXCEPTION 'refusing to drop staff actions that hold records';
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute(TABLE)
    op.execute(GUARD)
    for statement in RULES:
        op.execute(statement)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE console_client_actions")
    op.execute("DROP FUNCTION finvest_console_client_actions_guard()")
