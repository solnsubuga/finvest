"""Move a bank transfer credited to the wrong client to the client whose money it was.

Revision ID: 0171_payment_reassignments
Revises: 0052_kyc_carried_over

A staff member matched a client's receipt to a statement line that was really another client's
payment of the same amount, and a second person approved it. The money is real and on the
statement, but it sits with the wrong client. A reassignment moves it, by two people:

* One member of staff proposes, with a reason, naming the deposit and the right client's
  investment that is waiting for exactly that amount. The evidence is the statement line the
  original review matched, recorded with the proposal and shown to the second person.
* A different member of staff approves, sends it back with a note, or the proposer withdraws it.
  Approving moves the money in the same transaction, so a reassignment is never approved and
  not yet done:

      debit   client.awaiting_start   first client, first investment    the amount
      credit  client.awaiting_start   right client, their investment     the amount

  and saves a new confirmed bank transfer deposit for the right client's investment, pointing
  at that entry. The original deposit, its review and the ledger entry it posted never change.

What the database enforces:

* A reassignment starts as proposed, by the staff member signed in, for a confirmed bank
  transfer of exactly its amount, for another client, citing the line its approved review
  matched, or for a deposit that was itself reassigned, the line that reassignment cited.
* Only the proposer withdraws it. Nobody else decides it but a different member of staff.
* It is applied only with a `payment.deposit_reassigned` entry for it that takes exactly the
  amount from the first client's awaiting-start account for the first investment and gives it
  to the right client's for theirs. By the time the transaction commits, the new deposit exists.
* A deposit has at most one open or applied reassignment, and so does a target investment.
* A deposit inserted in a staff transaction is the confirmed deposit of an applied
  reassignment, with that reassignment's client, investment, amount and entry.
* Nothing is ever deleted, and a finished reassignment never changes.

Staff may post `payment.reassignment` entries (ledger_staff_source_types).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0171_payment_reassignments"
down_revision: str | None = "0052_kyc_carried_over"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
STAFF = "public.finvest_actor_kind() = 'staff'"

OLD_CAUSES = "'request', 'send', 'status_read', 'sweep', 'reconciliation', 'staff_review'"
CAUSES = f"{OLD_CAUSES}, 'reassignment'"

TABLE = """
CREATE TABLE payment_reassignments (
    id push_id NOT NULL,
    -- The confirmed bank transfer credited to the wrong client, and who and what it paid for.
    deposit_id push_id NOT NULL,
    from_client_id push_id NOT NULL,
    from_investment_id push_id NOT NULL,
    -- The client whose money it was, and their investment waiting for exactly this amount. IDs
    -- in the identity and investments modules: no foreign key, a module owns only its tables.
    to_client_id push_id NOT NULL,
    to_investment_id push_id NOT NULL,
    currency varchar(3) NOT NULL,
    amount_minor bigint NOT NULL,
    -- The evidence: the bank statement line the original review matched. In reconciliation.
    statement_line_id push_id NOT NULL,
    reason varchar(500) NOT NULL,
    status varchar(10) NOT NULL DEFAULT 'proposed',
    -- Staff IDs in the identity module.
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_note varchar(500),
    -- The entry that moved the money, and the right client's new deposit.
    entry_id push_id,
    new_deposit_id push_id,
    CONSTRAINT pk_payment_reassignments PRIMARY KEY (id),
    CONSTRAINT fk_payment_reassignments_deposit_id_payment_deposits
        FOREIGN KEY (deposit_id) REFERENCES payment_deposits (id),
    CONSTRAINT uq_payment_reassignments_entry_id UNIQUE (entry_id),
    CONSTRAINT uq_payment_reassignments_new_deposit_id UNIQUE (new_deposit_id),
    CONSTRAINT ck_payment_reassignments_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_payment_reassignments_amount CHECK (amount_minor > 0),
    CONSTRAINT ck_payment_reassignments_other_client CHECK (to_client_id <> from_client_id),
    CONSTRAINT ck_payment_reassignments_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_payment_reassignments_status
        CHECK (status IN ('proposed', 'applied', 'sent_back', 'withdrawn')),
    CONSTRAINT ck_payment_reassignments_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (status = 'proposed') = (decided_at IS NULL)),
    -- Nobody decides their own proposal, and only the proposer withdraws it.
    CONSTRAINT ck_payment_reassignments_second_person
        CHECK (status IN ('proposed', 'withdrawn') OR decided_by <> proposed_by),
    CONSTRAINT ck_payment_reassignments_withdrawn
        CHECK (status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_payment_reassignments_note
        CHECK (status <> 'sent_back' OR btrim(coalesce(decision_note, '')) <> ''),
    -- Applied means the money moved and the new deposit is saved. Nothing else moved money.
    CONSTRAINT ck_payment_reassignments_applied CHECK (
        (status = 'applied') = (entry_id IS NOT NULL)
        AND (status = 'applied') = (new_deposit_id IS NOT NULL))
)
"""
INDEXES = [
    # A deposit moves once. A mistaken move is put right by reassigning the new deposit.
    "CREATE UNIQUE INDEX uq_payment_reassignments_deposit ON payment_reassignments (deposit_id) "
    "WHERE status IN ('proposed', 'applied')",
    "CREATE UNIQUE INDEX uq_payment_reassignments_target ON payment_reassignments "
    "(to_investment_id) WHERE status IN ('proposed', 'applied')",
    "CREATE INDEX ix_payment_reassignments_status ON payment_reassignments (status, proposed_at)",
    "CREATE INDEX ix_payment_reassignments_line ON payment_reassignments (statement_line_id)",
]

# Runs as the table owner so it can read the deposit's review and the ledger lines whatever the
# caller's row policies show. It pins its search path and names tables with their schema.
GUARD = """
CREATE FUNCTION finvest_payment_reassignments_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        deposit public.payment_deposits%ROWTYPE;
        moved boolean;
    BEGIN
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff reassign a deposit' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            SELECT * INTO deposit FROM public.payment_deposits WHERE id = NEW.deposit_id;
            IF NEW.status <> 'proposed' OR NEW.proposed_by <> public.finvest_actor_id()
                OR NEW.decided_by IS NOT NULL OR NEW.entry_id IS NOT NULL
                OR NEW.new_deposit_id IS NOT NULL
                OR deposit.id IS NULL OR deposit.status <> 'confirmed'
                OR deposit.method <> 'bank_transfer' OR deposit.purpose_type <> 'investment'
                OR deposit.client_id <> NEW.from_client_id
                OR deposit.purpose_id <> NEW.from_investment_id
                OR deposit.currency <> NEW.currency OR deposit.amount_minor <> NEW.amount_minor
                OR NOT (EXISTS (
                    SELECT FROM public.payment_bank_reviews AS review
                    JOIN public.payment_bank_approvals AS approval
                      ON approval.deposit_id = review.deposit_id
                    WHERE review.deposit_id = NEW.deposit_id AND review.outcome = 'matched'
                    AND review.statement_line_id = NEW.statement_line_id)
                    -- A deposit that was itself reassigned carries its reassignment's line.
                    OR EXISTS (
                    SELECT FROM public.payment_reassignments AS earlier
                    WHERE earlier.new_deposit_id = NEW.deposit_id AND earlier.status = 'applied'
                    AND earlier.statement_line_id = NEW.statement_line_id))
            THEN
                RAISE EXCEPTION 'a reassignment starts as proposed, by the person signed in, '
                    'for a confirmed bank transfer and the line its review matched'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' THEN
            RAISE EXCEPTION 'a decided reassignment never changes' USING ERRCODE = '42501';
        END IF;
        IF NEW.status NOT IN ('applied', 'sent_back', 'withdrawn')
            OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
            OR (to_jsonb(NEW)
                - '{status,decided_by,decided_at,decision_note,entry_id,new_deposit_id}'::text[])
               <> (to_jsonb(OLD)
                - '{status,decided_by,decided_at,decision_note,entry_id,new_deposit_id}'::text[])
        THEN
            RAISE EXCEPTION 'a reassignment is only decided, by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'applied' THEN
            -- The entry takes exactly the amount from the first client's money awaiting start
            -- for the first investment and gives it to the right client's for theirs. Nothing
            -- else is in it.
            SELECT count(*) = 2
                AND bool_and(
                    (line.amount_minor = NEW.amount_minor
                        AND account.owner_client_id = NEW.from_client_id
                        AND account.reference = NEW.from_investment_id)
                    OR (line.amount_minor = -NEW.amount_minor
                        AND account.owner_client_id = NEW.to_client_id
                        AND account.reference = NEW.to_investment_id))
                AND bool_or(line.amount_minor > 0)
                AND bool_and(account.code = 'client.awaiting_start'
                    AND account.currency = NEW.currency)
              INTO moved
              FROM public.ledger_journal_entries AS entry
              JOIN public.ledger_lines AS line ON line.entry_id = entry.id
              JOIN public.ledger_accounts AS account ON account.id = line.account_id
             WHERE entry.id = NEW.entry_id AND entry.entry_type = 'payment.deposit_reassigned'
               AND entry.source_type = 'payment.reassignment' AND entry.source_id = NEW.id;
            IF moved IS NOT TRUE THEN
                RAISE EXCEPTION 'a reassignment is applied only by its own ledger entry'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF NEW.entry_id IS NOT NULL OR NEW.new_deposit_id IS NOT NULL THEN
            RAISE EXCEPTION 'only an applied reassignment moved money' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# Checked when the transaction commits: an applied reassignment has its new deposit.
