"""Let staff lift an email suppression, with two people.

Revision ID: 0153_email_suppression_lifts
Revises: 0101_complaint_outcomes

Owned by the notifications module. See "Lifting a suppression" in docs/notifications.md.

* `notification_email_suppression_lifts` holds each proposal to send email again to an
  address that bounced for good or complained. One member of staff proposes with a reason and
  a different one approves, and PostgreSQL enforces that. Nothing in
  `notification_email_suppressions` is ever updated or deleted: a lift is a later fact that
  outweighs the suppressing events before it, and a new bounce or complaint after the lift
  suppresses the address again.
* Staff transactions may read the suppression list and the events, which hold only address
  hashes, so the console can list them.
* A GIN index on the events' recipients finds an address's latest suppressing event.
* An address that ever sent a spam complaint is never lifted: the guard refuses the proposal.
"""

from alembic import op

revision = "0153_email_suppression_lifts"
down_revision = "0101_complaint_outcomes"
branch_labels = None
depends_on = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE notification_email_suppression_lifts (
    id push_id NOT NULL,
    address_hash char(64) COLLATE "C" NOT NULL,
    reason varchar(500) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'proposed',
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    CONSTRAINT pk_notification_email_suppression_lifts PRIMARY KEY (id),
    CONSTRAINT fk_notification_email_suppression_lifts_address_hash
        FOREIGN KEY (address_hash) REFERENCES notification_email_suppressions (address_hash),
    CONSTRAINT ck_notification_email_suppression_lifts_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_notification_email_suppression_lifts_status
        CHECK (status IN ('proposed', 'approved', 'rejected', 'withdrawn')),
    CONSTRAINT ck_notification_email_suppression_lifts_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (decided_by IS NULL) = (decision_reason IS NULL)),
    CONSTRAINT ck_notification_email_suppression_lifts_decision_reason
        CHECK (decision_reason IS NULL OR btrim(decision_reason) <> ''),
    CONSTRAINT ck_notification_email_suppression_lifts_two_people CHECK (
        status = 'proposed'
        OR (status = 'withdrawn' AND decided_by = proposed_by)
        OR (status IN ('approved', 'rejected') AND decided_by <> proposed_by))
);
CREATE UNIQUE INDEX uq_notification_email_suppression_lifts_open
    ON notification_email_suppression_lifts (address_hash) WHERE status = 'proposed';
CREATE INDEX ix_notification_email_suppression_lifts_approved
    ON notification_email_suppression_lifts (address_hash, decided_at DESC)
    WHERE status = 'approved';
CREATE INDEX ix_notification_email_events_recipients
    ON notification_email_events USING gin (recipients)
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001).
GUARD = """
CREATE FUNCTION finvest_notification_email_suppression_lifts_guard() RETURNS trigger
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

RULES = [
    "REVOKE ALL ON FUNCTION finvest_notification_email_suppression_lifts_guard() FROM PUBLIC",
    "CREATE TRIGGER notification_email_suppression_lifts_guard BEFORE INSERT OR UPDATE "
    "ON notification_email_suppression_lifts FOR EACH ROW "
    "EXECUTE FUNCTION finvest_notification_email_suppression_lifts_guard()",
    "ALTER TABLE notification_email_suppression_lifts ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON notification_email_suppression_lifts FROM PUBLIC",
    # No DELETE for anyone: why mail was sent again to an address that refused it is a record.
    f"GRANT SELECT, INSERT ON notification_email_suppression_lifts TO {APP_ROLE}",
    "GRANT UPDATE (status, decided_by, decided_at, decision_reason) "
    f"ON notification_email_suppression_lifts TO {APP_ROLE}",
    f"GRANT SELECT ON notification_email_suppression_lifts TO {WORKER_ROLE}",
    "CREATE POLICY notification_email_suppression_lifts_staff "
    f"ON notification_email_suppression_lifts TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'staff') WITH CHECK (finvest_actor_kind() = 'staff')",
    "CREATE POLICY notification_email_suppression_lifts_worker "
    f"ON notification_email_suppression_lifts FOR SELECT TO {WORKER_ROLE} USING (true)",
    # Staff read the list and what caused it. Both hold hashes only, never an address.
    f"GRANT SELECT ON notification_email_suppressions TO {APP_ROLE}",
    f"GRANT SELECT (svix_id, delivery, recipients, permanent, received_at) "
    f"ON notification_email_events TO {APP_ROLE}",
    "CREATE POLICY notification_email_suppressions_staff_read "
    f"ON notification_email_suppressions FOR SELECT TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'staff')",
    "CREATE POLICY notification_email_events_staff_read "
    f"ON notification_email_events FOR SELECT TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'staff')",
]


def split(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in split(TABLES):
        op.execute(statement)
    op.execute(GUARD)
    for statement in RULES:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM notification_email_suppression_lifts) THEN
        RAISE EXCEPTION 'refusing to drop suppression lifts while any are recorded';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP POLICY notification_email_events_staff_read ON notification_email_events")
    op.execute(
        "DROP POLICY notification_email_suppressions_staff_read ON notification_email_suppressions"
    )
    op.execute(
        "REVOKE SELECT (svix_id, delivery, recipients, permanent, received_at) "
        "ON notification_email_events FROM finvest_app"
    )
    op.execute("REVOKE SELECT ON notification_email_suppressions FROM finvest_app")
    op.execute("DROP INDEX ix_notification_email_events_recipients")
    op.execute("DROP TABLE notification_email_suppression_lifts")
    op.execute("DROP FUNCTION finvest_notification_email_suppression_lifts_guard()")
