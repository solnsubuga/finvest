"""Add the ledger movements reconciliation has seen, the matches, and the two-person day close.

Revision ID: 0041_reconciliation_day_close
Revises: 0120_autoinvest_plans

* A match pairs one statement line with one ledger movement. The database refuses a pair from
  different pots, or for different amounts, or going different ways. A match made by reference
  must carry the same reference on both sides; any other match is a person's, with a reason.
  A line and a movement are matched once and a match is never changed.
* A day close holds, for each pot, the statement balance, the ledger balance and what the open
  exceptions account for. A position that leaves a shilling unexplained cannot be saved.
* Two people. A close is prepared by one member of staff and closed or sent back by another
  signed-in member of staff. The database refuses the same person twice, and refuses a closer
  who imported any of the day's statements.
* Days are closed in order. Once a day is closed the next close is for the next day.
* Closes, positions, movements and matches are never deleted, for every role.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0041_reconciliation_day_close"
down_revision: str | None = "0120_autoinvest_plans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLES = """
CREATE TABLE reconciliation_movements (
    entry_id push_id NOT NULL,
    line_no integer NOT NULL,
    source varchar(16) NOT NULL,
    account_reference varchar(64) NOT NULL,
    effective_date date NOT NULL,
    currency varchar(3) NOT NULL,
    amount_minor bigint NOT NULL,
    reference varchar(64) NOT NULL,
    entry_type varchar(100) NOT NULL,
    recorded_at timestamptz NOT NULL,
    CONSTRAINT pk_reconciliation_movements PRIMARY KEY (entry_id, line_no),
    CONSTRAINT ck_reconciliation_movements_source
        CHECK (source IN ('iotec', 'bank', 'custodian')),
    CONSTRAINT ck_reconciliation_movements_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_reconciliation_movements_amount CHECK (amount_minor <> 0)
);
CREATE INDEX ix_reconciliation_movements_pot
    ON reconciliation_movements (source, account_reference, effective_date);
CREATE TABLE reconciliation_matches (
    statement_line_id push_id NOT NULL,
    entry_id push_id NOT NULL,
    line_no integer NOT NULL,
    how varchar(16) NOT NULL,
    matched_by push_id NOT NULL,
    reason varchar(500),
    matched_at timestamptz NOT NULL,
    CONSTRAINT pk_reconciliation_matches PRIMARY KEY (statement_line_id),
    CONSTRAINT uq_reconciliation_matches_movement UNIQUE (entry_id, line_no),
    CONSTRAINT fk_reconciliation_matches_line
        FOREIGN KEY (statement_line_id) REFERENCES reconciliation_statement_lines (id),
    CONSTRAINT fk_reconciliation_matches_movement
        FOREIGN KEY (entry_id, line_no) REFERENCES reconciliation_movements (entry_id, line_no),
    CONSTRAINT ck_reconciliation_matches_how CHECK (how IN ('reference', 'staff')),
    CONSTRAINT ck_reconciliation_matches_reason
        CHECK (how = 'reference' OR btrim(COALESCE(reason, '')) <> '')
);
CREATE TABLE reconciliation_day_closes (
    id push_id NOT NULL,
    business_date date NOT NULL,
    status varchar(16) NOT NULL,
    prepared_by push_id NOT NULL,
    prepared_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    reason varchar(500),
    created_xact xid8 NOT NULL DEFAULT pg_current_xact_id(),
    CONSTRAINT pk_reconciliation_day_closes PRIMARY KEY (id),
    CONSTRAINT ck_reconciliation_day_closes_status
        CHECK (status IN ('prepared', 'sent_back', 'closed')),
    CONSTRAINT ck_reconciliation_day_closes_decided CHECK (
        (status = 'prepared') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
    ),
    CONSTRAINT ck_reconciliation_day_closes_two_people
        CHECK (status <> 'closed' OR decided_by <> prepared_by),
    CONSTRAINT ck_reconciliation_day_closes_reason
        CHECK (status <> 'sent_back' OR btrim(COALESCE(reason, '')) <> '')
);
CREATE UNIQUE INDEX uq_reconciliation_day_closes_live
    ON reconciliation_day_closes (business_date) WHERE status <> 'sent_back';
