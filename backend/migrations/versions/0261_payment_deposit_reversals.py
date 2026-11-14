"""Take back a deposit credited by mistake while its money still waits, by two people.

Revision ID: 0261_payment_deposit_reversals
Revises: 0301_goal_saving_rules

A deposit was confirmed, but the money never arrived: a provider said yes in error, or staff
matched a bank transfer to a statement line that was not it. While the whole amount still waits
in the client's `client.awaiting_start` account for the investment, the deposit's own ledger
entry is reversed exactly:

    debit   client.awaiting_start   the client, the investment       the amount
    credit  the wallet or bank account the deposit said it reached   the amount

One member of staff proposes, with a reason. A different one approves, sends it back with a
note, or the proposer withdraws it. The worker carries out what was approved: it locks the
investment as the start of an investment does, checks again, posts the reversal and marks the
deposit reversed. If anything changed, the reversal is marked failed and nothing moves.

A reversed deposit stays `confirmed`, because it was, and keeps its entry. Its `reversal_id`
says it was taken back, so it no longer counts as the investment's payment: the client can pay
again, or the investment is cancelled as unpaid at its pay-by time.

What the database enforces:

* A reversal starts as proposed, by the staff member signed in, for a confirmed investment
  deposit that has not been reversed, whose entry is the one that confirmed it, with the
  deposit's own client, investment, currency and amount.
* One open, approved or done reversal per deposit.
* Nobody decides their own proposal, and only the proposer withdraws it.
* Only the worker records the outcome of an approved reversal. Done means its own entry
  exactly reverses the deposit's entry.
* A deposit's `reversal_id` is set once, by the worker, to a done reversal of that deposit, and
  nothing else about a confirmed deposit ever changes.
* Nothing is deleted, and a finished reversal never changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0261_payment_deposit_reversals"
down_revision: str | None = "0301_goal_saving_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
STAFF = "public.finvest_actor_kind() = 'staff'"
GUARD_REVISION = "0022_bank_transfer_deposits"

TABLE = """
CREATE TABLE payment_deposit_reversals (
    id push_id NOT NULL,
    deposit_id push_id NOT NULL,
    -- The deposit's client and investment. IDs in the identity and investments modules: no
    -- foreign key, a module owns only its own tables.
    client_id push_id NOT NULL,
    investment_id push_id NOT NULL,
    currency varchar(3) NOT NULL,
    amount_minor bigint NOT NULL,
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    -- Staff IDs in the identity module.
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_note varchar(500),
    done_at timestamptz,
    -- The entry that reversed the deposit's entry.
    entry_id push_id,
    failure_code varchar(64),
    CONSTRAINT pk_payment_deposit_reversals PRIMARY KEY (id),
    CONSTRAINT fk_payment_deposit_reversals_deposit_id_payment_deposits
        FOREIGN KEY (deposit_id) REFERENCES payment_deposits (id),
    CONSTRAINT uq_payment_deposit_reversals_entry_id UNIQUE (entry_id),
    CONSTRAINT ck_payment_deposit_reversals_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_payment_deposit_reversals_amount CHECK (amount_minor > 0),
    CONSTRAINT ck_payment_deposit_reversals_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_payment_deposit_reversals_status CHECK (
        status IN ('proposed', 'approved', 'sent_back', 'withdrawn', 'done', 'failed')),
    CONSTRAINT ck_payment_deposit_reversals_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (status = 'proposed') = (decided_at IS NULL)),
    CONSTRAINT ck_payment_deposit_reversals_second_person
        CHECK (status IN ('proposed', 'withdrawn') OR decided_by <> proposed_by),
    CONSTRAINT ck_payment_deposit_reversals_withdrawn
        CHECK (status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_payment_deposit_reversals_note
        CHECK (status <> 'sent_back' OR btrim(coalesce(decision_note, '')) <> ''),
    -- Done means the money moved. Failed means it did not, and says why.
    CONSTRAINT ck_payment_deposit_reversals_outcome CHECK (
        (status IN ('done', 'failed')) = (done_at IS NOT NULL)
        AND (status = 'done') = (entry_id IS NOT NULL)
        AND (status = 'failed') = (failure_code IS NOT NULL))
)
"""
INDEXES = [
    # A deposit is taken back once, and is in at most one proposal at a time.
    "CREATE UNIQUE INDEX uq_payment_deposit_reversals_deposit ON payment_deposit_reversals "
    "(deposit_id) WHERE status IN ('proposed', 'approved', 'done')",
    "CREATE INDEX ix_payment_deposit_reversals_status ON payment_deposit_reversals "
    "(status, proposed_at)",
]

# Runs as the caller, so the worker is told apart by its role. Staff and the worker both read
# every deposit and every ledger entry. It pins its search path and names tables with their
# schema.
REVERSALS_GUARD = f"""
CREATE FUNCTION finvest_payment_deposit_reversals_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        deposit public.payment_deposits%ROWTYPE;
        reverses boolean;
        is_worker boolean := public.finvest_actor_kind() = 'system'
            AND current_user = '{WORKER_ROLE}';
    BEGIN
        IF TG_OP = 'INSERT' THEN
            SELECT * INTO deposit FROM public.payment_deposits WHERE id = NEW.deposit_id;
            IF public.finvest_actor_kind() IS DISTINCT FROM 'staff'
                OR NEW.status <> 'proposed' OR NEW.proposed_by <> public.finvest_actor_id()
                OR NEW.decided_by IS NOT NULL OR NEW.done_at IS NOT NULL
                OR NEW.entry_id IS NOT NULL OR NEW.failure_code IS NOT NULL
                OR deposit.id IS NULL OR deposit.status <> 'confirmed'
                OR deposit.reversal_id IS NOT NULL OR deposit.purpose_type <> 'investment'
                OR deposit.client_id <> NEW.client_id OR deposit.purpose_id <> NEW.investment_id
                OR deposit.currency <> NEW.currency OR deposit.amount_minor <> NEW.amount_minor
                OR NOT EXISTS (
                    SELECT FROM public.ledger_journal_entries AS entry
                    WHERE entry.id = deposit.ledger_entry_id
                    AND entry.entry_type = 'payment.deposit_confirmed'
                    AND entry.source_type = 'payment.deposit' AND entry.source_id = deposit.id)
            THEN
                RAISE EXCEPTION 'a reversal starts as proposed, by the person signed in, for a '
                    'confirmed investment deposit that was not reversed'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status = 'proposed' THEN
            IF public.finvest_actor_kind() IS DISTINCT FROM 'staff'
                OR NEW.status NOT IN ('approved', 'sent_back', 'withdrawn')
                OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
                OR (to_jsonb(NEW) - '{{status,decided_by,decided_at,decision_note}}'::text[])
                    <> (to_jsonb(OLD) - '{{status,decided_by,decided_at,decision_note}}'::text[])
            THEN
                RAISE EXCEPTION 'a reversal is only decided, by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status = 'approved' THEN
            IF NOT is_worker OR NEW.status NOT IN ('done', 'failed')
                OR (to_jsonb(NEW) - '{{status,done_at,entry_id,failure_code}}'::text[])
                    <> (to_jsonb(OLD) - '{{status,done_at,entry_id,failure_code}}'::text[])
            THEN
                RAISE EXCEPTION 'only the worker records what an approved reversal did'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.status = 'done' THEN
                SELECT entry.entry_type = 'payment.deposit_reversed'
                    AND entry.source_type = 'payment.deposit_reversal'
                    AND entry.source_id = NEW.id
                    AND entry.reverses_entry_id = original.ledger_entry_id
                  INTO reverses
                  FROM public.ledger_journal_entries AS entry,
                       public.payment_deposits AS original
                 WHERE entry.id = NEW.entry_id AND original.id = NEW.deposit_id;
                IF reverses IS NOT TRUE THEN
                    RAISE EXCEPTION 'a reversal is done only by its own entry reversing the '
                        'deposit''s' USING ERRCODE = '42501';
                END IF;
            END IF;
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'a finished reversal never changes' USING ERRCODE = '42501';
    END;
    $$
