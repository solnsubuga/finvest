"""Add two-person corrections to the ledger.

Revision ID: 0013_ledger_corrections
Revises: 0030_payout_destinations

A reversal is refused once the money has moved on, because it would overdraw. The fix is a new
entry that takes the money back from where it sits now. Such an entry is a judgement call, so it
needs two people: one staff member proposes it and a different one approves it.

PostgreSQL enforces this, not only the application:

* A proposal and a decision each name the staff member whose transaction wrote them.
* The person who decides is never the person who proposed.
* A proposal is decided once.
* A journal entry with source_type 'ledger.correction' cannot be committed unless an approved
  proposal points at it, and it matches that proposal in type, date, currency and every line.
* Proposals and decisions are insert-only for every role.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_ledger_corrections"
down_revision: str | None = "0030_payout_destinations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

CODE = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"

TABLES = f"""
CREATE TABLE ledger_correction_proposals (
    id push_id NOT NULL,
    corrects_entry_id push_id NOT NULL,
    reverses boolean NOT NULL,
    entry_type varchar(100) NOT NULL,
    currency varchar(3) NOT NULL,
    effective_date date NOT NULL,
    lines jsonb NOT NULL,
    reason varchar(500) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    CONSTRAINT pk_ledger_correction_proposals PRIMARY KEY (id),
    CONSTRAINT fk_ledger_correction_proposals_corrects_entry_id
        FOREIGN KEY (corrects_entry_id) REFERENCES ledger_journal_entries (id),
    CONSTRAINT ck_ledger_correction_proposals_entry_type CHECK (entry_type ~ '{CODE}'),
    CONSTRAINT ck_ledger_correction_proposals_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_ledger_correction_proposals_lines
        CHECK (jsonb_typeof(lines) = 'array' AND jsonb_array_length(lines) BETWEEN 2 AND 500),
    CONSTRAINT ck_ledger_correction_proposals_reason CHECK (length(btrim(reason)) > 0)
);
CREATE INDEX ix_ledger_correction_proposals_corrects_entry_id
    ON ledger_correction_proposals (corrects_entry_id);
CREATE INDEX ix_ledger_correction_proposals_proposed_at
    ON ledger_correction_proposals (proposed_at, id);

CREATE TABLE ledger_correction_decisions (
    proposal_id push_id NOT NULL,
    outcome varchar(16) NOT NULL,
    decided_by push_id NOT NULL,
    decided_at timestamptz NOT NULL,
    reason varchar(500),
    entry_id push_id,
    CONSTRAINT pk_ledger_correction_decisions PRIMARY KEY (proposal_id),
    CONSTRAINT fk_ledger_correction_decisions_proposal_id
        FOREIGN KEY (proposal_id) REFERENCES ledger_correction_proposals (id),
    CONSTRAINT fk_ledger_correction_decisions_entry_id
        FOREIGN KEY (entry_id) REFERENCES ledger_journal_entries (id),
    CONSTRAINT uq_ledger_correction_decisions_entry_id UNIQUE (entry_id),
    CONSTRAINT ck_ledger_correction_decisions_outcome CHECK (outcome IN ('approved', 'rejected')),
    CONSTRAINT ck_ledger_correction_decisions_entry
        CHECK ((outcome = 'approved') = (entry_id IS NOT NULL)),
    CONSTRAINT ck_ledger_correction_decisions_reason CHECK (length(btrim(reason)) > 0),
    CONSTRAINT ck_ledger_correction_decisions_rejection_reason
        CHECK (outcome <> 'rejected' OR reason IS NOT NULL)
)
"""

FUNCTIONS = [
    """
CREATE FUNCTION finvest_ledger_corrections_immutable() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'ledger table % is insert-only', TG_TABLE_NAME USING ERRCODE = '42501';
    END;
    $$
""",
    # Runs as the table owner so it reads the proposal whatever the caller may see.
    """
CREATE FUNCTION finvest_ledger_second_person() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        proposer public.push_id;
    BEGIN
        SELECT proposed_by INTO proposer
        FROM public.ledger_correction_proposals WHERE id = NEW.proposal_id;
        IF proposer = NEW.decided_by THEN
            RAISE EXCEPTION 'a correction is decided by a second person, not its proposer'
                USING ERRCODE = '23514', CONSTRAINT = 'ledger_correction_second_person';
        END IF;
        RETURN NEW;
    END;
    $$
""",
    # Deferred, and queued by the entry, by each of its lines and by the decision, so that
    # whatever order they are written in, and whatever is added later in the transaction, the
    # entry that commits is the entry that was approved.
    # The comparison is a multiset one (EXCEPT ALL). An entry holds an account once, so a
    # proposal that repeats a line, or carries a malformed one, matches no entry at all.
    """