CREATE TABLE reconciliation_close_positions (
    close_id push_id NOT NULL,
    source varchar(16) NOT NULL,
    account_reference varchar(64) NOT NULL,
    currency varchar(3) NOT NULL,
    statement_closing_minor bigint NOT NULL,
    ledger_closing_minor bigint NOT NULL,
    open_total_minor bigint NOT NULL,
    open_count integer NOT NULL,
    CONSTRAINT pk_reconciliation_close_positions
        PRIMARY KEY (close_id, source, account_reference),
    CONSTRAINT fk_reconciliation_close_positions_close
        FOREIGN KEY (close_id) REFERENCES reconciliation_day_closes (id),
    CONSTRAINT ck_reconciliation_close_positions_open_count CHECK (open_count >= 0),
    CONSTRAINT ck_reconciliation_close_positions_explained
        CHECK (statement_closing_minor - ledger_closing_minor - open_total_minor = 0)
)
"""

NEVER_FUNCTION = """
CREATE FUNCTION finvest_reconciliation_never_changed() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'reconciliation records are never changed or deleted'
            USING ERRCODE = '42501';
    END;
    $$
"""

MATCH_FUNCTION = """
CREATE FUNCTION finvest_reconciliation_match_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        found_line record;
        found_movement record;
    BEGIN
        SELECT statement.source, statement.account_reference, statement.currency,
               CASE line.direction WHEN 'in' THEN line.amount_minor ELSE -line.amount_minor END
                   AS signed_minor,
               line.reference
          INTO found_line
          FROM public.reconciliation_statement_lines AS line
          JOIN public.reconciliation_statements AS statement ON statement.id = line.statement_id
         WHERE line.id = NEW.statement_line_id;
        SELECT source, account_reference, currency, amount_minor, reference INTO found_movement
          FROM public.reconciliation_movements
         WHERE entry_id = NEW.entry_id AND line_no = NEW.line_no;
        IF found_line IS NULL OR found_movement IS NULL THEN
            RETURN NEW;  -- the foreign keys refuse it
        END IF;
        IF (found_line.source, found_line.account_reference, found_line.currency,
            found_line.signed_minor)
           IS DISTINCT FROM
           (found_movement.source, found_movement.account_reference, found_movement.currency,
            found_movement.amount_minor) THEN
            RAISE EXCEPTION 'a match is one pot, one amount and one direction'
                USING ERRCODE = '23514', CONSTRAINT = 'ck_reconciliation_matches_same_money';
        END IF;
        IF NEW.how = 'reference' AND (found_line.reference = ''
            OR found_line.reference <> found_movement.reference) THEN
            RAISE EXCEPTION 'a match by reference needs the same reference on both sides'
                USING ERRCODE = '23514', CONSTRAINT = 'ck_reconciliation_matches_reference';
        END IF;
        RETURN NEW;
    END;
    $$
"""

CLOSE_FUNCTION = """
CREATE FUNCTION finvest_reconciliation_day_closes_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        last_closed date;
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'a day close is never deleted' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            NEW.created_xact := pg_current_xact_id();
            IF NEW.status <> 'prepared' THEN
                RAISE EXCEPTION 'a day close starts prepared' USING ERRCODE = '42501';
            END IF;
            -- One close at a time, so two days cannot both claim to follow the last one.
            PERFORM pg_advisory_xact_lock(hashtextextended('reconciliation:day-close', 0));
            SELECT max(business_date) INTO last_closed
              FROM public.reconciliation_day_closes WHERE status = 'closed';
            IF last_closed IS NOT NULL AND NEW.business_date <> last_closed + 1 THEN
                RAISE EXCEPTION 'days are closed in order, one after another'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_reconciliation_day_closes_next_day';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'prepared' OR NEW.status NOT IN ('closed', 'sent_back') THEN
            RAISE EXCEPTION 'a prepared close is closed or sent back, once'
                USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.business_date, NEW.prepared_by, NEW.prepared_at, NEW.created_xact)
           IS DISTINCT FROM
           (OLD.id, OLD.business_date, OLD.prepared_by, OLD.prepared_at, OLD.created_xact) THEN
            RAISE EXCEPTION 'what was prepared does not change' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() <> 'staff'
           OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id() THEN
            RAISE EXCEPTION 'a close is decided by the signed-in member of staff'
                USING ERRCODE = '42501';
        END IF;
        -- Whoever imported a statement for the day cannot be the one who closes it, since the
        -- import decides what the close compares against.
        IF NEW.status = 'closed' AND EXISTS (
            SELECT FROM public.reconciliation_statements
             WHERE business_date = NEW.business_date AND imported_by = NEW.decided_by
        ) THEN
            RAISE EXCEPTION 'the person who imported a statement for the day cannot close it'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'closed' AND NOT EXISTS (
            SELECT FROM public.reconciliation_close_positions WHERE close_id = NEW.id
        ) THEN
            RAISE EXCEPTION 'a close without positions proves nothing' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