"""  # noqa: S608 - constant role name and status pairs


def deposit_guard(moves: Sequence[tuple[str, str]]) -> str:
    """The deposit update guard of 0022, which also lets the worker mark a deposit reversed."""
    moves_sql = ", ".join(f"('{old}', '{new}')" for old, new in moves)
    return f"""
CREATE OR REPLACE FUNCTION finvest_payment_deposit_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF (NEW.id, NEW.client_id, NEW.purpose_type, NEW.purpose_id, NEW.method, NEW.provider,
            NEW.currency, NEW.amount_minor, NEW.payer_msisdn, NEW.created_at)
           IS DISTINCT FROM
           (OLD.id, OLD.client_id, OLD.purpose_type, OLD.purpose_id, OLD.method, OLD.provider,
            OLD.currency, OLD.amount_minor, OLD.payer_msisdn, OLD.created_at) THEN
            RAISE EXCEPTION 'a deposit''s terms cannot change' USING ERRCODE = '42501';
        END IF;
        IF NEW.reversal_id IS DISTINCT FROM OLD.reversal_id THEN
            -- Set once, on a confirmed deposit, to a done reversal of it, and nothing else.
            IF OLD.reversal_id IS NOT NULL OR OLD.status <> 'confirmed'
                OR (to_jsonb(NEW) - 'reversal_id') <> (to_jsonb(OLD) - 'reversal_id')
                OR NOT EXISTS (
                    SELECT FROM public.payment_deposit_reversals AS reversal
                    WHERE reversal.id = NEW.reversal_id AND reversal.deposit_id = NEW.id
                    AND reversal.status = 'done')
            THEN
                RAISE EXCEPTION 'a deposit is marked reversed once, by its done reversal'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status IN ('confirmed', 'failed') THEN
            RAISE EXCEPTION 'a % deposit is final', OLD.status USING ERRCODE = '42501';
        END IF;
        IF NEW.status <> OLD.status
           AND (OLD.status, NEW.status) NOT IN ({moves_sql}) THEN
            RAISE EXCEPTION 'a deposit cannot move from % to %', OLD.status, NEW.status
                USING ERRCODE = '42501';
        END IF;
        IF OLD.provider_reference IS NOT NULL
           AND NEW.provider_reference IS DISTINCT FROM OLD.provider_reference THEN
            RAISE EXCEPTION 'a provider reference cannot change' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""  # noqa: S608 - constant role name and status pairs


