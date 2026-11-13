"""Close a complaint with two people, and log each complaint once per key.

Revision ID: 0101_complaint_outcomes
Revises: 0082_dealing_maturities

Owned by the support module. See "Closing a complaint" in docs/support.md.

* `complaint_outcome_proposals`: one member of staff proposes the outcome, what was done and
  whether the client accepted it. A different one confirms or rejects it, and the proposer may
  withdraw it. Whoever logged the complaint can never confirm its outcome. One proposal waits
  per complaint at a time, and a decided proposal never changes.
* Closing a complaint now needs a confirmed proposal that says the same, confirmed by the
  person closing it, in the same transaction. PostgreSQL refuses anything else.
* `complaints.client_key`: the key the app or the console sends with a new complaint, so a
  request repeated after a lost answer logs it once.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0101_complaint_outcomes"
down_revision: str | None = "0082_dealing_maturities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
_STAFF = "finvest_actor_kind() = 'staff'"

OLD_ACTIONS = "'logged', 'assigned', 'status', 'note', 'escalated', 'closed'"
NEW_ACTIONS = f"{OLD_ACTIONS}, 'proposed', 'rejected', 'withdrawn'"

TABLES = """
CREATE TABLE complaint_outcome_proposals (
    id push_id NOT NULL,
    complaint_id push_id NOT NULL,
    outcome varchar(16) NOT NULL,
    resolution varchar(2000) NOT NULL,
    client_accepted boolean NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    status varchar(12) NOT NULL DEFAULT 'pending',
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(2000),
    CONSTRAINT pk_complaint_outcome_proposals PRIMARY KEY (id),
    CONSTRAINT fk_complaint_outcome_proposals_complaint
        FOREIGN KEY (complaint_id) REFERENCES complaints (id),
    CONSTRAINT ck_complaint_outcome_proposals_outcome
        CHECK (outcome IN ('upheld', 'partly_upheld', 'not_upheld') AND btrim(resolution) <> ''),
    CONSTRAINT ck_complaint_outcome_proposals_status
        CHECK (status IN ('pending', 'confirmed', 'rejected', 'withdrawn')),
    CONSTRAINT ck_complaint_outcome_proposals_decided CHECK (
        (status = 'pending') = (decided_by IS NULL)
        AND (status = 'pending') = (decided_at IS NULL)),
    -- A rejection says why, so the proposer knows what to change.
    CONSTRAINT ck_complaint_outcome_proposals_reason
        CHECK (status <> 'rejected' OR btrim(decision_reason) <> '')
);
CREATE UNIQUE INDEX uq_complaint_outcome_proposals_pending
    ON complaint_outcome_proposals (complaint_id) WHERE status = 'pending';
ALTER TABLE complaints
    ADD COLUMN client_key varchar(64) COLLATE "C",
    ADD CONSTRAINT ck_complaints_client_key CHECK (client_key ~ '^[A-Za-z0-9._:-]{8,64}$');
CREATE UNIQUE INDEX uq_complaints_client_key
    ON complaints (logged_by, client_key) WHERE client_key IS NOT NULL
