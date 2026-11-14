"""Record what a client owes for a deposit credited by mistake once the money has left, by two
people, and clear it when the money arrives late or is written off.

Revision ID: 0262_payment_receivables
Revises: 0261_payment_deposit_reversals

A deposit was confirmed but the money never arrived, and by the time anyone noticed, the money
it credited had left Finvest: the investment matured and was paid out, it was withdrawn early,
or a refund went out. There is nothing to reverse. Instead, each step below is proposed by one
member of staff and approved by a different one, and the approval posts the entry in the same
transaction (docs/payments.md, "Correcting a mistaken credit"):

    open       debit  receivables.deposit_correction   credit  the account the deposit debited
    recover    debit  the account the deposit debited  credit  receivables.deposit_correction
    write_off  debit  losses.deposit_correction        credit  receivables.deposit_correction

The receivable is owned by the client, with the deposit's ID as its reference, and always for the
deposit's whole amount.

What the database enforces:

* A step starts as proposed, by the staff member signed in, for a confirmed investment deposit
  that was not reversed, with the deposit's own client, currency and amount.
* A deposit's receivable is opened once and cleared once, by recovering or writing it off, and
  only after it was opened. One step at a time waits for a second person.
* Nobody decides their own proposal, and only the proposer withdraws it.
* An applied step carries its own entry, of its kind's type, with exactly the two lines above.
* Nothing is deleted, and a decided step never changes.

Staff may post `payment.receivable` entries (ledger_staff_source_types).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0262_payment_receivables"
down_revision: str | None = "0261_payment_deposit_reversals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
STAFF = "public.finvest_actor_kind() = 'staff'"

TABLE = """
CREATE TABLE payment_receivable_steps (
    id push_id NOT NULL,
    deposit_id push_id NOT NULL,
    -- `open` records what the client owes, `recover` clears it when the money arrives late,
    -- `write_off` clears it as a loss.
    kind varchar(9) NOT NULL,
    -- The deposit's client, an ID in the identity module. No foreign key.
    client_id push_id NOT NULL,
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
    -- The entry the approval posted.
    entry_id push_id,
    CONSTRAINT pk_payment_receivable_steps PRIMARY KEY (id),
    CONSTRAINT fk_payment_receivable_steps_deposit_id_payment_deposits
        FOREIGN KEY (deposit_id) REFERENCES payment_deposits (id),
    CONSTRAINT uq_payment_receivable_steps_entry_id UNIQUE (entry_id),
    CONSTRAINT ck_payment_receivable_steps_kind CHECK (kind IN ('open', 'recover', 'write_off')),
    CONSTRAINT ck_payment_receivable_steps_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_payment_receivable_steps_amount CHECK (amount_minor > 0),
    CONSTRAINT ck_payment_receivable_steps_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_payment_receivable_steps_status
        CHECK (status IN ('proposed', 'applied', 'sent_back', 'withdrawn')),
    CONSTRAINT ck_payment_receivable_steps_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (status = 'proposed') = (decided_at IS NULL)),
    CONSTRAINT ck_payment_receivable_steps_second_person
        CHECK (status IN ('proposed', 'withdrawn') OR decided_by <> proposed_by),
    CONSTRAINT ck_payment_receivable_steps_withdrawn
        CHECK (status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_payment_receivable_steps_note
        CHECK (status <> 'sent_back' OR btrim(coalesce(decision_note, '')) <> ''),
    CONSTRAINT ck_payment_receivable_steps_applied
        CHECK ((status = 'applied') = (entry_id IS NOT NULL))
)
"""
INDEXES = [
    # One step waits for a second person at a time.
    "CREATE UNIQUE INDEX uq_payment_receivable_steps_waiting ON payment_receivable_steps "
    "(deposit_id) WHERE status = 'proposed'",
    # Opened once, and cleared once.
    "CREATE UNIQUE INDEX uq_payment_receivable_steps_opened ON payment_receivable_steps "
    "(deposit_id) WHERE status = 'applied' AND kind = 'open'",
    "CREATE UNIQUE INDEX uq_payment_receivable_steps_cleared ON payment_receivable_steps "
    "(deposit_id) WHERE status = 'applied' AND kind IN ('recover', 'write_off')",
    "CREATE INDEX ix_payment_receivable_steps_status ON payment_receivable_steps "
    "(status, proposed_at)",
]

# Runs as the table owner so it can read the deposit, the other steps and the ledger whatever
# the caller's row policies show. It pins its search path and names tables with their schema.
GUARD = """
CREATE FUNCTION finvest_payment_receivable_steps_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        deposit public.payment_deposits%ROWTYPE;
        opened boolean;
        cleared boolean;
        cash public.push_id;
        posted boolean;
    BEGIN
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff record a receivable' USING ERRCODE = '42501';
        END IF;
        SELECT * INTO deposit FROM public.payment_deposits WHERE id = NEW.deposit_id;
        IF TG_OP = 'INSERT' THEN
            SELECT
                EXISTS (SELECT FROM public.payment_receivable_steps
                        WHERE deposit_id = NEW.deposit_id AND status = 'applied'
                        AND kind = 'open'),
                EXISTS (SELECT FROM public.payment_receivable_steps
                        WHERE deposit_id = NEW.deposit_id AND status = 'applied'
                        AND kind IN ('recover', 'write_off'))
              INTO opened, cleared;
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> public.finvest_actor_id()
                OR NEW.decided_by IS NOT NULL OR NEW.entry_id IS NOT NULL
                OR deposit.id IS NULL OR deposit.status <> 'confirmed'
                OR deposit.reversal_id IS NOT NULL OR deposit.purpose_type <> 'investment'
                OR deposit.client_id <> NEW.client_id OR deposit.currency <> NEW.currency
                OR deposit.amount_minor <> NEW.amount_minor
                OR (NEW.kind = 'open' AND opened)
                OR (NEW.kind <> 'open' AND (NOT opened OR cleared))
            THEN
                RAISE EXCEPTION 'a receivable step starts as proposed, by the person signed in, '
                    'for a confirmed deposit, in order' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided receivable step never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status NOT IN ('applied', 'sent_back', 'withdrawn')
            OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
            OR (to_jsonb(NEW) - '{status,decided_by,decided_at,decision_note,entry_id}'::text[])
               <> (to_jsonb(OLD) - '{status,decided_by,decided_at,decision_note,entry_id}'::text[])
        THEN
            RAISE EXCEPTION 'a receivable step is only decided, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'applied' THEN
            -- The account the deposit's own entry debited: the wallet or bank account.
            SELECT line.account_id INTO cash
              FROM public.ledger_lines AS line
             WHERE line.entry_id = deposit.ledger_entry_id AND line.amount_minor > 0;
            -- Two lines: the receivable, debited to open and credited to clear, and on the
            -- other side the cash account, or for a write-off the losses account.
            SELECT count(*) = 2
                AND bool_or(account.code = 'receivables.deposit_correction'
                    AND account.owner_client_id = NEW.client_id
                    AND account.reference = NEW.deposit_id
                    AND line.amount_minor
                        = CASE WHEN NEW.kind = 'open' THEN NEW.amount_minor
                               ELSE -NEW.amount_minor END)
                AND bool_or(CASE WHEN NEW.kind = 'write_off'
                        THEN account.code = 'losses.deposit_correction'
                        ELSE account.id = cash END
                    AND line.amount_minor
                        = CASE WHEN NEW.kind = 'open' THEN -NEW.amount_minor
                               ELSE NEW.amount_minor END)
                AND bool_and(account.currency = NEW.currency)
              INTO posted
              FROM public.ledger_journal_entries AS entry
              JOIN public.ledger_lines AS line ON line.entry_id = entry.id
              JOIN public.ledger_accounts AS account ON account.id = line.account_id
             WHERE entry.id = NEW.entry_id AND entry.source_type = 'payment.receivable'
               AND entry.source_id = NEW.id
               AND entry.entry_type = CASE NEW.kind
                   WHEN 'open' THEN 'payment.deposit_correction'
                   WHEN 'recover' THEN 'payment.receivable_recovered'
                   ELSE 'payment.receivable_written_off' END;
            IF posted IS NOT TRUE THEN
                RAISE EXCEPTION 'a receivable step is applied only by its own ledger entry'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF NEW.entry_id IS NOT NULL THEN
            RAISE EXCEPTION 'only an applied receivable step moved money'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    "REVOKE ALL ON FUNCTION finvest_payment_receivable_steps_guard() FROM PUBLIC",
    "CREATE TRIGGER payment_receivable_steps_guard BEFORE INSERT OR UPDATE "
    "ON payment_receivable_steps FOR EACH ROW "
    "EXECUTE FUNCTION finvest_payment_receivable_steps_guard()",
    "CREATE TRIGGER payment_receivable_steps_no_delete BEFORE DELETE "
    "ON payment_receivable_steps FOR EACH ROW EXECUTE FUNCTION finvest_payments_immutable()",
    "CREATE TRIGGER payment_receivable_steps_no_truncate BEFORE TRUNCATE "
    "ON payment_receivable_steps FOR EACH STATEMENT "
    "EXECUTE FUNCTION finvest_payments_immutable()",
    "REVOKE ALL ON payment_receivable_steps FROM PUBLIC",
    "GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note, entry_id) "
    f"ON payment_receivable_steps TO {APP_ROLE}",
    f"GRANT SELECT ON payment_receivable_steps TO {WORKER_ROLE}",
    # Staff only on the API's role: a client transaction reads no step.
    "ALTER TABLE payment_receivable_steps ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY payment_receivable_steps_app_read ON payment_receivable_steps FOR SELECT "
    f"TO {APP_ROLE} USING ({STAFF})",
    "CREATE POLICY payment_receivable_steps_app_insert ON payment_receivable_steps "
    f"FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF})",
    "CREATE POLICY payment_receivable_steps_app_update ON payment_receivable_steps "
    f"FOR UPDATE TO {APP_ROLE} USING ({STAFF}) WITH CHECK ({STAFF})",
    "CREATE POLICY payment_receivable_steps_worker_read ON payment_receivable_steps "
    f"FOR SELECT TO {WORKER_ROLE} USING (true)",
    "INSERT INTO ledger_staff_source_types (source_type, purpose) VALUES ('payment.receivable', "
    "'payments: recording, recovering or writing off what a client owes for a mistaken "
    "deposit, approved by a second member of staff')",
]


def upgrade() -> None:
    for statement in [TABLE, *INDEXES, GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payment_receivable_steps) THEN
        RAISE EXCEPTION 'refusing to drop the record of mistaken deposit receivables';
    END IF;
END
$$
"""


def downgrade() -> None:
    # A receivable is part of the story of the books; a downgrade never erases one.
    op.execute(EMPTY_GUARD)
    for statement in (
        "DELETE FROM ledger_staff_source_types WHERE source_type = 'payment.receivable'",
        "DROP TABLE payment_receivable_steps",
        "DROP FUNCTION finvest_payment_receivable_steps_guard()",
    ):
        op.execute(statement)
