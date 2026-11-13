"""Undo an investment started by a mistaken deposit, and refund money that never started one.

Revision ID: 0066_investment_corrections
Revises: 0094_email_keyed_backfill

Both are corrections staff make, so both take two people: one proposes, a different one
approves, and the worker carries out what was approved. `investment_corrections` keeps them.
The database refuses a decision by the proposer, a second open correction of the same kind for
one investment, and any change after a decision except the worker recording the outcome.

Adds the `unwound` status: an investment undone because the deposit that started it was a
mistake. The guard of migration 0065 is replaced so that only the worker undoes an investment,
only while it runs, and only with an approved correction. See docs/investing.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0066_investment_corrections"
down_revision: str | None = "0094_email_keyed_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
STAFF = "public.finvest_actor_kind() = 'staff'"

TABLE = """
CREATE TABLE investment_corrections (
    id push_id NOT NULL,
    -- `unwind` moves a running investment's principal back to money awaiting start and
    -- reverses its interest. `refund` pays money awaiting start back to the client.
    kind varchar(8) NOT NULL,
    investment_id push_id NOT NULL,
    -- The client's ID in the identity module. No foreign key: a module owns only its own tables.
    client_id push_id NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    -- What the proposer saw. The worker does nothing unless it is still exactly this.
    amount_minor bigint NOT NULL,
    -- A refund's payout account, in the payouts module.
    destination_id push_id,
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    -- Staff IDs in the identity module.
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_note varchar(500),
    done_at timestamptz,
    -- The ledger entry that moved the money, and a refund's payout.
    entry_id push_id,
    payout_id push_id,
    failure_code varchar(64),
    CONSTRAINT pk_investment_corrections PRIMARY KEY (id),
    CONSTRAINT fk_investment_corrections_investment
        FOREIGN KEY (investment_id) REFERENCES investments (id),
    CONSTRAINT ck_investment_corrections_kind CHECK (kind IN ('unwind', 'refund')),
    CONSTRAINT ck_investment_corrections_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_investment_corrections_amount CHECK (amount_minor > 0),
    CONSTRAINT ck_investment_corrections_destination
        CHECK ((kind = 'refund') = (destination_id IS NOT NULL)),
    CONSTRAINT ck_investment_corrections_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_investment_corrections_status CHECK (
        status IN ('proposed', 'approved', 'sent_back', 'withdrawn', 'done', 'failed')),
    CONSTRAINT ck_investment_corrections_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL) AND (status = 'proposed') = (decided_at IS NULL)),
    -- Nobody decides their own proposal, and only the proposer withdraws it.
    CONSTRAINT ck_investment_corrections_second_person CHECK (
        status IN ('proposed', 'withdrawn') OR decided_by <> proposed_by),
    CONSTRAINT ck_investment_corrections_withdrawn CHECK (
        status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_investment_corrections_note CHECK (
        status <> 'sent_back' OR btrim(coalesce(decision_note, '')) <> ''),
    -- Done means the money moved, and a refund has its payout. Failed means it did not.
    CONSTRAINT ck_investment_corrections_outcome CHECK (
        (status IN ('done', 'failed')) = (done_at IS NOT NULL)
        AND (status = 'done') = (entry_id IS NOT NULL)
        AND (status = 'failed') = (failure_code IS NOT NULL)
        AND (payout_id IS NULL OR (status = 'done' AND kind = 'refund'))
        AND (status <> 'done' OR kind <> 'refund' OR payout_id IS NOT NULL))
)
"""
INDEXES = [
    # One open correction of each kind per investment.
    "CREATE UNIQUE INDEX uq_investment_corrections_open ON investment_corrections "
    "(investment_id, kind) WHERE status IN ('proposed', 'approved')",
    "CREATE INDEX ix_investment_corrections_status ON investment_corrections (status, proposed_at)",
]

# Staff propose and decide, as the person signed in. The worker only records what happened to
# an approved correction. Nothing else ever changes, and nothing is deleted.
CORRECTIONS_GUARD = f"""
CREATE FUNCTION finvest_investment_corrections_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        is_worker boolean := finvest_actor_kind() = 'system' AND current_user = '{WORKER_ROLE}';
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF finvest_actor_kind() IS DISTINCT FROM 'staff' OR NEW.status <> 'proposed'
                OR NEW.proposed_by <> finvest_actor_id() OR NEW.done_at IS NOT NULL
                OR NEW.entry_id IS NOT NULL OR NEW.payout_id IS NOT NULL
                OR NEW.failure_code IS NOT NULL THEN
                RAISE EXCEPTION 'a correction starts as proposed, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status = 'proposed' THEN
            IF finvest_actor_kind() IS DISTINCT FROM 'staff' OR NEW.status = 'proposed'
                OR NEW.status NOT IN ('approved', 'sent_back', 'withdrawn')
                OR NEW.decided_by IS DISTINCT FROM finvest_actor_id()
                OR (to_jsonb(NEW) - '{{status,decided_by,decided_at,decision_note}}'::text[])
                    <> (to_jsonb(OLD) - '{{status,decided_by,decided_at,decision_note}}'::text[])
            THEN
                RAISE EXCEPTION 'a correction is only decided, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status = 'approved' THEN
            IF NOT is_worker OR NEW.status NOT IN ('done', 'failed')
                OR (to_jsonb(NEW) - '{{status,done_at,entry_id,payout_id,failure_code}}'::text[])
                    <> (to_jsonb(OLD) - '{{status,done_at,entry_id,payout_id,failure_code}}'::text[])
            THEN
                RAISE EXCEPTION 'only the worker records what an approved correction did'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'a finished correction never changes' USING ERRCODE = '42501';
    END;
    $$
