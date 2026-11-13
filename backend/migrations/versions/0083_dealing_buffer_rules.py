"""Add the liquidity buffer rules and their two-person changes.

Revision ID: 0083_dealing_buffer_rules
Revises: 0150_staff_changes

A version of the rules is proposed by one member of staff and approved by another, and takes
effect on a later day. A version is added only for an approved proposal, with exactly its
values, and is never changed or removed. The shilling rules from the canvas are seeded.
See docs/dealing-desk.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0083_dealing_buffer_rules"
down_revision: str | None = "0150_staff_changes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

RULE_COLUMNS = """
    currency char(3) COLLATE "C" NOT NULL,
    effective_from date NOT NULL,
    target_minor bigint NOT NULL,
    floor_minor bigint NOT NULL,
    from_buffer_up_to_minor bigint NOT NULL,
    pay_within_working_days integer NOT NULL,
"""


def rule_checks(table: str) -> str:
    return f"""
    CONSTRAINT ck_{table}_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_{table}_amounts CHECK (
        from_buffer_up_to_minor > 0 AND from_buffer_up_to_minor <= floor_minor
        AND floor_minor <= target_minor),
    CONSTRAINT ck_{table}_days CHECK (pay_within_working_days BETWEEN 1 AND 10)"""


TABLES = f"""
CREATE TABLE dealing_buffer_rule_proposals (
    id push_id NOT NULL,
    {RULE_COLUMNS}
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    -- Staff IDs in the identity module.
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_note varchar(500),
    CONSTRAINT pk_dealing_buffer_rule_proposals PRIMARY KEY (id),
    CONSTRAINT ck_dealing_buffer_rule_proposals_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_dealing_buffer_rule_proposals_status
        CHECK (status IN ('proposed', 'approved', 'sent_back', 'withdrawn')),
    CONSTRAINT ck_dealing_buffer_rule_proposals_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (status = 'proposed') = (decided_at IS NULL)),
    CONSTRAINT ck_dealing_buffer_rule_proposals_second_person CHECK (
        status NOT IN ('approved', 'sent_back') OR decided_by <> proposed_by),
    CONSTRAINT ck_dealing_buffer_rule_proposals_withdrawn CHECK (
        status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_dealing_buffer_rule_proposals_note CHECK (
        status <> 'sent_back' OR btrim(coalesce(decision_note, '')) <> ''),{rule_checks("dealing_buffer_rule_proposals")}
);
-- One change waits for approval per currency at a time.
CREATE UNIQUE INDEX uq_dealing_buffer_rule_proposals_waiting
    ON dealing_buffer_rule_proposals (currency) WHERE status = 'proposed';
CREATE TABLE dealing_buffer_rules (
    id push_id NOT NULL,
    {RULE_COLUMNS}
    -- The approved proposal it came from. NULL only for the rules seeded by a migration.
    proposal_id push_id,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_dealing_buffer_rules PRIMARY KEY (id),
    CONSTRAINT fk_dealing_buffer_rules_proposal
        FOREIGN KEY (proposal_id) REFERENCES dealing_buffer_rule_proposals (id),
    CONSTRAINT uq_dealing_buffer_rules_date UNIQUE (currency, effective_from),
    CONSTRAINT uq_dealing_buffer_rules_proposal UNIQUE (proposal_id),{rule_checks("dealing_buffer_rules")}
)
"""

PROPOSALS_GUARD = """
CREATE FUNCTION finvest_dealing_buffer_rule_proposals_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'a buffer rules proposal is never removed' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff propose and decide buffer rules' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed'
                OR NEW.proposed_by IS DISTINCT FROM public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a buffer rules proposal starts as proposed, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided buffer rules proposal never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'proposed' OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
            OR (to_jsonb(NEW) - '{status,decided_by,decided_at,decision_note}'::text[])
                <> (to_jsonb(OLD) - '{status,decided_by,decided_at,decision_note}'::text[]) THEN
            RAISE EXCEPTION 'a buffer rules proposal is only decided, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES_GUARD = f"""
