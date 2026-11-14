"""Settle many bank payouts from one bulk debit line on the trust statement.

Revision ID: 0280_payout_bank_bulk_debits
Revises: 0222_identity_recovery_requests

When a batch's transfer file is uploaded in the bank's portal, the bank often takes the money in
one debit for the whole file rather than one line per payout. Migration 0039 settles a paid bank
payout only from a line for its own amount, so such a batch could not be settled. This adds a
bulk settlement, two people as every money step:

* One member of staff proposes it: one trust statement line going out, in the batch's currency,
  on or after the day the batch was approved, and the sent payouts of that one batch that it
  paid. In the same transaction a paid settlement is written for each payout, naming the bulk
  and the same line. At commit their amounts must add up to the line exactly.
* A different one approves it. In the same transaction every payout settlement in it is
  approved by the same person and every payout becomes paid, through the payouts guard 0039
  already has. They may instead decline it, or the proposer may withdraw it; the payout
  settlements follow the bulk, and are never decided on their own.
* A statement line is named by one live settlement at a time: one bulk, or one single
  settlement, never both. Both take the same lock on the line before they look, so two people
  cannot each claim it at once.
* A bulk's payouts are named only in the transaction that proposes it, and never change.
  Nothing is deleted, and a decided bulk never changes. Only staff see them.

A payout in an approved bulk is accounted for by its line, so the "failed" check of 0039 does
not see that line as a payout that may have left.
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0280_payout_bank_bulk_debits"
down_revision: str | None = "0222_identity_recovery_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
U = "payout_bank_bulk_settlements"
STAFF = "public.finvest_actor_kind() = 'staff'"

TABLES = """
CREATE TABLE payout_bank_bulk_settlements (
    id push_id NOT NULL,
    batch_id push_id NOT NULL,
    statement_line_id push_id NOT NULL,
    reason varchar(500) NOT NULL,
    status varchar(16) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    CONSTRAINT pk_payout_bank_bulk_settlements PRIMARY KEY (id),
    CONSTRAINT fk_payout_bank_bulk_settlements_batch
        FOREIGN KEY (batch_id) REFERENCES payout_bank_batches (id),
    CONSTRAINT fk_payout_bank_bulk_settlements_line
        FOREIGN KEY (statement_line_id) REFERENCES reconciliation_statement_lines (id),
    CONSTRAINT ck_payout_bank_bulk_settlements_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_payout_bank_bulk_settlements_status
        CHECK (status IN ('proposed', 'approved', 'declined', 'withdrawn')),
    CONSTRAINT ck_payout_bank_bulk_settlements_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
    ),
    CONSTRAINT ck_payout_bank_bulk_settlements_two_people CHECK (
        status NOT IN ('approved', 'declined') OR decided_by <> proposed_by
    ),
    CONSTRAINT ck_payout_bank_bulk_settlements_withdrawn_by_proposer
        CHECK (status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_payout_bank_bulk_settlements_decision_reason
        CHECK (status = 'proposed' OR btrim(COALESCE(decision_reason, '')) <> '')
);
CREATE UNIQUE INDEX uq_payout_bank_bulk_settlements_line
    ON payout_bank_bulk_settlements (statement_line_id) WHERE status IN ('proposed', 'approved');
CREATE INDEX ix_payout_bank_bulk_settlements_status
    ON payout_bank_bulk_settlements (status, proposed_at);
ALTER TABLE payout_bank_settlements ADD COLUMN bulk_id push_id;
ALTER TABLE payout_bank_settlements ADD CONSTRAINT fk_payout_bank_settlements_bulk
    FOREIGN KEY (bulk_id) REFERENCES payout_bank_bulk_settlements (id);
ALTER TABLE payout_bank_settlements ADD CONSTRAINT ck_payout_bank_settlements_bulk_paid
    CHECK (bulk_id IS NULL OR outcome = 'paid');
CREATE INDEX ix_payout_bank_settlements_bulk ON payout_bank_settlements (bulk_id)
    WHERE bulk_id IS NOT NULL;
