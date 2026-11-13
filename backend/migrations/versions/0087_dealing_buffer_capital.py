"""Add Finvest's own money in the liquidity buffer, paid in or taken out by two people.

Revision ID: 0087_dealing_buffer_capital
Revises: 0036_payout_guard_search_path

The owner decided on 2026-11-13 that the buffer's cash is Finvest's own money. It sits in the
trust account and is booked to the equity account `dealing.buffer_capital`.

* One member of staff proposes a movement, with a reason, and a different signed-in member of
  staff approves or declines it, once. What was proposed never changes.
* Paying in names the bank's reference and the day it arrived. Taking out names neither: it is
  dated when approved and quotes its own ID on the transfer.
* An approved movement names exactly the ledger entry it posted: from this movement, on its
  value date, with the trust account and `dealing.buffer_capital` moving together by its
  amount. The ledger already refuses to take `dealing.buffer_capital` below zero, so no more
  can come out than went in.
* Nothing is ever deleted. The approval posts in the second person's staff transaction, so
  `dealing.buffer_capital` joins the ledger's list of source types staff may post.

See docs/dealing-desk.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0087_dealing_buffer_capital"
down_revision: str | None = "0036_payout_guard_search_path"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
T = "dealing_buffer_capital"

TABLE = """
CREATE TABLE dealing_buffer_capital (
    id push_id NOT NULL,
    direction varchar(3) NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    amount_minor bigint NOT NULL,
    bank_reference varchar(100),
    value_date date,
    reason varchar(500) NOT NULL,
    status varchar(8) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    ledger_entry_id push_id,
    CONSTRAINT pk_dealing_buffer_capital PRIMARY KEY (id),
    CONSTRAINT fk_dealing_buffer_capital_entry
        FOREIGN KEY (ledger_entry_id) REFERENCES ledger_journal_entries (id),
    CONSTRAINT ck_dealing_buffer_capital_direction CHECK (direction IN ('in', 'out')),
    CONSTRAINT ck_dealing_buffer_capital_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_dealing_buffer_capital_amount CHECK (amount_minor > 0),
    CONSTRAINT ck_dealing_buffer_capital_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_dealing_buffer_capital_paid_in CHECK (
        direction <> 'in'
        OR (btrim(coalesce(bank_reference, '')) <> '' AND value_date IS NOT NULL)),
    CONSTRAINT ck_dealing_buffer_capital_taken_out CHECK (
        direction <> 'out'
        OR (bank_reference IS NULL AND (value_date IS NOT NULL) = (status = 'approved'))),
    CONSTRAINT ck_dealing_buffer_capital_status
        CHECK (status IN ('proposed', 'approved', 'declined')),
    CONSTRAINT ck_dealing_buffer_capital_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (status = 'approved') = (ledger_entry_id IS NOT NULL)),
    CONSTRAINT ck_dealing_buffer_capital_two_people
        CHECK (decided_by IS NULL OR decided_by <> proposed_by),
    CONSTRAINT ck_dealing_buffer_capital_decline_reason
        CHECK (status <> 'declined' OR btrim(coalesce(decision_reason, '')) <> '')
);
CREATE UNIQUE INDEX uq_dealing_buffer_capital_bank_reference
    ON dealing_buffer_capital (currency, bank_reference)
    WHERE direction = 'in' AND status <> 'declined';
CREATE UNIQUE INDEX uq_dealing_buffer_capital_entry
    ON dealing_buffer_capital (ledger_entry_id) WHERE ledger_entry_id IS NOT NULL;
CREATE INDEX ix_dealing_buffer_capital_proposed
    ON dealing_buffer_capital (proposed_at, id) WHERE status = 'proposed'