POSITIONS_FUNCTION = """
CREATE FUNCTION finvest_reconciliation_close_positions_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        -- Positions are written with the close and never added to afterwards.
        IF NOT EXISTS (
            SELECT FROM public.reconciliation_day_closes
             WHERE id = NEW.close_id AND created_xact = pg_current_xact_id()
        ) THEN
            RAISE EXCEPTION 'positions are saved only while their close is being prepared'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

STAFF = "finvest_actor_kind() = 'staff'"
INSERT_ONLY = (
    "reconciliation_movements",
    "reconciliation_matches",
    "reconciliation_close_positions",
)
ALL_TABLES = (*INSERT_ONLY, "reconciliation_day_closes")

RULES = [
    "REVOKE ALL ON FUNCTION finvest_reconciliation_never_changed() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_reconciliation_match_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_reconciliation_day_closes_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION finvest_reconciliation_close_positions_guard() FROM PUBLIC",
    "CREATE TRIGGER reconciliation_matches_guard BEFORE INSERT ON reconciliation_matches "
    "FOR EACH ROW EXECUTE FUNCTION finvest_reconciliation_match_guard()",
    "CREATE TRIGGER reconciliation_day_closes_guard BEFORE INSERT OR UPDATE OR DELETE "
    "ON reconciliation_day_closes FOR EACH ROW "
    "EXECUTE FUNCTION finvest_reconciliation_day_closes_guard()",
    "CREATE TRIGGER reconciliation_close_positions_guard BEFORE INSERT "
    "ON reconciliation_close_positions FOR EACH ROW "
    "EXECUTE FUNCTION finvest_reconciliation_close_positions_guard()",
    "GRANT UPDATE (status, decided_by, decided_at, reason) ON reconciliation_day_closes "
    f"TO {APP_ROLE}",
    f"CREATE POLICY reconciliation_day_closes_app_update ON reconciliation_day_closes "
    f"FOR UPDATE TO {APP_ROLE} USING ({STAFF}) WITH CHECK ({STAFF})",
]
for _table in INSERT_ONLY:
    RULES.append(
        f"CREATE TRIGGER {_table}_never_changed BEFORE UPDATE OR DELETE ON {_table} "
        "FOR EACH ROW EXECUTE FUNCTION finvest_reconciliation_never_changed()"
    )
for _table in ALL_TABLES:
    RULES += [
        f"CREATE TRIGGER {_table}_no_truncate BEFORE TRUNCATE ON {_table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION finvest_reconciliation_never_changed()",
        f"ALTER TABLE {_table} ENABLE ROW LEVEL SECURITY",
        f"REVOKE ALL ON {_table} FROM PUBLIC",
        f"GRANT SELECT, INSERT ON {_table} TO {APP_ROLE}",
        f"GRANT SELECT ON {_table} TO {WORKER_ROLE}",
        f"CREATE POLICY {_table}_app_read ON {_table} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
        f"CREATE POLICY {_table}_worker_read ON {_table} FOR SELECT TO {WORKER_ROLE} USING (true)",
    ]
RULES += [
    f"CREATE POLICY reconciliation_movements_app_insert ON reconciliation_movements "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF})",
    f"CREATE POLICY reconciliation_matches_app_insert ON reconciliation_matches "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF} AND matched_by = finvest_actor_id())",
    f"CREATE POLICY reconciliation_day_closes_app_insert ON reconciliation_day_closes "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF} AND prepared_by = finvest_actor_id())",
    f"CREATE POLICY reconciliation_close_positions_app_insert ON reconciliation_close_positions "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF})",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    functions = [NEVER_FUNCTION, MATCH_FUNCTION, CLOSE_FUNCTION, POSITIONS_FUNCTION]
    for statement in [*split(TABLES), *functions, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM reconciliation_day_closes)
       OR EXISTS (SELECT FROM reconciliation_matches)
       OR EXISTS (SELECT FROM reconciliation_movements) THEN
        RAISE EXCEPTION 'refusing to drop reconciliation records';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP TABLE reconciliation_close_positions")
    op.execute("DROP TABLE reconciliation_day_closes")
    op.execute("DROP TABLE reconciliation_matches")
    op.execute("DROP TABLE reconciliation_movements")
    for name in ("close_positions_guard", "day_closes_guard", "match_guard", "never_changed"):
        op.execute(f"DROP FUNCTION finvest_reconciliation_{name}()")