"""

PROPOSALS_GUARD = """
CREATE FUNCTION finvest_complaint_outcome_proposals_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        complaint record;
    BEGIN
        SELECT status, logged_by_kind, logged_by INTO complaint
            FROM public.complaints WHERE id = NEW.complaint_id;
        IF complaint.status = 'closed' THEN
            RAISE EXCEPTION 'a closed complaint takes no outcome' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'pending' OR NEW.proposed_by <> public.finvest_actor_id() THEN
                RAISE EXCEPTION 'an outcome is proposed by the acting member of staff'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'pending' THEN
            RAISE EXCEPTION 'a decided outcome never changes' USING ERRCODE = '42501';
        END IF;
        IF (to_jsonb(NEW) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason'])
            <> (to_jsonb(OLD) - ARRAY['status', 'decided_by', 'decided_at', 'decision_reason']) THEN
            RAISE EXCEPTION 'a proposed outcome never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.decided_by IS DISTINCT FROM public.finvest_actor_id() THEN
            RAISE EXCEPTION 'an outcome is decided by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'withdrawn' AND NEW.decided_by <> OLD.proposed_by THEN
            RAISE EXCEPTION 'only the proposer withdraws an outcome' USING ERRCODE = '42501';
        END IF;
        IF NEW.status IN ('confirmed', 'rejected') AND NEW.decided_by = OLD.proposed_by THEN
            RAISE EXCEPTION 'an outcome is decided by someone other than its proposer'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'confirmed' AND complaint.logged_by_kind = 'staff'
            AND NEW.decided_by = complaint.logged_by THEN
            RAISE EXCEPTION 'whoever logged a complaint does not confirm its outcome'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# 0100's guard, with the rule that closing follows a confirmed outcome.
COMPLAINTS_GUARD = """
CREATE OR REPLACE FUNCTION finvest_complaints_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF OLD.status = 'closed' THEN
            RAISE EXCEPTION 'a closed complaint never changes' USING ERRCODE = '42501';
        END IF;
        IF (to_jsonb(NEW) - ARRAY['status', 'owner_id', 'escalated_at', 'closed_at', 'closed_by',
                'outcome', 'resolution', 'client_accepted', 'updated_at'])
            <> (to_jsonb(OLD) - ARRAY['status', 'owner_id', 'escalated_at', 'closed_at',
                'closed_by', 'outcome', 'resolution', 'client_accepted', 'updated_at']) THEN
            RAISE EXCEPTION 'what a complaint said when it was logged never changes'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'closed' AND NEW.closed_by IS DISTINCT FROM public.finvest_actor_id() THEN
            RAISE EXCEPTION 'a complaint is closed by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'closed' AND NOT EXISTS (
            SELECT FROM public.complaint_outcome_proposals p
            WHERE p.complaint_id = NEW.id AND p.status = 'confirmed'
                AND p.decided_by = NEW.closed_by AND p.outcome = NEW.outcome
                AND p.resolution = NEW.resolution AND p.client_accepted = NEW.client_accepted) THEN
            RAISE EXCEPTION 'a complaint closes only with the outcome a second person confirmed'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

OLD_COMPLAINTS_GUARD = """
CREATE OR REPLACE FUNCTION finvest_complaints_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF OLD.status = 'closed' THEN
            RAISE EXCEPTION 'a closed complaint never changes' USING ERRCODE = '42501';
        END IF;
        IF (to_jsonb(NEW) - ARRAY['status', 'owner_id', 'escalated_at', 'closed_at', 'closed_by',
                'outcome', 'resolution', 'client_accepted', 'updated_at'])
            <> (to_jsonb(OLD) - ARRAY['status', 'owner_id', 'escalated_at', 'closed_at',
                'closed_by', 'outcome', 'resolution', 'client_accepted', 'updated_at']) THEN
            RAISE EXCEPTION 'what a complaint said when it was logged never changes'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'closed' AND NEW.closed_by IS DISTINCT FROM public.finvest_actor_id() THEN
            RAISE EXCEPTION 'a complaint is closed by the acting member of staff'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

EVENT_ACTIONS = (
    "ALTER TABLE complaint_events DROP CONSTRAINT ck_complaint_events_action, "
    "ADD CONSTRAINT ck_complaint_events_action CHECK (action IN ({actions}))"
)

RULES = [
    "REVOKE ALL ON FUNCTION finvest_complaint_outcome_proposals_guard() FROM PUBLIC",
    "CREATE TRIGGER complaint_outcome_proposals_guard BEFORE INSERT OR UPDATE "
    "ON complaint_outcome_proposals "
    "FOR EACH ROW EXECUTE FUNCTION finvest_complaint_outcome_proposals_guard()",
    "REVOKE ALL ON complaint_outcome_proposals FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_reason) "
    f"ON complaint_outcome_proposals TO {APP_ROLE}",
    "ALTER TABLE complaint_outcome_proposals ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY complaint_outcome_proposals_staff ON complaint_outcome_proposals "
    f"TO {APP_ROLE} USING ({_STAFF}) WITH CHECK ({_STAFF})",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [
        *split(TABLES),
        PROPOSALS_GUARD,
        COMPLAINTS_GUARD,
        EVENT_ACTIONS.format(actions=NEW_ACTIONS),
        *RULES,
    ]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM complaint_outcome_proposals)
        OR EXISTS (SELECT FROM complaints WHERE client_key IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop complaint outcomes or keys that hold records';
    END IF;
END
$$
"""


def downgrade() -> None:
    # Who proposed and who confirmed an outcome is part of the regulated register.
    op.execute(EMPTY_GUARD)
    op.execute(OLD_COMPLAINTS_GUARD)
    op.execute(EVENT_ACTIONS.format(actions=OLD_ACTIONS))
    op.execute("DROP INDEX uq_complaints_client_key")
    op.execute(
        "ALTER TABLE complaints DROP CONSTRAINT ck_complaints_client_key, DROP COLUMN client_key"
    )
    op.execute("DROP TABLE complaint_outcome_proposals")
    op.execute("DROP FUNCTION finvest_complaint_outcome_proposals_guard()")