CREATE FUNCTION finvest_dealing_buffer_rules_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        proposal public.dealing_buffer_rule_proposals%ROWTYPE;
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'buffer rules are never changed or removed' USING ERRCODE = '42501';
        END IF;
        IF NEW.proposal_id IS NULL THEN
            IF current_user IN ('{APP_ROLE}', '{WORKER_ROLE}') THEN
                RAISE EXCEPTION 'buffer rules need an approved proposal' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        SELECT * INTO proposal FROM public.dealing_buffer_rule_proposals WHERE id = NEW.proposal_id;
        IF NOT FOUND OR proposal.status <> 'approved'
            OR proposal.decided_by IS DISTINCT FROM public.finvest_actor_id()
            OR (proposal.currency, proposal.effective_from, proposal.target_minor,
                proposal.floor_minor, proposal.from_buffer_up_to_minor,
                proposal.pay_within_working_days)
            IS DISTINCT FROM (NEW.currency, NEW.effective_from, NEW.target_minor,
                NEW.floor_minor, NEW.from_buffer_up_to_minor, NEW.pay_within_working_days) THEN
            RAISE EXCEPTION 'buffer rules must match their approved proposal'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role names

RULES = [
    "REVOKE ALL ON FUNCTION finvest_dealing_buffer_rule_proposals_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_dealing_buffer_rules_guard() FROM PUBLIC",
    "CREATE TRIGGER dealing_buffer_rule_proposals_guard "
    "BEFORE INSERT OR UPDATE OR DELETE ON dealing_buffer_rule_proposals "
    "FOR EACH ROW EXECUTE FUNCTION finvest_dealing_buffer_rule_proposals_guard()",
    "CREATE TRIGGER dealing_buffer_rule_proposals_no_truncate "
    "BEFORE TRUNCATE ON dealing_buffer_rule_proposals "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_dealing_buffer_rule_proposals_guard()",
    "CREATE TRIGGER dealing_buffer_rules_guard "
    "BEFORE INSERT OR UPDATE OR DELETE ON dealing_buffer_rules "
    "FOR EACH ROW EXECUTE FUNCTION finvest_dealing_buffer_rules_guard()",
    "CREATE TRIGGER dealing_buffer_rules_no_truncate BEFORE TRUNCATE ON dealing_buffer_rules "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_dealing_buffer_rules_guard()",
    "REVOKE ALL ON dealing_buffer_rule_proposals, dealing_buffer_rules FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note) "
    f"ON dealing_buffer_rule_proposals TO {APP_ROLE}",
    f"GRANT SELECT, INSERT ON dealing_buffer_rules TO {APP_ROLE}",
    # The worker reads the rules when it funds an early withdrawal.
    f"GRANT SELECT ON dealing_buffer_rules TO {WORKER_ROLE}",
    "ALTER TABLE dealing_buffer_rule_proposals ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE dealing_buffer_rules ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY dealing_buffer_rule_proposals_staff ON dealing_buffer_rule_proposals "
    f"TO {APP_ROLE} USING (finvest_actor_kind() = 'staff') "
    "WITH CHECK (finvest_actor_kind() = 'staff')",
    f"CREATE POLICY dealing_buffer_rules_staff ON dealing_buffer_rules TO {APP_ROLE} "
    "USING (finvest_actor_kind() = 'staff') WITH CHECK (finvest_actor_kind() = 'staff')",
    f"CREATE POLICY dealing_buffer_rules_worker_read ON dealing_buffer_rules FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
]

# The canvas's rules. A test compares this row with SEED_RULES in the dealing domain.
SEED = (
    "INSERT INTO dealing_buffer_rules (id, currency, effective_from, target_minor, floor_minor, "
    "from_buffer_up_to_minor, pay_within_working_days, created_at) VALUES "
    "('FvBufferSeedUGX_0001', 'UGX', DATE '2026-01-01', 150000000, 100000000, 5000000, 2, now())"
)


def split(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), PROPOSALS_GUARD, RULES_GUARD, *RULES, SEED]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM dealing_buffer_rule_proposals)
        OR EXISTS (SELECT FROM dealing_buffer_rules WHERE proposal_id IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop buffer rules that staff proposed or approved';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE dealing_buffer_rules")
    op.execute("DROP TABLE dealing_buffer_rule_proposals")
    op.execute("DROP FUNCTION finvest_dealing_buffer_rules_guard()")
    op.execute("DROP FUNCTION finvest_dealing_buffer_rule_proposals_guard()")