COLUMNS = [
    # The done reversal that took this deposit back. Set once, by the worker.
    "ALTER TABLE payment_deposits ADD COLUMN reversal_id push_id",
    "ALTER TABLE payment_deposits ADD CONSTRAINT uq_payment_deposits_reversal_id "
    "UNIQUE (reversal_id)",
    # A reversed deposit no longer holds the investment's one live payment.
    "DROP INDEX uq_payment_deposits_live_investment",
    "CREATE UNIQUE INDEX uq_payment_deposits_live_investment ON payment_deposits (purpose_id) "
    "WHERE purpose_type = 'investment' AND status <> 'failed' AND reversal_id IS NULL",
]

RULES = [
    "REVOKE ALL ON FUNCTION finvest_payment_deposit_reversals_guard() FROM PUBLIC",
    "CREATE TRIGGER payment_deposit_reversals_guard BEFORE INSERT OR UPDATE "
    "ON payment_deposit_reversals FOR EACH ROW "
    "EXECUTE FUNCTION finvest_payment_deposit_reversals_guard()",
    "CREATE TRIGGER payment_deposit_reversals_no_delete BEFORE DELETE "
    "ON payment_deposit_reversals FOR EACH ROW EXECUTE FUNCTION finvest_payments_immutable()",
    "CREATE TRIGGER payment_deposit_reversals_no_truncate BEFORE TRUNCATE "
    "ON payment_deposit_reversals FOR EACH STATEMENT "
    "EXECUTE FUNCTION finvest_payments_immutable()",
    "REVOKE ALL ON payment_deposit_reversals FROM PUBLIC",
    "GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note) "
    f"ON payment_deposit_reversals TO {APP_ROLE}",
    "GRANT SELECT, UPDATE (status, done_at, entry_id, failure_code) "
    f"ON payment_deposit_reversals TO {WORKER_ROLE}",
    f"GRANT UPDATE (reversal_id) ON payment_deposits TO {WORKER_ROLE}",
    # Staff only on the API's role: a client transaction reads no reversal.
    "ALTER TABLE payment_deposit_reversals ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY payment_deposit_reversals_app_read ON payment_deposit_reversals FOR SELECT "
    f"TO {APP_ROLE} USING ({STAFF})",
    "CREATE POLICY payment_deposit_reversals_app_insert ON payment_deposit_reversals "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF})",
    "CREATE POLICY payment_deposit_reversals_app_update ON payment_deposit_reversals "
    f"FOR UPDATE TO {APP_ROLE} USING ({STAFF}) WITH CHECK ({STAFF})",
    "CREATE POLICY payment_deposit_reversals_worker ON payment_deposit_reversals "
    f"TO {WORKER_ROLE} USING (true) WITH CHECK (true)",
]


def _earlier_guard_moves() -> Sequence[tuple[str, str]]:
    script = op.get_context().script
    if script is None:
        raise RuntimeError("the migration scripts are needed to rebuild the deposit guard")
    moves: Sequence[tuple[str, str]] = script.get_revision(GUARD_REVISION).module.ALLOWED_MOVES
    return moves


def upgrade() -> None:
    for statement in [TABLE, *INDEXES, REVERSALS_GUARD, *COLUMNS, *RULES]:
        op.execute(statement)
    op.execute(deposit_guard(_earlier_guard_moves()))


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payment_deposit_reversals)
        OR EXISTS (SELECT FROM payment_deposits WHERE reversal_id IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to drop the record of reversed deposits';
    END IF;
END
$$
"""


def downgrade() -> None:
    # A reversal is part of the story of the books; a downgrade never erases one.
    op.execute(EMPTY_GUARD)
    script = op.get_context().script
    if script is None:
        raise RuntimeError("the migration scripts are needed to restore the deposit guard")
    earlier = script.get_revision(GUARD_REVISION).module
    op.execute(earlier.guard(earlier.ALLOWED_MOVES))
    for statement in (
        f"REVOKE UPDATE (reversal_id) ON payment_deposits FROM {WORKER_ROLE}",
        "DROP INDEX uq_payment_deposits_live_investment",
        "CREATE UNIQUE INDEX uq_payment_deposits_live_investment ON payment_deposits "
        "(purpose_id) WHERE purpose_type = 'investment' AND status <> 'failed'",
        "ALTER TABLE payment_deposits DROP COLUMN reversal_id",
        "DROP TABLE payment_deposit_reversals",
        "DROP FUNCTION finvest_payment_deposit_reversals_guard()",
    ):
        op.execute(statement)