DROP INDEX uq_payout_bank_settlements_line;
CREATE UNIQUE INDEX uq_payout_bank_settlements_line ON payout_bank_settlements (statement_line_id)
    WHERE status IN ('proposed', 'approved') AND bulk_id IS NULL
"""

# The lock both kinds of settlement take on a statement line before looking for another claim.
LINE_LOCK = "pg_advisory_xact_lock(hashtext('payout_bank_lines'), hashtext(NEW.statement_line_id))"

# Whether a statement line can be the bulk debit of a batch: the trust account, going out, in the
# batch's currency, on or after the day the batch was approved.
LINE_FITS_FUNCTION = """
CREATE FUNCTION public.finvest_payout_bank_bulk_line_fits(
    bulk public.payout_bank_bulk_settlements
) RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        batch public.payout_bank_batches;
    BEGIN
        SELECT * INTO batch FROM public.payout_bank_batches
         WHERE id = bulk.batch_id AND status = 'approved';
        IF NOT FOUND THEN
            RETURN false;
        END IF;
        RETURN EXISTS (
            SELECT FROM public.reconciliation_statement_lines AS line
              JOIN public.reconciliation_statements AS statement
                ON statement.id = line.statement_id
             WHERE line.id = bulk.statement_line_id AND statement.source = 'bank'
               AND statement.account_reference = 'trust'
               AND statement.currency = batch.currency AND line.direction = 'out'
               AND line.value_date >= (batch.decided_at AT TIME ZONE 'Africa/Kampala')::date
        );
    END;
    $$
"""

BULK_GUARD = """
CREATE FUNCTION public.finvest_payout_bank_bulk_settlements_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'a bulk settlement is never deleted' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff settle a bank payout' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by IS DISTINCT FROM public.finvest_actor_id()
               OR abs(extract(epoch FROM NEW.proposed_at - clock_timestamp())) > 300 THEN
                RAISE EXCEPTION 'a bulk settlement starts proposed, by the person signed in, now'
                    USING ERRCODE = '42501';
            END IF;
            PERFORM {lock};
            IF EXISTS (
                SELECT FROM public.payout_bank_settlements
                 WHERE statement_line_id = NEW.statement_line_id AND bulk_id IS NULL
                   AND status IN ('proposed', 'approved')
            ) THEN
                RAISE EXCEPTION 'another settlement names this statement line'
                    USING ERRCODE = '23505';
            END IF;
            IF NOT public.finvest_payout_bank_bulk_line_fits(NEW) THEN
                RAISE EXCEPTION 'the line is not money out of the trust account after this batch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' OR NEW.status = 'proposed' THEN
            RAISE EXCEPTION 'a proposed bulk settlement is decided once' USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.batch_id, NEW.statement_line_id, NEW.reason, NEW.proposed_by,
            NEW.proposed_at)
           IS DISTINCT FROM
           (OLD.id, OLD.batch_id, OLD.statement_line_id, OLD.reason, OLD.proposed_by,
            OLD.proposed_at) THEN
            RAISE EXCEPTION 'what was proposed does not change' USING ERRCODE = '42501';
        END IF;
        IF NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
           OR abs(extract(epoch FROM NEW.decided_at - clock_timestamp())) > 300 THEN
            RAISE EXCEPTION 'a bulk settlement is decided by the person signed in, now'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'approved' AND NOT public.finvest_payout_bank_bulk_line_fits(NEW) THEN
            RAISE EXCEPTION 'the statement no longer shows this line for this batch'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# At commit: the payouts in a bulk add up to its line exactly, and were decided with it.