HAS_DEPOSIT = """
CREATE FUNCTION finvest_payment_reassignments_has_deposit() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF NEW.status = 'applied' AND NOT EXISTS (
            SELECT FROM public.payment_deposits WHERE id = NEW.new_deposit_id
        ) THEN
            RAISE EXCEPTION 'an applied reassignment has its new deposit'
                USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END;
    $$
"""

# A deposit saved in a staff transaction is only ever the new deposit of an applied
# reassignment, exactly as that reassignment says. Clients and the worker are not affected.
DEPOSIT_INSERT_GUARD = """
CREATE FUNCTION finvest_payment_reassigned_deposit_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RETURN NEW;
        END IF;
        IF NEW.status <> 'confirmed' OR NEW.method <> 'bank_transfer' OR NEW.provider <> 'bank'
            OR NEW.purpose_type <> 'investment' OR NEW.provider_reference IS NOT NULL
            OR NOT EXISTS (
                SELECT FROM public.payment_reassignments AS moved
                WHERE moved.new_deposit_id = NEW.id AND moved.status = 'applied'
                AND moved.to_client_id = NEW.client_id
                AND moved.to_investment_id = NEW.purpose_id
                AND moved.currency = NEW.currency AND moved.amount_minor = NEW.amount_minor
                AND moved.entry_id = NEW.ledger_entry_id
                AND moved.decided_by = public.finvest_actor_id())
        THEN
            RAISE EXCEPTION 'staff save a deposit only for an applied reassignment'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

RULES = [
    *(
        f"REVOKE ALL ON FUNCTION {name}() FROM PUBLIC"
        for name in (
            "finvest_payment_reassignments_guard",
            "finvest_payment_reassignments_has_deposit",
            "finvest_payment_reassigned_deposit_guard",
        )
    ),
    "CREATE TRIGGER payment_reassignments_guard BEFORE INSERT OR UPDATE "
    "ON payment_reassignments FOR EACH ROW "
    "EXECUTE FUNCTION finvest_payment_reassignments_guard()",
    "CREATE CONSTRAINT TRIGGER payment_reassignments_has_deposit AFTER UPDATE "
    "ON payment_reassignments DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
    "EXECUTE FUNCTION finvest_payment_reassignments_has_deposit()",
    "CREATE TRIGGER payment_reassignments_no_delete BEFORE DELETE ON payment_reassignments "
    "FOR EACH ROW EXECUTE FUNCTION finvest_payments_immutable()",
    "CREATE TRIGGER payment_reassignments_no_truncate BEFORE TRUNCATE ON payment_reassignments "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_payments_immutable()",
    "CREATE TRIGGER payment_deposits_reassigned_insert BEFORE INSERT ON payment_deposits "
    "FOR EACH ROW EXECUTE FUNCTION finvest_payment_reassigned_deposit_guard()",
    "REVOKE ALL ON payment_reassignments FROM PUBLIC",
    "GRANT SELECT, INSERT, UPDATE (status, decided_by, decided_at, decision_note, entry_id, "
    f"new_deposit_id) ON payment_reassignments TO {APP_ROLE}",
    f"GRANT SELECT ON payment_reassignments TO {WORKER_ROLE}",
    # Staff only on the API's role: a client transaction reads no reassignment.
    "ALTER TABLE payment_reassignments ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY payment_reassignments_app_read ON payment_reassignments FOR SELECT "
    f"TO {APP_ROLE} USING ({STAFF})",
    "CREATE POLICY payment_reassignments_app_insert ON payment_reassignments FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK ({STAFF})",
    "CREATE POLICY payment_reassignments_app_update ON payment_reassignments FOR UPDATE "
    f"TO {APP_ROLE} USING ({STAFF}) WITH CHECK ({STAFF})",
    "CREATE POLICY payment_reassignments_worker_read ON payment_reassignments FOR SELECT "
    f"TO {WORKER_ROLE} USING (true)",
    # The right client's new deposit, saved by the approver. The trigger above checks it.
    "CREATE POLICY payment_deposits_app_reassigned ON payment_deposits FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK ({STAFF} AND status = 'confirmed')",
    "ALTER TABLE payment_deposit_events DROP CONSTRAINT ck_payment_deposit_events_cause",
    "ALTER TABLE payment_deposit_events ADD CONSTRAINT ck_payment_deposit_events_cause "
    f"CHECK (cause IN ({CAUSES}))",
    "CREATE POLICY payment_deposit_events_app_reassigned ON payment_deposit_events FOR INSERT "
    f"TO {APP_ROLE} WITH CHECK ({STAFF} AND cause = 'reassignment' AND from_status IS NULL "
    "AND to_status = 'confirmed' AND EXISTS (SELECT FROM payment_reassignments AS moved "
    "WHERE moved.new_deposit_id = payment_deposit_events.deposit_id))",
    "INSERT INTO ledger_staff_source_types (source_type, purpose) VALUES ('payment.reassignment', "
    "'payments: moving a bank transfer credited to the wrong client, approved by a second "
    "member of staff')",
]


def upgrade() -> None:
    for statement in [TABLE, *INDEXES, GUARD, HAS_DEPOSIT, DEPOSIT_INSERT_GUARD, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payment_reassignments)
        OR EXISTS (SELECT FROM payment_deposit_events WHERE cause = 'reassignment') THEN
        RAISE EXCEPTION 'refusing to drop the record of reassigned deposits';
    END IF;
END
$$
"""


def downgrade() -> None:
    # A reassignment is part of the story of the books; a downgrade never erases one.
    op.execute(EMPTY_GUARD)
    for statement in (
        "DELETE FROM ledger_staff_source_types WHERE source_type = 'payment.reassignment'",
        "DROP POLICY payment_deposit_events_app_reassigned ON payment_deposit_events",
        "ALTER TABLE payment_deposit_events DROP CONSTRAINT ck_payment_deposit_events_cause",
        "ALTER TABLE payment_deposit_events ADD CONSTRAINT ck_payment_deposit_events_cause "
        f"CHECK (cause IN ({OLD_CAUSES}))",
        "DROP POLICY payment_deposits_app_reassigned ON payment_deposits",
        "DROP TRIGGER payment_deposits_reassigned_insert ON payment_deposits",
        "DROP TABLE payment_reassignments",
        "DROP FUNCTION finvest_payment_reassigned_deposit_guard()",
        "DROP FUNCTION finvest_payment_reassignments_has_deposit()",
        "DROP FUNCTION finvest_payment_reassignments_guard()",
    ):
        op.execute(statement)