"""

COLUMNS = [
    "ALTER TABLE investments ADD COLUMN unwound_at timestamptz",
    # The ledger entry that moved the principal back and reversed the interest.
    "ALTER TABLE investments ADD COLUMN unwind_entry_id push_id",
    "ALTER TABLE investments DROP CONSTRAINT ck_investments_status",
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_status CHECK (status IN "
    "('awaiting_payment', 'active', 'cancelled', 'matured', 'withdrawn', 'unwound'))",
    "ALTER TABLE investments DROP CONSTRAINT ck_investments_started",
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_started CHECK ("
    "(status IN ('active', 'matured', 'withdrawn', 'unwound')) = (start_date IS NOT NULL) "
    "AND (start_date IS NULL) = (started_at IS NULL) "
    "AND (start_date IS NULL) = (start_entry_id IS NULL))",
    "ALTER TABLE investments ADD CONSTRAINT ck_investments_unwound CHECK ("
    "(status = 'unwound') = (unwound_at IS NOT NULL) "
    "AND (status = 'unwound') = (unwind_entry_id IS NOT NULL))",
]

_FREE = (
    "status,at_maturity,start_date,maturity_date,started_at,start_entry_id,cancelled_at,"
    "accrued_through,accrued_gross_minor,accrued_tax_minor,updated_at,payout_destination_id,"
    "matured_at,maturity_entry_id,rolled_into_id,payout_id,payout_refusal,"
    "withdrawal_requested_at,withdrawal_destination_id,withdrawn_at,withdrawal_entry_id,"
    "withdrawal_payout_id,unwound_at,unwind_entry_id"
)

# The whole guard again, as PostgreSQL replaces a function in one piece. What is new: a running
# investment may be undone by the worker when an approved correction says so, and an undone
# one never changes.
GUARD = f"""
CREATE OR REPLACE FUNCTION finvest_investments_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        actor text := finvest_actor_kind();
        is_worker boolean := finvest_actor_kind() = 'system' AND current_user = '{WORKER_ROLE}';
        product investment_products%ROWTYPE;
        rate investment_rates%ROWTYPE;
        day date;
    BEGIN
        IF TG_OP = 'INSERT' AND NEW.legacy_reference IS NOT NULL THEN
            -- Carried over from Finvest 1.0 at the switch, by the worker only: already running,
            -- on the old system's own rate and tax, with the interest earned up to a day
            -- before today and before it matures. Nothing else of a new investment applies.
            SELECT * INTO product FROM investment_products WHERE id = NEW.product_id;
            day := (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date;
            IF NOT is_worker OR NEW.status <> 'active'
                OR product.id IS NULL OR product.status = 'draft'
                OR NEW.currency <> product.currency
                OR NEW.term_days IS DISTINCT FROM product.term_days
                OR NEW.rolled_from_id IS NOT NULL OR NEW.source_id IS NOT NULL
                OR NEW.payout_destination_id IS NOT NULL
                OR NEW.withdrawal_requested_at IS NOT NULL
                OR NEW.accrued_through IS NULL OR NEW.accrued_through >= day
                OR NEW.start_date > NEW.accrued_through
                OR (NEW.maturity_date IS NOT NULL AND NEW.accrued_through >= NEW.maturity_date)
            THEN
                RAISE EXCEPTION 'only the switch carries over a running investment'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'awaiting_payment' OR NEW.accrued_gross_minor <> 0
                OR NEW.accrued_tax_minor <> 0 OR NEW.accrued_through IS NOT NULL
                OR NEW.payout_destination_id IS NOT NULL THEN
                RAISE EXCEPTION 'an investment begins unpaid, with nothing earned'
                    USING ERRCODE = '42501';
            END IF;
            -- A client creates their own. Only the worker creates one, from a rollover or a
            -- saving source such as an automatic investing run.
            IF (NEW.rolled_from_id IS NOT NULL OR NEW.source_id IS NOT NULL) <> is_worker
                OR (NOT is_worker AND actor IS DISTINCT FROM 'client') THEN
                RAISE EXCEPTION 'an investment is created by its client, a rollover or a saving source'
                    USING ERRCODE = '42501';
            END IF;
            -- The terms are the catalogue's on the day of the review, not whatever was sent.
            SELECT * INTO product FROM investment_products WHERE id = NEW.product_id;
            day := (NEW.created_at AT TIME ZONE 'Africa/Kampala')::date;
            SELECT * INTO rate FROM investment_rates
                WHERE product_id = NEW.product_id AND effective_from <= day
                ORDER BY effective_from DESC LIMIT 1;
            IF product.id IS NULL OR rate.id IS NULL OR product.status <> 'live'
                OR NEW.currency <> product.currency
                OR NEW.principal_minor < product.minimum_minor
                OR NEW.term_days IS DISTINCT FROM product.term_days
                OR NEW.annual_rate_bps <> rate.annual_rate_bps
                OR NEW.tax_bps <> rate.tax_bps THEN
                RAISE EXCEPTION 'an investment takes the product''s own terms on the day'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;

        IF OLD.status = 'cancelled' THEN
            RAISE EXCEPTION 'a cancelled investment never changes' USING ERRCODE = '42501';
        END IF;
        IF (to_jsonb(NEW) - '{{{_FREE}}}'::text[]) <> (to_jsonb(OLD) - '{{{_FREE}}}'::text[]) THEN
            RAISE EXCEPTION 'the terms of an investment never change' USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'unwound' THEN
            RAISE EXCEPTION 'an undone investment never changes' USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'withdrawn' THEN
            -- Closed. The one thing left is the payout that carries the money, recorded once
            -- by the worker when the dealing desk has funded it.
            IF (to_jsonb(NEW) - '{{withdrawal_payout_id,updated_at}}'::text[])
                <> (to_jsonb(OLD) - '{{withdrawal_payout_id,updated_at}}'::text[])
                OR OLD.withdrawal_payout_id IS NOT NULL OR NEW.withdrawal_payout_id IS NULL
                OR NOT is_worker THEN
                RAISE EXCEPTION 'a withdrawn investment never changes' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status = 'matured' THEN
            -- Closed. The one thing left is sending on money that is waiting for a
            -- destination, by the client or the worker.
            IF (to_jsonb(NEW) - '{{payout_id,payout_refusal,updated_at}}'::text[])
                <> (to_jsonb(OLD) - '{{payout_id,payout_refusal,updated_at}}'::text[])
                OR OLD.payout_id IS NOT NULL OR OLD.rolled_into_id IS NOT NULL
                OR NEW.payout_id IS NULL OR NEW.payout_refusal IS NOT NULL
                OR NOT (is_worker OR actor = 'client') THEN
                RAISE EXCEPTION 'a matured investment never changes' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.status <> OLD.status THEN
            IF NOT is_worker
                OR NOT (OLD.status = 'awaiting_payment' AND NEW.status IN ('active', 'cancelled')
                    OR OLD.status = 'active' AND NEW.status = 'matured'
                    OR OLD.status = 'active' AND NEW.status = 'withdrawn'
                        AND OLD.withdrawal_requested_at IS NOT NULL
                    -- Undone only for a mistaken deposit, once two members of staff agreed.
                    OR OLD.status = 'active' AND NEW.status = 'unwound'
                        AND OLD.withdrawal_requested_at IS NULL
                        AND EXISTS (SELECT FROM investment_corrections c
                            WHERE c.investment_id = OLD.id AND c.kind = 'unwind'
                            AND c.status = 'approved')) THEN
                RAISE EXCEPTION 'investment cannot go from % to % here', OLD.status, NEW.status
                    USING ERRCODE = '42501';
            END IF;
        ELSIF NEW.matured_at IS NOT NULL OR NEW.maturity_entry_id IS NOT NULL
            OR NEW.rolled_into_id IS NOT NULL OR NEW.payout_id IS NOT NULL
            OR NEW.payout_refusal IS NOT NULL OR NEW.withdrawn_at IS NOT NULL
            OR NEW.withdrawal_entry_id IS NOT NULL OR NEW.withdrawal_payout_id IS NOT NULL
            OR NEW.unwound_at IS NOT NULL OR NEW.unwind_entry_id IS NOT NULL THEN
            RAISE EXCEPTION 'only maturity records what became of the money'
                USING ERRCODE = '42501';
        END IF;
        IF OLD.status = 'active' AND (
            NEW.start_date <> OLD.start_date OR NEW.started_at <> OLD.started_at
            OR NEW.start_entry_id <> OLD.start_entry_id
            OR NEW.maturity_date IS DISTINCT FROM OLD.maturity_date) THEN
            RAISE EXCEPTION 'the start of an investment never changes' USING ERRCODE = '42501';
        END IF;
        -- Interest only ever goes up, a day at a time, and only the worker posts it.
        IF NEW.accrued_through IS DISTINCT FROM OLD.accrued_through
            OR NEW.accrued_gross_minor <> OLD.accrued_gross_minor
            OR NEW.accrued_tax_minor <> OLD.accrued_tax_minor THEN
            IF NOT is_worker OR OLD.status <> 'active'
                OR OLD.withdrawal_requested_at IS NOT NULL
                OR NEW.accrued_through IS NULL
                OR (OLD.accrued_through IS NOT NULL AND NEW.accrued_through <= OLD.accrued_through)
                OR NEW.accrued_gross_minor < OLD.accrued_gross_minor
                OR NEW.accrued_tax_minor < OLD.accrued_tax_minor THEN
                RAISE EXCEPTION 'interest is added by the daily run, forwards only'
                    USING ERRCODE = '42501';
            END IF;
        END IF;
        -- The client changes only the choice for maturity, and only before the maturity
        -- date. Staff change nothing here.
        IF NEW.at_maturity IS DISTINCT FROM OLD.at_maturity
            OR NEW.payout_destination_id IS DISTINCT FROM OLD.payout_destination_id THEN
            IF actor IS DISTINCT FROM 'client' OR OLD.withdrawal_requested_at IS NOT NULL THEN
                RAISE EXCEPTION 'only the client chooses what happens at maturity'
                    USING ERRCODE = '42501';
            END IF;
            IF OLD.maturity_date IS NOT NULL
                AND (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date >= OLD.maturity_date
            THEN
                RAISE EXCEPTION 'the choice for maturity closes the day before'
                    USING ERRCODE = '42501';
            END IF;
        END IF;
        -- Only the client asks to withdraw early, once, while it runs and before the day it
        -- matures. Interest stops at the request, so the amount they saw is the amount paid.
        IF NEW.withdrawal_requested_at IS DISTINCT FROM OLD.withdrawal_requested_at
            OR NEW.withdrawal_destination_id IS DISTINCT FROM OLD.withdrawal_destination_id THEN
            IF actor IS DISTINCT FROM 'client' OR OLD.status <> 'active'
                OR OLD.withdrawal_requested_at IS NOT NULL
                OR NEW.withdrawal_requested_at IS NULL OR NEW.withdrawal_destination_id IS NULL
                OR abs(extract(epoch FROM NEW.withdrawal_requested_at - clock_timestamp())) > 300
            THEN
                RAISE EXCEPTION 'only the client asks, once, to withdraw a running investment'
                    USING ERRCODE = '42501';
            END IF;
            IF OLD.maturity_date IS NOT NULL
                AND (clock_timestamp() AT TIME ZONE 'Africa/Kampala')::date >= OLD.maturity_date
            THEN
                RAISE EXCEPTION 'early withdrawal closes the day before maturity'
                    USING ERRCODE = '42501';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role name

RULES = [
    "REVOKE ALL ON FUNCTION finvest_investment_corrections_guard() FROM PUBLIC",
    "CREATE TRIGGER investment_corrections_guard BEFORE INSERT OR UPDATE "
    "ON investment_corrections FOR EACH ROW "
    "EXECUTE FUNCTION finvest_investment_corrections_guard()",
    "REVOKE ALL ON investment_corrections FROM PUBLIC",
    f"GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note) "
    f"ON investment_corrections TO {APP_ROLE}",
    f"GRANT SELECT, UPDATE (status, done_at, entry_id, payout_id, failure_code) "
    f"ON investment_corrections TO {WORKER_ROLE}",
    f"GRANT UPDATE (unwound_at, unwind_entry_id) ON investments TO {WORKER_ROLE}",
    # Staff only on the API's role, so a client transaction reads no correction, however the
    # code asks. The worker reads and records outcomes.
    "ALTER TABLE investment_corrections ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY investment_corrections_app_read ON investment_corrections FOR SELECT "
    f"TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY investment_corrections_app_insert ON investment_corrections FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK ({STAFF})",
    f"CREATE POLICY investment_corrections_app_update ON investment_corrections FOR UPDATE "
    f"TO {APP_ROLE} USING ({STAFF}) WITH CHECK ({STAFF})",
    f"CREATE POLICY investment_corrections_worker_read ON investment_corrections FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY investment_corrections_worker_update ON investment_corrections FOR UPDATE "
    f"TO {WORKER_ROLE} USING (true) WITH CHECK (true)",
]


def upgrade() -> None:
    for statement in [TABLE, *INDEXES, CORRECTIONS_GUARD, *COLUMNS, GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM investment_corrections)
        OR EXISTS (SELECT FROM investments WHERE status = 'unwound') THEN
        RAISE EXCEPTION 'refusing to drop the record of investment corrections';
    END IF;
END
$$
"""


def downgrade() -> None:
    # A correction is part of the books' story; a downgrade never erases it.
    op.execute(EMPTY_GUARD)
    op.execute(f"REVOKE UPDATE (unwound_at, unwind_entry_id) ON investments FROM {WORKER_ROLE}")
    script = op.get_context().script
    if script is None:
        raise RuntimeError("the migration scripts are needed to restore the guard")
    earlier = script.get_revision("0065_investment_carried_over")
    op.execute(earlier.module.GUARD)
    op.execute("ALTER TABLE investments DROP CONSTRAINT ck_investments_unwound")
    op.execute("ALTER TABLE investments DROP CONSTRAINT ck_investments_started")
    op.execute("ALTER TABLE investments DROP CONSTRAINT ck_investments_status")
    op.execute("ALTER TABLE investments DROP COLUMN unwind_entry_id")
    op.execute("ALTER TABLE investments DROP COLUMN unwound_at")
    op.execute(
        "ALTER TABLE investments ADD CONSTRAINT ck_investments_status CHECK (status IN "
        "('awaiting_payment', 'active', 'cancelled', 'matured', 'withdrawn'))"
    )
    op.execute(
        "ALTER TABLE investments ADD CONSTRAINT ck_investments_started CHECK ("
        "(status IN ('active', 'matured', 'withdrawn')) = (start_date IS NOT NULL) "
        "AND (start_date IS NULL) = (started_at IS NULL) "
        "AND (start_date IS NULL) = (start_entry_id IS NULL))"
    )
    op.execute("DROP TABLE investment_corrections")
    op.execute("DROP FUNCTION finvest_investment_corrections_guard()")
