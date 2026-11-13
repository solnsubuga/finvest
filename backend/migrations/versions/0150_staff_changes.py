"""Change the team with two people: invite, change a role, deactivate, reactivate, reset sign-in.

Revision ID: 0150_staff_changes
Revises: 0111_goal_rollovers

* `identity_staff_changes` holds every proposed change to who works here and what they may
  do. One admin proposes and a different admin approves, and PostgreSQL enforces that: the
  approver is never the proposer, and nobody proposes or decides a change to their own account.
* `identity_staff` gains a guard. A new member of staff added by a member of staff, a new role,
  and a move into or out of `disabled` each need a change approved in the same transaction.
  Without the guard the API role could not change a role at all (migration 0008). With it, it
  can, but only by approving a change.

A member of staff added with no acting member of staff is the first administrator, added from
the command line (`app.modules.identity.bootstrap`). That path stays open, as it was.
"""

from alembic import op

revision = "0150_staff_changes"
down_revision = "0111_goal_rollovers"
branch_labels = None
depends_on = None

ROLES = "'admin', 'operations', 'finance', 'compliance', 'support'"

TABLES = f"""
CREATE TABLE identity_staff_changes (
    id push_id NOT NULL,
    kind text NOT NULL,
    -- For an invite, the ID the new account will take when the change is approved.
    staff_id push_id NOT NULL,
    email text,
    name text,
    role text,
    reason text NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'proposed',
    decided_by push_id,
    decided_at timestamptz,
    decision_reason text,
    -- The transaction that approved it. The guard on identity_staff accepts a change only
    -- from this transaction, so an approval cannot be replayed later.
    decided_in xid8,
    CONSTRAINT pk_identity_staff_changes PRIMARY KEY (id),
    CONSTRAINT fk_identity_staff_changes_proposed_by_identity_staff
        FOREIGN KEY (proposed_by) REFERENCES identity_staff (id),
    CONSTRAINT fk_identity_staff_changes_decided_by_identity_staff
        FOREIGN KEY (decided_by) REFERENCES identity_staff (id),
    CONSTRAINT ck_identity_staff_changes_kind
        CHECK (kind IN ('invite', 'change_role', 'deactivate', 'reactivate', 'reset_sign_in')),
    CONSTRAINT ck_identity_staff_changes_invite_details CHECK (
        (kind = 'invite') = (email IS NOT NULL) AND (kind = 'invite') = (name IS NOT NULL)),
    CONSTRAINT ck_identity_staff_changes_role CHECK (
        (kind IN ('invite', 'change_role')) = (role IS NOT NULL)
        AND (role IS NULL OR role IN ({ROLES}))),
    CONSTRAINT ck_identity_staff_changes_email CHECK (
        email IS NULL OR (email = lower(email) AND email ~ '^[^@\\s]+@[^@\\s]+$'
        AND length(email) <= 254)),
    CONSTRAINT ck_identity_staff_changes_name
        CHECK (name IS NULL OR (btrim(name) <> '' AND length(name) <= 120)),
    CONSTRAINT ck_identity_staff_changes_reason
        CHECK (btrim(reason) <> '' AND length(reason) <= 500),
    CONSTRAINT ck_identity_staff_changes_status
        CHECK (status IN ('proposed', 'approved', 'rejected', 'withdrawn')),
    CONSTRAINT ck_identity_staff_changes_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (decided_by IS NULL) = (decision_reason IS NULL)
        AND (status = 'approved') = (decided_in IS NOT NULL)),
    CONSTRAINT ck_identity_staff_changes_decision_reason
        CHECK (decision_reason IS NULL OR (btrim(decision_reason) <> ''
        AND length(decision_reason) <= 500)),
    -- One person proposes and a different person approves or rejects. Only the proposer
    -- withdraws.
    CONSTRAINT ck_identity_staff_changes_two_people CHECK (
        status = 'proposed'
        OR (status = 'withdrawn' AND decided_by = proposed_by)
        OR (status IN ('approved', 'rejected') AND decided_by <> proposed_by)),
    -- Nobody proposes or decides a change to their own account.
    CONSTRAINT ck_identity_staff_changes_not_own CHECK (
        staff_id <> proposed_by AND (decided_by IS NULL OR decided_by <> staff_id))
);
-- One open change per person, and one open invite per address.
CREATE UNIQUE INDEX uq_identity_staff_changes_open
    ON identity_staff_changes (staff_id) WHERE status = 'proposed';
CREATE UNIQUE INDEX uq_identity_staff_changes_open_invite
    ON identity_staff_changes (email) WHERE status = 'proposed' AND kind = 'invite';
CREATE INDEX ix_identity_staff_changes_staff
    ON identity_staff_changes (staff_id, proposed_at DESC)
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001).
CHANGES_GUARD = """
CREATE FUNCTION finvest_identity_staff_changes_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff change the team' USING ERRCODE = '42501';
        END IF;
        IF NOT EXISTS (SELECT FROM public.identity_staff
                       WHERE id = public.finvest_actor_id() AND status = 'active'
                         AND role = 'admin') THEN
            RAISE EXCEPTION 'only an active admin changes the team'
                USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a team change starts as a proposal by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided team change never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
            OR (NEW.status = 'approved') <> (NEW.decided_in = pg_current_xact_id())
            OR (to_jsonb(NEW) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason',
                                      'decided_in'])
               <> (to_jsonb(OLD) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason',
                                         'decided_in']) THEN
            RAISE EXCEPTION 'a team change is decided once, by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# What only an approved change may do to an account. `approved` finds the change this
