"""Record paying URA the tax withheld in a month, by two people.

Revision ID: 0170_tax_remittances
Revises: 0037_payout_resolutions

Tax on interest is set aside in `tax.withholding_payable` as it is earned (migration 0062). This
table records each payment of it to URA.

* One member of staff records a payment that has left the trust account, for one month, with
  URA's payment registration number, the bank's reference and the day it was paid. A different
  signed-in member of staff approves or declines it, once. What was recorded never changes.
* An approved remittance names exactly the ledger entry it posted: from this remittance, on the
  day it was paid, with `tax.withholding_payable` and the trust account going down together by
  its amount. The ledger already refuses to take `tax.withholding_payable` below zero.
* URA's reference and the bank's are each recorded once, unless declined.
* Nothing is ever deleted. The approval posts in the second person's staff transaction, so
  `tax.remittance` joins the ledger's list of source types staff may post.

See docs/tax.md.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0170_tax_remittances"
down_revision: str | None = "0037_payout_resolutions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
T = "tax_remittances"

TABLE = """
CREATE TABLE tax_remittances (
    id push_id NOT NULL,
    month date NOT NULL,
    currency char(3) COLLATE "C" NOT NULL,
    amount_minor bigint NOT NULL,
    ura_reference varchar(100) NOT NULL,
    bank_reference varchar(100) NOT NULL,
    paid_on date NOT NULL,
    note varchar(500) NOT NULL,
    status varchar(8) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    ledger_entry_id push_id,
    CONSTRAINT pk_tax_remittances PRIMARY KEY (id),
    CONSTRAINT ck_tax_remittances_month CHECK (extract(day FROM month) = 1),
    CONSTRAINT ck_tax_remittances_paid_on CHECK (paid_on >= month),
    CONSTRAINT ck_tax_remittances_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_tax_remittances_amount CHECK (amount_minor > 0),
    CONSTRAINT ck_tax_remittances_references CHECK (
        btrim(ura_reference) <> '' AND btrim(bank_reference) <> ''),
    CONSTRAINT ck_tax_remittances_note CHECK (btrim(note) <> ''),
    CONSTRAINT ck_tax_remittances_status CHECK (status IN ('proposed', 'approved', 'declined')),
    CONSTRAINT ck_tax_remittances_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (status = 'approved') = (ledger_entry_id IS NOT NULL)),
    CONSTRAINT ck_tax_remittances_two_people CHECK (decided_by IS NULL OR decided_by <> proposed_by),
    CONSTRAINT ck_tax_remittances_decline_reason
        CHECK (status <> 'declined' OR btrim(coalesce(decision_reason, '')) <> '')
);
CREATE UNIQUE INDEX uq_tax_remittances_ura_reference
    ON tax_remittances (ura_reference) WHERE status <> 'declined';
CREATE UNIQUE INDEX uq_tax_remittances_bank_reference
    ON tax_remittances (currency, bank_reference) WHERE status <> 'declined';
CREATE UNIQUE INDEX uq_tax_remittances_entry
    ON tax_remittances (ledger_entry_id) WHERE ledger_entry_id IS NOT NULL;
CREATE INDEX ix_tax_remittances_month ON tax_remittances (currency, month, proposed_at)
"""

GUARD = """
CREATE FUNCTION finvest_tax_remittances_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        bank bigint;
        payable bigint;
        others bigint;
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'a tax remittance is never removed' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'tax remittances are recorded by staff' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status IS DISTINCT FROM 'proposed'
                OR NEW.proposed_by IS DISTINCT FROM public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a remittance starts proposed by the person signed in'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status IS DISTINCT FROM 'proposed'
            OR NEW.status NOT IN ('approved', 'declined') THEN
            RAISE EXCEPTION 'a proposed remittance is approved or declined, once'
                USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.month, NEW.currency, NEW.amount_minor, NEW.ura_reference,
            NEW.bank_reference, NEW.paid_on, NEW.note, NEW.proposed_by, NEW.proposed_at)
           IS DISTINCT FROM
           (OLD.id, OLD.month, OLD.currency, OLD.amount_minor, OLD.ura_reference,
            OLD.bank_reference, OLD.paid_on, OLD.note, OLD.proposed_by, OLD.proposed_at) THEN
            RAISE EXCEPTION 'what was recorded does not change' USING ERRCODE = '42501';
        END IF;
        IF NEW.decided_by IS DISTINCT FROM public.finvest_actor_id() THEN
            RAISE EXCEPTION 'a remittance is decided by the person signed in'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'declined' THEN
            RETURN NEW;
        END IF;
        IF NOT EXISTS (
            SELECT FROM public.ledger_journal_entries
            WHERE id = NEW.ledger_entry_id AND source_type = 'tax.remittance'
                AND source_id = NEW.id AND currency = NEW.currency
                AND effective_date = NEW.paid_on AND reverses_entry_id IS NULL
        ) THEN
            RAISE EXCEPTION 'an approved remittance names the entry it posted'
                USING ERRCODE = '42501';
        END IF;
        SELECT
            coalesce(sum(line.amount_minor) FILTER (WHERE account.code = 'bank.trust'), 0),
            coalesce(sum(line.amount_minor)
                FILTER (WHERE account.code = 'tax.withholding_payable'), 0),
            count(*) FILTER (
                WHERE account.code NOT IN ('bank.trust', 'tax.withholding_payable')
                    OR account.owner_client_id IS NOT NULL OR account.reference IS NOT NULL)
            INTO bank, payable, others
            FROM public.ledger_lines AS line
            JOIN public.ledger_accounts AS account ON account.id = line.account_id
            WHERE line.entry_id = NEW.ledger_entry_id;
        IF bank IS DISTINCT FROM -NEW.amount_minor
            OR payable IS DISTINCT FROM NEW.amount_minor OR others <> 0 THEN
            RAISE EXCEPTION 'the entry takes the amount from the tax held and the trust account'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

STAFF = "public.finvest_actor_kind() = 'staff'"

RULES = [
    "REVOKE ALL ON FUNCTION finvest_tax_remittances_guard() FROM PUBLIC",
    f"CREATE TRIGGER {T}_guard BEFORE INSERT OR UPDATE OR DELETE ON {T} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_tax_remittances_guard()",
    f"CREATE TRIGGER {T}_no_truncate BEFORE TRUNCATE ON {T} "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_tax_remittances_guard()",
    f"REVOKE ALL ON {T} FROM PUBLIC",
    f"GRANT SELECT, INSERT ON {T} TO {APP_ROLE}",
    f"GRANT UPDATE (status, decided_by, decided_at, decision_reason, ledger_entry_id) "
    f"ON {T} TO {APP_ROLE}",
    f"GRANT SELECT ON {T} TO {WORKER_ROLE}",
    f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY {T}_app_read ON {T} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY {T}_worker_read ON {T} FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY {T}_app_insert ON {T} FOR INSERT TO {APP_ROLE} WITH CHECK ({STAFF})",
    f"CREATE POLICY {T}_app_update ON {T} FOR UPDATE TO {APP_ROLE} "
    f"USING ({STAFF}) WITH CHECK ({STAFF})",
    "INSERT INTO ledger_staff_source_types (source_type, purpose) VALUES "
    "('tax.remittance', 'tax: withholding tax paid to URA, approved by a second staff member')",
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
    IF EXISTS (SELECT FROM tax_remittances) THEN
        RAISE EXCEPTION 'refusing to drop tax remittances';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DELETE FROM ledger_staff_source_types WHERE source_type = 'tax.remittance'")
    op.execute(f"DROP TABLE {T}")
    op.execute("DROP FUNCTION finvest_tax_remittances_guard()")
