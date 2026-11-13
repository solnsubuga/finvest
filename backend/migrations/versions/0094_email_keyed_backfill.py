"""Let the worker fill the keyed hash of rows written before it existed.

Revision ID: 0094_email_keyed_backfill
Revises: 0065_investment_carried_over

The second step of three (docs/notifications.md#how-addresses-are-hashed), first half. Rows saved
before 0093 have no keyed hash. The worker, which alone holds the key with the API, works each one
out from the plain hash beside it (`notifications.keyed_hashes`). The migrator never holds the key.

The worker may set each keyed column once, from empty, and change nothing else in the row. A
trigger holds that for events and suppressions, and the lifts guard gains the same exception.
Reads still use the plain columns; they move once nothing is left to fill.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0094_email_keyed_backfill"
down_revision: str | None = "0065_investment_carried_over"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
zero_downtime = True

WORKER_ROLE = "finvest_worker"

# The actor settings are a fence against bugs, not a credential (see migration 0001).
ONCE = """
CREATE FUNCTION public.finvest_email_keyed_once() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        column_name text := TG_ARGV[0];
        before jsonb := to_jsonb(OLD) -> column_name;
    BEGIN
        IF before <> 'null'::jsonb AND before IS DISTINCT FROM to_jsonb(NEW) -> column_name THEN
            RAISE EXCEPTION 'a keyed hash is set once and never changed' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

LIFTS_GUARD = """
CREATE OR REPLACE FUNCTION public.finvest_notification_email_suppression_lifts_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        -- The worker fills a keyed hash left empty before migration 0093, and changes nothing
        -- else. Which hash it is, is checked by the worker, which alone holds the key.
        IF TG_OP = 'UPDATE' AND OLD.address_key IS NULL AND NEW.address_key IS NOT NULL
            AND (to_jsonb(NEW) - 'address_key') = (to_jsonb(OLD) - 'address_key') THEN
            RETURN NEW;
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff lift a suppression' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a lift starts as a proposal by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            -- A spam complaint is the person refusing our email. It is never lifted.
            IF EXISTS (
                SELECT FROM public.notification_email_suppressions AS s
                WHERE s.address_hash = NEW.address_hash AND s.reason = 'complained'
            ) OR EXISTS (
                SELECT FROM public.notification_email_events AS e
                WHERE e.recipients @> ARRAY[NEW.address_hash::text]
                  AND e.delivery = 'complained'
            ) THEN
                RAISE EXCEPTION 'an address that complained is never lifted'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided lift never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
            OR (to_jsonb(NEW) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason'])
               <> (to_jsonb(OLD) - ARRAY['status', 'decided_by', 'decided_at',
                                         'decision_reason']) THEN
            RAISE EXCEPTION 'a lift is decided once, by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        IF NOT EXISTS (SELECT FROM public.identity_staff
                       WHERE id = NEW.decided_by AND status = 'active') THEN
            RAISE EXCEPTION 'only an active member of staff decides a lift'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

ORIGINAL_LIFTS_GUARD = """
CREATE OR REPLACE FUNCTION public.finvest_notification_email_suppression_lifts_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff lift a suppression' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a lift starts as a proposal by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            -- A spam complaint is the person refusing our email. It is never lifted.
            IF EXISTS (
                SELECT FROM public.notification_email_suppressions AS s
                WHERE s.address_hash = NEW.address_hash AND s.reason = 'complained'
            ) OR EXISTS (
                SELECT FROM public.notification_email_events AS e
                WHERE e.recipients @> ARRAY[NEW.address_hash::text]
                  AND e.delivery = 'complained'
            ) THEN
                RAISE EXCEPTION 'an address that complained is never lifted'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided lift never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
            OR (to_jsonb(NEW) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason'])
               <> (to_jsonb(OLD) - ARRAY['status', 'decided_by', 'decided_at',
                                         'decision_reason']) THEN
            RAISE EXCEPTION 'a lift is decided once, by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        IF NOT EXISTS (SELECT FROM public.identity_staff
                       WHERE id = NEW.decided_by AND status = 'active') THEN
            RAISE EXCEPTION 'only an active member of staff decides a lift'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

UPGRADE = [
    ONCE,
    "REVOKE ALL ON FUNCTION public.finvest_email_keyed_once() FROM PUBLIC",
    "CREATE TRIGGER notification_email_events_keyed_once BEFORE UPDATE OF recipients_keyed "
    "ON notification_email_events FOR EACH ROW "
    "EXECUTE FUNCTION public.finvest_email_keyed_once('recipients_keyed')",
    "CREATE TRIGGER notification_email_suppressions_keyed_once BEFORE UPDATE "
    "ON notification_email_suppressions FOR EACH ROW "
    "EXECUTE FUNCTION public.finvest_email_keyed_once('address_key')",
    LIFTS_GUARD,
    f"GRANT UPDATE (recipients_keyed) ON notification_email_events TO {WORKER_ROLE}",
    f"GRANT UPDATE (address_key) ON notification_email_suppressions TO {WORKER_ROLE}",
    f"GRANT UPDATE (address_key) ON notification_email_suppression_lifts TO {WORKER_ROLE}",
    "CREATE POLICY notification_email_suppression_lifts_worker_key "
    f"ON notification_email_suppression_lifts FOR UPDATE TO {WORKER_ROLE} "
    "USING (address_key IS NULL) WITH CHECK (true)",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # Keyed hashes already filled stay: each is worked out again from the plain hash beside it.
    op.execute(
        "DROP POLICY notification_email_suppression_lifts_worker_key "
        "ON notification_email_suppression_lifts"
    )
    op.execute(
        f"REVOKE UPDATE (address_key) ON notification_email_suppression_lifts FROM {WORKER_ROLE}"
    )
    op.execute(f"REVOKE UPDATE (address_key) ON notification_email_suppressions FROM {WORKER_ROLE}")
    op.execute(f"REVOKE UPDATE (recipients_keyed) ON notification_email_events FROM {WORKER_ROLE}")
    op.execute(ORIGINAL_LIFTS_GUARD)
    op.execute(
        "DROP TRIGGER notification_email_suppressions_keyed_once ON notification_email_suppressions"
    )
    op.execute("DROP TRIGGER notification_email_events_keyed_once ON notification_email_events")
    op.execute("DROP FUNCTION public.finvest_email_keyed_once()")