CREATE FUNCTION finvest_ledger_check_correction() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        checked_id public.push_id;
        entry public.ledger_journal_entries%ROWTYPE;
        proposal public.ledger_correction_proposals%ROWTYPE;
    BEGIN
        IF TG_TABLE_NAME = 'ledger_journal_entries' THEN
            checked_id := NEW.id;
        ELSE
            checked_id := NEW.entry_id;
        END IF;
        IF checked_id IS NULL THEN
            RETURN NULL;  -- a rejection has no entry
        END IF;
        SELECT * INTO entry FROM public.ledger_journal_entries WHERE id = checked_id;
        IF entry.source_type IS DISTINCT FROM 'ledger.correction' THEN
            IF TG_TABLE_NAME = 'ledger_correction_decisions' THEN
                RAISE EXCEPTION 'an approval must point at a correction entry'
                    USING ERRCODE = '23514', CONSTRAINT = 'ledger_correction_entry';
            END IF;
            RETURN NULL;
        END IF;
        SELECT p.* INTO proposal
        FROM public.ledger_correction_decisions AS d
        JOIN public.ledger_correction_proposals AS p ON p.id = d.proposal_id
        WHERE d.entry_id = checked_id AND d.outcome = 'approved';
        IF NOT FOUND THEN
            RAISE EXCEPTION 'a correction entry needs an approved proposal'
                USING ERRCODE = '23514', CONSTRAINT = 'ledger_correction_approved';
        END IF;
        IF entry.source_id IS DISTINCT FROM proposal.id::text
            OR entry.entry_type <> proposal.entry_type
            OR entry.currency <> proposal.currency
            OR entry.effective_date <> proposal.effective_date
            OR entry.reverses_entry_id IS DISTINCT FROM
               (CASE WHEN proposal.reverses THEN proposal.corrects_entry_id END)
            OR EXISTS (
                (SELECT account_id::text, amount_minor FROM public.ledger_lines
                 WHERE entry_id = checked_id
                 EXCEPT ALL
                 SELECT account_id, amount_minor
                 FROM jsonb_to_recordset(proposal.lines) AS line(account_id text, amount_minor bigint))
                UNION ALL
                (SELECT account_id, amount_minor
                 FROM jsonb_to_recordset(proposal.lines) AS line(account_id text, amount_minor bigint)
                 EXCEPT ALL
                 SELECT account_id::text, amount_minor FROM public.ledger_lines
                 WHERE entry_id = checked_id)
            )
        THEN
            RAISE EXCEPTION 'a correction entry must be exactly what was proposed'
                USING ERRCODE = '23514', CONSTRAINT = 'ledger_correction_matches';
        END IF;
        RETURN NULL;
    END;
    $$
""",
]

TABLE_NAMES = ("ledger_correction_proposals", "ledger_correction_decisions")
STAFF = "finvest_actor_kind() = 'staff'"

RULES = [
    *(
        f"REVOKE ALL ON FUNCTION {name}() FROM PUBLIC"
        for name in (
            "finvest_ledger_corrections_immutable",
            "finvest_ledger_second_person",
            "finvest_ledger_check_correction",
        )
    ),
    *(
        f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION finvest_ledger_corrections_immutable()"
        for table in TABLE_NAMES
    ),
    *(
        f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION finvest_ledger_corrections_immutable()"
        for table in TABLE_NAMES
    ),
    "CREATE TRIGGER ledger_correction_decisions_second_person "
    "BEFORE INSERT ON ledger_correction_decisions "
    "FOR EACH ROW EXECUTE FUNCTION finvest_ledger_second_person()",
    *(
        f"CREATE CONSTRAINT TRIGGER {table}_check_correction AFTER INSERT ON {table} "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION finvest_ledger_check_correction()"
        for table in ("ledger_journal_entries", "ledger_lines", "ledger_correction_decisions")
    ),
    *(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" for table in TABLE_NAMES),
    *(f"REVOKE ALL ON {table} FROM PUBLIC" for table in TABLE_NAMES),
    *(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}" for table in TABLE_NAMES),
    # The worker can report on corrections. It is not a person, so it cannot make or decide one.
    *(f"GRANT SELECT ON {table} TO {WORKER_ROLE}" for table in TABLE_NAMES),
    *(
        f"CREATE POLICY {table}_worker_read ON {table} FOR SELECT TO {WORKER_ROLE} USING (true)"
        for table in TABLE_NAMES
    ),
    *(
        f"CREATE POLICY {table}_app_read ON {table} FOR SELECT TO {APP_ROLE} USING ({STAFF})"
        for table in TABLE_NAMES
    ),
    # Each row names the staff member whose transaction wrote it, and nobody else.
    f"CREATE POLICY ledger_correction_proposals_app_insert ON ledger_correction_proposals "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF} AND proposed_by = finvest_actor_id())",
    f"CREATE POLICY ledger_correction_decisions_app_insert ON ledger_correction_decisions "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF} AND decided_by = finvest_actor_id())",
]

EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM ledger_correction_proposals) THEN
        RAISE EXCEPTION 'refusing to drop ledger corrections that hold proposals';
    END IF;
END
$$
"""


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLES), *FUNCTIONS, *RULES]:
        op.execute(statement)


def downgrade() -> None:
    # Dropping the tables would erase who proposed and approved each correction.
    op.execute(EMPTY_GUARD)
    for table in ("ledger_journal_entries", "ledger_lines"):
        op.execute(f"DROP TRIGGER {table}_check_correction ON {table}")
    op.execute("DROP TABLE ledger_correction_decisions")
    op.execute("DROP TABLE ledger_correction_proposals")
    for function in (
        "finvest_ledger_check_correction",
        "finvest_ledger_second_person",
        "finvest_ledger_corrections_immutable",
    ):
        op.execute(f"DROP FUNCTION {function}()")