# transaction approved for the account, so an old approval is never enough.
STAFF_GUARD = """
CREATE FUNCTION finvest_identity_staff_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        approved public.identity_staff_changes%ROWTYPE;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NEW.created_by IS NULL AND public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
                -- The first administrator, added from the command line.
                RETURN NEW;
            END IF;
            SELECT * INTO approved FROM public.identity_staff_changes
             WHERE staff_id = NEW.id AND kind = 'invite' AND status = 'approved'
               AND decided_in = pg_current_xact_id();
            IF NOT FOUND OR NEW.status <> 'invited' OR NEW.email <> approved.email
                OR NEW.name <> approved.name OR NEW.role <> approved.role
                OR NEW.created_by IS DISTINCT FROM approved.proposed_by THEN
                RAISE EXCEPTION 'staff are added only by an approved invite'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.role IS DISTINCT FROM OLD.role AND NOT EXISTS (
            SELECT FROM public.identity_staff_changes
             WHERE staff_id = NEW.id AND kind = 'change_role' AND role = NEW.role
               AND status = 'approved' AND decided_in = pg_current_xact_id()) THEN
            RAISE EXCEPTION 'a role changes only by an approved change' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'disabled' AND OLD.status <> 'disabled' AND NOT EXISTS (
            SELECT FROM public.identity_staff_changes
             WHERE staff_id = NEW.id AND kind = 'deactivate'
               AND status = 'approved' AND decided_in = pg_current_xact_id()) THEN
            RAISE EXCEPTION 'staff are deactivated only by an approved change'
                USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'disabled' AND NEW.status <> 'disabled' AND NOT EXISTS (
            SELECT FROM public.identity_staff_changes
             WHERE staff_id = NEW.id AND kind = 'reactivate'
               AND status = 'approved' AND decided_in = pg_current_xact_id()) THEN
            RAISE EXCEPTION 'staff are reactivated only by an approved change'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_identity_staff_changes_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_identity_staff_guard() FROM PUBLIC",
    "CREATE TRIGGER identity_staff_changes_guard BEFORE INSERT OR UPDATE "
    "ON identity_staff_changes FOR EACH ROW EXECUTE FUNCTION finvest_identity_staff_changes_guard()",
    "CREATE TRIGGER identity_staff_guard BEFORE INSERT OR UPDATE ON identity_staff "
    "FOR EACH ROW EXECUTE FUNCTION finvest_identity_staff_guard()",
    "REVOKE ALL ON identity_staff_changes FROM PUBLIC",
    # No DELETE for anyone: who was let in, and by whom, is part of the record.
    "GRANT SELECT, INSERT ON identity_staff_changes TO finvest_app",
    "GRANT UPDATE (status, decided_by, decided_at, decision_reason, decided_in) "
    "ON identity_staff_changes TO finvest_app",
    # The guard above decides when. Migration 0008 left role with no UPDATE at all.
    "GRANT UPDATE (role) ON identity_staff TO finvest_app",
]


def upgrade() -> None:
    for statement in TABLES.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    op.execute(CHANGES_GUARD)
    op.execute(STAFF_GUARD)
    for statement in RULES:
        op.execute(statement)


# A decided change is the record of who was let in or shut out. Refuse to drop it.
EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM identity_staff_changes) THEN
        RAISE EXCEPTION 'refusing to drop identity_staff_changes while it holds changes';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("REVOKE UPDATE (role) ON identity_staff FROM finvest_app")
    op.execute("DROP TRIGGER identity_staff_guard ON identity_staff")
    op.execute("DROP TABLE identity_staff_changes")
    op.execute("DROP FUNCTION finvest_identity_staff_guard()")
    op.execute("DROP FUNCTION finvest_identity_staff_changes_guard()")