ADDS_UP_FUNCTION = """
CREATE FUNCTION public.finvest_payout_bank_bulk_settlements_adds_up() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        bulk public.payout_bank_bulk_settlements;
        line_amount bigint;
        item_count integer;
        total bigint;
    BEGIN
        -- The row as it now stands, not as the event that queued this check saw it.
        SELECT * INTO bulk FROM public.payout_bank_bulk_settlements WHERE id = NEW.id;
        SELECT amount_minor INTO line_amount FROM public.reconciliation_statement_lines
         WHERE id = bulk.statement_line_id;
        SELECT count(*), COALESCE(sum(payout.amount_minor), 0) INTO item_count, total
          FROM public.payout_bank_settlements AS settlement
          JOIN public.payouts AS payout ON payout.id = settlement.payout_id
         WHERE settlement.bulk_id = bulk.id;
        IF item_count = 0 OR total IS DISTINCT FROM line_amount THEN
            RAISE EXCEPTION 'the payouts in a bulk settlement add up to its statement line'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT FROM public.payout_bank_settlements
             WHERE bulk_id = bulk.id
               AND (status <> bulk.status OR decided_by IS DISTINCT FROM bulk.decided_by)
        ) THEN
            RAISE EXCEPTION 'the payouts in a bulk settlement are decided with it'
                USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END;
    $$
"""

# 0039's evidence, with one more case: a payout in a bulk is shown by its bulk's line, which
# is for the whole bulk, not its own amount. The sum is checked at commit.
EVIDENCE_FUNCTION = """
CREATE OR REPLACE FUNCTION public.finvest_payout_bank_settlement_evidence(
    settlement public.payout_bank_settlements
) RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        payout public.payouts;
        approved_on date;
        cited public.reconciliation_statements;
    BEGIN
        SELECT * INTO payout FROM public.payouts WHERE id = settlement.payout_id;
        IF NOT FOUND OR payout.kind <> 'bank' OR payout.status <> 'sent' THEN
            RETURN false;
        END IF;
        SELECT (decided_at AT TIME ZONE 'Africa/Kampala')::date INTO approved_on
          FROM public.payout_bank_batches
         WHERE id = settlement.batch_id AND status = 'approved';
        IF approved_on IS NULL THEN
            RETURN false;
        END IF;
        IF settlement.outcome = 'paid' AND settlement.bulk_id IS NOT NULL THEN
            RETURN EXISTS (
                SELECT FROM public.payout_bank_bulk_settlements AS bulk
                 WHERE bulk.id = settlement.bulk_id AND bulk.batch_id = settlement.batch_id
                   AND bulk.statement_line_id = settlement.statement_line_id
                   AND public.finvest_payout_bank_bulk_line_fits(bulk)
            );
        END IF;
        IF settlement.outcome = 'paid' THEN
            RETURN EXISTS (
                SELECT FROM public.reconciliation_statement_lines AS line
                  JOIN public.reconciliation_statements AS statement
                    ON statement.id = line.statement_id
                 WHERE line.id = settlement.statement_line_id AND statement.source = 'bank'
                   AND statement.account_reference = 'trust'
                   AND statement.currency = payout.currency AND line.direction = 'out'
                   AND line.amount_minor = payout.amount_minor
                   AND line.value_date >= approved_on
            );
        END IF;
        SELECT * INTO cited FROM public.reconciliation_statements
         WHERE id = settlement.statement_id AND source = 'bank' AND account_reference = 'trust'
           AND currency = payout.currency AND business_date >= approved_on + 2;
        IF NOT FOUND THEN
            RETURN false;
        END IF;
        -- Every day from the approval to the cited one is imported, and no line on them going
        -- out for this amount is unaccounted for, so a payout that left cannot be parked.
        RETURN (
                SELECT count(DISTINCT business_date) FROM public.reconciliation_statements
                 WHERE source = 'bank' AND account_reference = 'trust'
                   AND currency = payout.currency
                   AND business_date BETWEEN approved_on AND cited.business_date
            ) = cited.business_date - approved_on + 1
            AND NOT EXISTS (
                SELECT FROM public.reconciliation_statement_lines AS line
                  JOIN public.reconciliation_statements AS statement
                    ON statement.id = line.statement_id
                 WHERE statement.source = 'bank' AND statement.account_reference = 'trust'
                   AND statement.currency = payout.currency
                   AND statement.business_date BETWEEN approved_on AND cited.business_date
                   AND line.direction = 'out' AND line.amount_minor = payout.amount_minor
                   AND NOT EXISTS (
                       SELECT FROM public.payout_bank_settlements AS other
                        WHERE other.statement_line_id = line.id
                          AND other.status = 'approved'
                   )
            );
    END;
    $$
"""

