"""Let staff send an announcement to every client, with two people.

Revision ID: 0102_announcements
Revises: 0082_dealing_maturities

Owned by the notifications module. See "Announcements" in docs/notifications.md.

* `notification_announcements`: one member of staff proposes the words with a reason, and a
  different one approves or rejects them. The proposer may withdraw their own. An approved
  announcement is sent by the worker, as one notification per active client.
* PostgreSQL enforces the two people, that a decided announcement never changes, and that a
  rejection says why.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0102_announcements"
down_revision: str | None = "0082_dealing_maturities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE notification_announcements (
    id push_id NOT NULL,
    title varchar(60) NOT NULL,
    message varchar(300) NOT NULL,
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    CONSTRAINT pk_notification_announcements PRIMARY KEY (id),
    CONSTRAINT ck_notification_announcements_text
        CHECK (btrim(title) <> '' AND btrim(message) <> '' AND btrim(reason) <> ''),
    CONSTRAINT ck_notification_announcements_status
        CHECK (status IN ('proposed', 'approved', 'rejected', 'withdrawn')),
    CONSTRAINT ck_notification_announcements_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)),
    -- A rejection says why, so the proposer knows what to change.
    CONSTRAINT ck_notification_announcements_reason
        CHECK (status <> 'rejected' OR btrim(decision_reason) <> ''),
    -- Two people. Only the proposer can withdraw, and never approve or reject, their own.
    CONSTRAINT ck_notification_announcements_two_people CHECK (
        status = 'proposed'
        OR (status = 'withdrawn' AND decided_by = proposed_by)
        OR (status IN ('approved', 'rejected') AND decided_by <> proposed_by))
);
CREATE INDEX ix_notification_announcements_recent
    ON notification_announcements (proposed_at DESC, id DESC)
"""

# The actor settings are a fence against bugs, not a credential (see migration 0001).
GUARD = """
CREATE FUNCTION finvest_notification_announcements_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff write an announcement' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> public.finvest_actor_id() THEN
                RAISE EXCEPTION 'an announcement starts as a proposal by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided announcement never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
            OR (to_jsonb(NEW) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason'])
               <> (to_jsonb(OLD) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason'])
        THEN
            RAISE EXCEPTION 'an announcement is decided once, by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_notification_announcements_guard() FROM PUBLIC",
    "CREATE TRIGGER notification_announcements_guard BEFORE INSERT OR UPDATE "
    "ON notification_announcements "
    "FOR EACH ROW EXECUTE FUNCTION finvest_notification_announcements_guard()",
    "REVOKE ALL ON notification_announcements FROM PUBLIC",
    # No DELETE for anyone: what every client was told, and who agreed to it, is the record.
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_reason) "
    f"ON notification_announcements TO {APP_ROLE}",
    f"GRANT SELECT ON notification_announcements TO {WORKER_ROLE}",
    "ALTER TABLE notification_announcements ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY notification_announcements_staff ON notification_announcements "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'staff') "
    "WITH CHECK (finvest_actor_kind() = 'staff')",
    # The worker reads an approved announcement to send it, and nothing else.
    f"CREATE POLICY notification_announcements_worker ON notification_announcements "
    f"FOR SELECT TO {WORKER_ROLE} USING (status = 'approved')",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLE), GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM notification_announcements) THEN
        RAISE EXCEPTION 'refusing to drop announcements that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Who proposed and who approved what every client was told is part of the record.
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE notification_announcements")
    op.execute("DROP FUNCTION finvest_notification_announcements_guard()")
