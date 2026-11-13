"""Add the notification centre, channel choices and staff-edited wordings.

Revision ID: 0090_notification_inbox
Revises: 0031_payouts

Three tables, all owned by the notifications module. See docs/notifications.md.

* `notifications` is what a client was told, in the words used at the time. A row is written in
  the transaction of the thing it reports, and is never changed except to mark it read.
* `notification_preferences` is the client's choice of push, SMS and email per kind of alert.
  A security alert always keeps SMS or email, and PostgreSQL enforces it.
* `notification_wordings` holds every proposed change to a message's words. One member of staff
  proposes and a different one decides, and PostgreSQL enforces that too: a message that goes
  to every client is not something one person can rewrite alone.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0090_notification_inbox"
down_revision: str | None = "0061_investments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

CATEGORIES = "'money', 'investments', 'earnings', 'account', 'security', 'support', 'news'"

TABLES = f"""
CREATE TABLE notifications (
    id push_id NOT NULL,
    -- The client's ID in the identity module. No foreign key: a module owns only its own tables.
    client_id push_id NOT NULL,
    event varchar(64) NOT NULL,
    category varchar(16) NOT NULL,
    -- Unique per client and event, so the same payment or payout is never announced twice,
    -- and a key such as a day or a statement period tells every client it applies to.
    idempotency_key varchar(200) COLLATE "C" NOT NULL,
    title varchar(300) NOT NULL,
    body varchar(2000) NOT NULL,
    sms varchar(320) NOT NULL,
    -- What the app opens when the notification is tapped. An identifier, never a web link.
    link_kind varchar(32),
    link_id varchar(64) COLLATE "C",
    created_at timestamptz NOT NULL,
    read_at timestamptz,
    CONSTRAINT pk_notifications PRIMARY KEY (id),
    CONSTRAINT uq_notifications_client_event_key UNIQUE (client_id, event, idempotency_key),
    CONSTRAINT ck_notifications_event CHECK (event ~ '^[a-z][a-z0-9_]{{0,63}}$'),
    CONSTRAINT ck_notifications_category CHECK (category IN ({CATEGORIES})),
    CONSTRAINT ck_notifications_idempotency_key
        CHECK (idempotency_key ~ '^[A-Za-z0-9._:@-]{{1,200}}$'),
    CONSTRAINT ck_notifications_text
        CHECK (btrim(title) <> '' AND btrim(body) <> '' AND btrim(sms) <> ''),
    CONSTRAINT ck_notifications_link CHECK (
        (link_kind IS NULL) = (link_id IS NULL)
        AND link_kind ~ '^[a-z][a-z0-9_]{{0,31}}$' AND link_id ~ '^[A-Za-z0-9._:-]{{1,64}}$')
);
CREATE INDEX ix_notifications_client ON notifications (client_id, created_at DESC, id DESC);
CREATE INDEX ix_notifications_unread ON notifications (client_id) WHERE read_at IS NULL;

CREATE TABLE notification_preferences (
    client_id push_id NOT NULL,
    category varchar(16) NOT NULL,
    channels text[] NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_notification_preferences PRIMARY KEY (client_id, category),
    CONSTRAINT ck_notification_preferences_category CHECK (category IN ({CATEGORIES})),
    CONSTRAINT ck_notification_preferences_channels
        CHECK (channels <@ ARRAY['push', 'sms', 'email'] AND cardinality(channels) <= 3),
    -- A security alert must be able to reach the client outside the app.
    CONSTRAINT ck_notification_preferences_security
        CHECK (category <> 'security' OR channels && ARRAY['sms', 'email'])
);

CREATE TABLE notification_wordings (
    id push_id NOT NULL,
    event varchar(64) NOT NULL,
    title varchar(60) NOT NULL,
    body varchar(300) NOT NULL,
    sms varchar(300) NOT NULL,
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    CONSTRAINT pk_notification_wordings PRIMARY KEY (id),
    CONSTRAINT ck_notification_wordings_event CHECK (event ~ '^[a-z][a-z0-9_]{{0,63}}$'),
    CONSTRAINT ck_notification_wordings_text
        CHECK (btrim(title) <> '' AND btrim(body) <> '' AND btrim(sms) <> ''),
    CONSTRAINT ck_notification_wordings_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_notification_wordings_status
        CHECK (status IN ('proposed', 'approved', 'rejected', 'withdrawn')),
    CONSTRAINT ck_notification_wordings_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (decided_by IS NULL) = (decision_reason IS NULL)),
    -- Two people. Only the proposer can withdraw, and never approve or reject, their own.
    CONSTRAINT ck_notification_wordings_two_people CHECK (
        status = 'proposed'
        OR (status = 'withdrawn' AND decided_by = proposed_by)
        OR (status IN ('approved', 'rejected') AND decided_by <> proposed_by))
);
-- One change at a time per message, so an approval is always of the words the approver read.
CREATE UNIQUE INDEX uq_notification_wordings_open
    ON notification_wordings (event) WHERE status = 'proposed';