# 0039's guard, with a settlement in a bulk written only as its bulk is proposed and decided
# only with it, and a single settlement refused a line a live bulk names.
SETTLEMENTS_GUARD = """
CREATE OR REPLACE FUNCTION public.finvest_payout_bank_settlements_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        bulk public.payout_bank_bulk_settlements;
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'a bank payout settlement is never deleted' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff settle a bank payout' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by IS DISTINCT FROM public.finvest_actor_id()
               OR abs(extract(epoch FROM NEW.proposed_at - clock_timestamp())) > 300 THEN
                RAISE EXCEPTION 'a settlement starts proposed, by the person signed in, now'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.bulk_id IS NOT NULL THEN
                SELECT * INTO bulk FROM public.payout_bank_bulk_settlements WHERE id = NEW.bulk_id;
                -- Only in the transaction that proposed the bulk, so an approver approves what
                -- they saw.
                IF NOT FOUND OR bulk.status <> 'proposed'
                   OR bulk.proposed_by IS DISTINCT FROM NEW.proposed_by
                   OR (SELECT xmin FROM public.payout_bank_bulk_settlements
                        WHERE id = NEW.bulk_id)::text
                      IS DISTINCT FROM (pg_current_xact_id()::xid)::text
                   OR NEW.batch_id IS DISTINCT FROM bulk.batch_id
                   OR NEW.statement_line_id IS DISTINCT FROM bulk.statement_line_id THEN
                    RAISE EXCEPTION 'the payouts in a bulk are named only as it is proposed'
                        USING ERRCODE = '42501';
                END IF;
            ELSIF NEW.statement_line_id IS NOT NULL THEN
                PERFORM {lock};
                IF EXISTS (
                    SELECT FROM public.payout_bank_bulk_settlements
                     WHERE statement_line_id = NEW.statement_line_id
                       AND status IN ('proposed', 'approved')
                ) THEN
                    RAISE EXCEPTION 'a bulk settlement names this statement line'
                        USING ERRCODE = '23505';
                END IF;
            END IF;
            IF NOT public.finvest_payout_bank_settlement_evidence(NEW) THEN
                RAISE EXCEPTION 'the statement does not show this outcome for this payout'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' OR NEW.status = 'proposed' THEN
            RAISE EXCEPTION 'a proposed settlement is decided once' USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.payout_id, NEW.batch_id, NEW.outcome, NEW.statement_line_id,
            NEW.statement_id, NEW.reason, NEW.proposed_by, NEW.proposed_at, NEW.bulk_id)
           IS DISTINCT FROM
           (OLD.id, OLD.payout_id, OLD.batch_id, OLD.outcome, OLD.statement_line_id,
            OLD.statement_id, OLD.reason, OLD.proposed_by, OLD.proposed_at, OLD.bulk_id) THEN
            RAISE EXCEPTION 'what was proposed does not change' USING ERRCODE = '42501';
        END IF;
        IF NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
           OR abs(extract(epoch FROM NEW.decided_at - clock_timestamp())) > 300 THEN
            RAISE EXCEPTION 'a settlement is decided by the person signed in, now'
                USING ERRCODE = '42501';
        END IF;
        -- A payout in a bulk is decided with its bulk, which is decided first.
        IF NEW.bulk_id IS NOT NULL AND NOT EXISTS (
            SELECT FROM public.payout_bank_bulk_settlements
             WHERE id = NEW.bulk_id AND status = NEW.status AND decided_by = NEW.decided_by
        ) THEN
            RAISE EXCEPTION 'a payout in a bulk settlement is decided with it'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'approved' AND NOT public.finvest_payout_bank_settlement_evidence(NEW)
        THEN
            RAISE EXCEPTION 'the statement no longer shows this outcome for this payout'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
"""