"""

GUARD = """
CREATE FUNCTION finvest_dealing_buffer_capital_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        bank bigint;
        capital bigint;
        sign bigint;
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'a buffer capital movement is never removed' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'buffer capital is moved by staff' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status IS DISTINCT FROM 'proposed'
                OR NEW.proposed_by IS DISTINCT FROM public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a movement starts proposed by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status IS DISTINCT FROM 'proposed'
            OR NEW.status NOT IN ('approved', 'declined') THEN
            RAISE EXCEPTION 'a proposed movement is approved or declined, once'
                USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.direction, NEW.currency, NEW.amount_minor, NEW.bank_reference,
            NEW.reason, NEW.proposed_by, NEW.proposed_at)
           IS DISTINCT FROM
           (OLD.id, OLD.direction, OLD.currency, OLD.amount_minor, OLD.bank_reference,
            OLD.reason, OLD.proposed_by, OLD.proposed_at)
           OR (NEW.direction = 'in' AND NEW.value_date IS DISTINCT FROM OLD.value_date) THEN
            RAISE EXCEPTION 'what was proposed does not change' USING ERRCODE = '42501';
        END IF;
        IF NEW.decided_by IS DISTINCT FROM public.finvest_actor_id() THEN
            RAISE EXCEPTION 'a movement is decided by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'declined' THEN
            RETURN NEW;
        END IF;
        IF NOT EXISTS (
            SELECT FROM public.ledger_journal_entries
            WHERE id = NEW.ledger_entry_id AND source_type = 'dealing.buffer_capital'
                AND source_id = NEW.id AND currency = NEW.currency
                AND effective_date = NEW.value_date AND reverses_entry_id IS NULL
        ) THEN
            RAISE EXCEPTION 'an approved movement names the entry it posted'
                USING ERRCODE = '42501';
        END IF;
        SELECT
            coalesce(sum(line.amount_minor) FILTER (WHERE account.code = 'bank.trust'), 0),
            coalesce(sum(line.amount_minor)
                FILTER (WHERE account.code = 'dealing.buffer_capital'), 0)
            INTO bank, capital
            FROM public.ledger_lines AS line
            JOIN public.ledger_accounts AS account ON account.id = line.account_id
            WHERE line.entry_id = NEW.ledger_entry_id
                AND account.owner_client_id IS NULL AND account.reference IS NULL;
        sign := CASE WHEN NEW.direction = 'in' THEN 1 ELSE -1 END;
        IF bank IS DISTINCT FROM sign * NEW.amount_minor
            OR capital IS DISTINCT FROM -sign * NEW.amount_minor THEN
            RAISE EXCEPTION 'the entry moves the trust account and Finvest''s money by the amount'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

STAFF = "public.finvest_actor_kind() = 'staff'"

RULES = [
    "REVOKE ALL ON FUNCTION finvest_dealing_buffer_capital_guard() FROM PUBLIC",
    f"CREATE TRIGGER {T}_guard BEFORE INSERT OR UPDATE OR DELETE ON {T} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_dealing_buffer_capital_guard()",
    f"CREATE TRIGGER {T}_no_truncate BEFORE TRUNCATE ON {T} "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_dealing_buffer_capital_guard()",
    f"REVOKE ALL ON {T} FROM PUBLIC",
    f"GRANT SELECT, INSERT ON {T} TO {APP_ROLE}",
    f"GRANT UPDATE (status, decided_by, decided_at, decision_reason, value_date, "
    f"ledger_entry_id) ON {T} TO {APP_ROLE}",
    f"GRANT SELECT ON {T} TO {WORKER_ROLE}",
    f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY {T}_app_read ON {T} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY {T}_worker_read ON {T} FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY {T}_app_insert ON {T} FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF})",
    f"CREATE POLICY {T}_app_update ON {T} FOR UPDATE TO {APP_ROLE} "
    f"USING ({STAFF}) WITH CHECK ({STAFF})",
    "INSERT INTO ledger_staff_source_types (source_type, purpose) VALUES "
    "('dealing.buffer_capital', 'dealing: Finvest''s own money paid into or taken out of the "
    "buffer, approved by a second staff member')",
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
    IF EXISTS (SELECT FROM dealing_buffer_capital) THEN
        RAISE EXCEPTION 'refusing to drop buffer capital movements';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DELETE FROM ledger_staff_source_types WHERE source_type = 'dealing.buffer_capital'")
    op.execute(f"DROP TABLE {T}")
    op.execute("DROP FUNCTION finvest_dealing_buffer_capital_guard()")