CREATE INDEX ix_notification_wordings_active
    ON notification_wordings (event, decided_at DESC, id DESC) WHERE status = 'approved'
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001). The guard
# names the acting member of staff on every row, so the two-person check above compares the
# people whose transactions wrote the proposal and the decision.
GUARD = """
CREATE FUNCTION finvest_notification_wordings_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    BEGIN
        IF finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff change a wording' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> finvest_actor_id() THEN
                RAISE EXCEPTION 'a wording starts as a proposal by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided wording never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM finvest_actor_id()
            OR (to_jsonb(NEW) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason'])
               <> (to_jsonb(OLD) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason'])
        THEN
            RAISE EXCEPTION 'a wording is decided once, by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_notification_wordings_guard() FROM PUBLIC",
    "CREATE TRIGGER notification_wordings_guard BEFORE INSERT OR UPDATE ON notification_wordings "
    "FOR EACH ROW EXECUTE FUNCTION finvest_notification_wordings_guard()",
    "REVOKE ALL ON notifications, notification_preferences, notification_wordings FROM PUBLIC",
    # No DELETE for anyone: what a client was told is part of the record.
    f"GRANT SELECT, INSERT, UPDATE (read_at) ON notifications TO {APP_ROLE}",
    f"GRANT SELECT, INSERT ON notifications TO {WORKER_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (channels, updated_at) ON notification_preferences TO {APP_ROLE}",
    f"GRANT SELECT ON notification_preferences TO {WORKER_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_reason) "
    f"ON notification_wordings TO {APP_ROLE}",
    f"GRANT SELECT ON notification_wordings TO {WORKER_ROLE}",
    "ALTER TABLE notifications ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE notification_preferences ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE notification_wordings ENABLE ROW LEVEL SECURITY",
    # A client reads and marks their own notifications. Staff read any client's, so support can
    # see what a client was told. Which staff may is a permission checked in the application.
    f"CREATE POLICY notifications_app_read ON notifications FOR SELECT TO {APP_ROLE} USING ("
    "(finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    # A request tells its own client, or a member of staff tells any client.
    f"CREATE POLICY notifications_app_insert ON notifications FOR INSERT TO {APP_ROLE} WITH CHECK ("
    "(finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY notifications_app_update ON notifications FOR UPDATE TO {APP_ROLE} USING ("
    "finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY notifications_worker ON notifications TO {WORKER_ROLE} "
    "USING (true) WITH CHECK (true)",
    f"CREATE POLICY notification_preferences_app_read ON notification_preferences FOR SELECT "
    f"TO {APP_ROLE} USING ((finvest_actor_kind() = 'client' AND client_id = finvest_actor_id()) "
    "OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY notification_preferences_app_insert ON notification_preferences FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY notification_preferences_app_update ON notification_preferences FOR UPDATE "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'client' AND client_id = finvest_actor_id())",
    f"CREATE POLICY notification_preferences_worker ON notification_preferences FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
    # Approved words are what every notification is written with, so any transaction that
    # raises one must be able to read them. Proposals and their history are for staff.
    f"CREATE POLICY notification_wordings_app_read ON notification_wordings FOR SELECT "
    f"TO {APP_ROLE} USING (status = 'approved' OR finvest_actor_kind() = 'staff')",
    f"CREATE POLICY notification_wordings_app_insert ON notification_wordings FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK (finvest_actor_kind() = 'staff')",
    f"CREATE POLICY notification_wordings_app_update ON notification_wordings FOR UPDATE "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'staff')",
    f"CREATE POLICY notification_wordings_worker ON notification_wordings FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM notifications) OR EXISTS (SELECT FROM notification_wordings)
        OR EXISTS (SELECT FROM notification_preferences) THEN
        RAISE EXCEPTION 'refusing to drop notifications that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # What clients were told, who changed the words and each client's choice of channels are
    # kept. A downgrade never erases them.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE notification_wordings")
    op.execute("DROP TABLE notification_preferences")
    op.execute("DROP TABLE notifications")
    op.execute("DROP FUNCTION finvest_notification_wordings_guard()")