BULK_GUARD = BULK_GUARD.replace("{lock}", LINE_LOCK)
SETTLEMENTS_GUARD = SETTLEMENTS_GUARD.replace("{lock}", LINE_LOCK)

FUNCTIONS = (
    "finvest_payout_bank_bulk_line_fits(public.payout_bank_bulk_settlements)",
    "finvest_payout_bank_bulk_settlements_guard()",
    "finvest_payout_bank_bulk_settlements_adds_up()",
)

RULES = [
    *(f"REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC" for function in FUNCTIONS),
    # The routes ask it first, so a refusal can be named. It answers yes or no.
    "GRANT EXECUTE ON FUNCTION public.finvest_payout_bank_bulk_line_fits"
    f"(public.payout_bank_bulk_settlements) TO {APP_ROLE}",
    f"CREATE TRIGGER {U}_guard BEFORE INSERT OR UPDATE OR DELETE ON {U} "
    "FOR EACH ROW EXECUTE FUNCTION public.finvest_payout_bank_bulk_settlements_guard()",
    f"CREATE TRIGGER {U}_no_truncate BEFORE TRUNCATE ON {U} "
    "FOR EACH STATEMENT EXECUTE FUNCTION public.finvest_payout_bank_bulk_settlements_guard()",
    f"CREATE CONSTRAINT TRIGGER {U}_adds_up AFTER INSERT OR UPDATE ON {U} "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
    "EXECUTE FUNCTION public.finvest_payout_bank_bulk_settlements_adds_up()",
    f"ALTER TABLE {U} ENABLE ROW LEVEL SECURITY",
    f"REVOKE ALL ON {U} FROM PUBLIC",
    f"GRANT SELECT ON {U} TO {WORKER_ROLE}",
    f"CREATE POLICY {U}_app_read ON {U} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY {U}_worker_read ON {U} FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"GRANT SELECT, INSERT ON {U} TO {APP_ROLE}",
    f"GRANT UPDATE (status, decided_by, decided_at, decision_reason) ON {U} TO {APP_ROLE}",
    f"CREATE POLICY {U}_app_insert ON {U} FOR INSERT TO {APP_ROLE} "
    f"WITH CHECK ({STAFF} AND proposed_by = public.finvest_actor_id())",
    f"CREATE POLICY {U}_app_update ON {U} FOR UPDATE TO {APP_ROLE} "
    f"USING ({STAFF}) WITH CHECK ({STAFF})",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [
        *split(TABLES),
        LINE_FITS_FUNCTION,
        BULK_GUARD,
        ADDS_UP_FUNCTION,
        EVIDENCE_FUNCTION,
        SETTLEMENTS_GUARD,
        *RULES,
    ]:
        op.execute(statement)


EMPTY_GUARD = f"""
DO $$
BEGIN
    IF EXISTS (SELECT FROM {U}) THEN
        RAISE EXCEPTION 'refusing to drop the bulk settlements of bank payouts';
    END IF;
END
$$
"""


def _from_0039(name: str) -> str:
    path = Path(__file__).with_name("0039_payout_bank_batches.py")
    spec = importlib.util.spec_from_file_location("finvest_0039_payout_bank_batches", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(getattr(module, name)).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(_from_0039("SETTLEMENTS_GUARD"))
    op.execute(_from_0039("EVIDENCE_FUNCTION"))
    op.execute("DROP INDEX uq_payout_bank_settlements_line")
    op.execute(
        "CREATE UNIQUE INDEX uq_payout_bank_settlements_line ON payout_bank_settlements "
        "(statement_line_id) WHERE status IN ('proposed', 'approved')"
    )
    op.execute("ALTER TABLE payout_bank_settlements DROP COLUMN bulk_id")
    # The line check takes a bulk row, so it goes before its table.
    op.execute(f"DROP FUNCTION public.{FUNCTIONS[0]}")
    op.execute(f"DROP TABLE {U}")
    for function in FUNCTIONS[1:]:
        op.execute(f"DROP FUNCTION public.{function}")
