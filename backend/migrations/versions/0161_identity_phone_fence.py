"""Fence number changes to staff, and let only an active member of staff propose or decide.

Revision ID: 0161_identity_phone_fence
Revises: 0160_identity_phone_changes

A proposal holds a phone number, so a transaction acting for a client, or for nobody, must never
read one, as with the other two-person tables. The guard now pins its search path to
`pg_catalog, pg_temp`, names every object in full, and refuses a member of staff whose account
is no longer active, so a disabled account cannot decide a change on a session it still holds.
"""

from alembic import op

revision = "0161_identity_phone_fence"
down_revision = "0160_identity_phone_changes"
branch_labels = None
depends_on = None

FENCE = [
    "ALTER TABLE identity_phone_changes ENABLE ROW LEVEL SECURITY",
    # Which staff may propose or approve is a permission, checked in the application. The
    # change itself runs as the table owner, inside identity_apply_phone_change.
    "CREATE POLICY identity_phone_changes_staff ON identity_phone_changes TO finvest_app "
    "USING (public.finvest_actor_kind() = 'staff') "
    "WITH CHECK (public.finvest_actor_kind() = 'staff')",
]

GUARD = """
CREATE OR REPLACE FUNCTION identity_phone_changes_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff'
            OR NOT EXISTS (
                SELECT FROM public.identity_staff
                WHERE id = public.finvest_actor_id() AND status = 'active'
            ) THEN
            RAISE EXCEPTION 'only active staff propose and decide a new number'
                USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed'
                OR NEW.proposed_by IS DISTINCT FROM public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a number change starts as proposed, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided number change never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
            OR (to_jsonb(NEW) - '{status,decided_by,decided_at,decision_note}'::text[])
                <> (to_jsonb(OLD) - '{status,decided_by,decided_at,decision_note}'::text[]) THEN
            RAISE EXCEPTION 'a number change is only decided, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# The guard as 0160 created it, for the downgrade.
OLD_GUARD = """
CREATE OR REPLACE FUNCTION identity_phone_changes_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff propose and decide a new number' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by IS DISTINCT FROM finvest_actor_id() THEN
                RAISE EXCEPTION 'a number change starts as proposed, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided number change never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM finvest_actor_id()
            OR (to_jsonb(NEW) - '{status,decided_by,decided_at,decision_note}'::text[])
                <> (to_jsonb(OLD) - '{status,decided_by,decided_at,decision_note}'::text[]) THEN
            RAISE EXCEPTION 'a number change is only decided, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""


def upgrade() -> None:
    for statement in FENCE:
        op.execute(statement)
    op.execute(GUARD)


def downgrade() -> None:
    # Nothing is dropped, so no rows are at risk: the table only loses its fence.
    op.execute(OLD_GUARD)
    op.execute("DROP POLICY identity_phone_changes_staff ON identity_phone_changes")
    op.execute("ALTER TABLE identity_phone_changes DISABLE ROW LEVEL SECURITY")
