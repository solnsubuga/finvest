"""Add transfers between Finvest's own pots, recorded by one person and approved by another.

Revision ID: 0042_pot_transfers
Revises: 0140_documents

* A transfer is proposed by one member of staff, with a reason, and approved or declined once
  by a different signed-in member of staff. The database refuses the same person twice.
* What was proposed never changes: the pots, the amounts, the value date and the reference.
* What arrived is never more than what was sent. The difference is the provider's fee.
* An approved transfer names the ledger entry it posted. Nothing is ever deleted.
* The approval is posted in the second person's staff transaction, so `pot_transfer` joins the
  ledger's list of source types staff may post.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0042_pot_transfers"
down_revision: str | None = "0140_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"

TABLE = """
CREATE TABLE reconciliation_pot_transfers (
    id push_id NOT NULL,
    from_source varchar(16) NOT NULL,
    from_reference varchar(64) NOT NULL,
    to_source varchar(16) NOT NULL,
    to_reference varchar(64) NOT NULL,
    currency varchar(3) NOT NULL,
    sent_minor bigint NOT NULL,
    received_minor bigint NOT NULL,
    value_date date NOT NULL,
    provider_reference varchar(100) NOT NULL,
    status varchar(16) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    reason varchar(500) NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    ledger_entry_id push_id,
    CONSTRAINT pk_reconciliation_pot_transfers PRIMARY KEY (id),
    CONSTRAINT ck_reconciliation_pot_transfers_sources CHECK (
        from_source IN ('iotec', 'bank') AND to_source IN ('iotec', 'bank')
    ),
    CONSTRAINT ck_reconciliation_pot_transfers_different_pots
        CHECK ((from_source, from_reference) <> (to_source, to_reference)),
    CONSTRAINT ck_reconciliation_pot_transfers_currency CHECK (currency IN ('UGX', 'USD')),
    CONSTRAINT ck_reconciliation_pot_transfers_amounts
        CHECK (received_minor > 0 AND sent_minor >= received_minor),
    CONSTRAINT ck_reconciliation_pot_transfers_provider_reference
        CHECK (btrim(provider_reference) <> ''),
    CONSTRAINT ck_reconciliation_pot_transfers_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_reconciliation_pot_transfers_status
        CHECK (status IN ('proposed', 'approved', 'declined')),
    CONSTRAINT ck_reconciliation_pot_transfers_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
        AND (status = 'approved') = (ledger_entry_id IS NOT NULL)
    ),
    CONSTRAINT ck_reconciliation_pot_transfers_two_people
        CHECK (decided_by IS NULL OR decided_by <> proposed_by),
    CONSTRAINT ck_reconciliation_pot_transfers_decline_reason
        CHECK (status <> 'declined' OR btrim(COALESCE(decision_reason, '')) <> '')
);
CREATE UNIQUE INDEX uq_reconciliation_pot_transfers_provider_reference
    ON reconciliation_pot_transfers (from_source, provider_reference)
    WHERE status <> 'declined'
"""

GUARD_FUNCTION = """
CREATE FUNCTION finvest_reconciliation_pot_transfers_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'a transfer is never deleted' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' THEN
                RAISE EXCEPTION 'a transfer starts proposed' USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' OR NEW.status NOT IN ('approved', 'declined') THEN
            RAISE EXCEPTION 'a proposed transfer is approved or declined, once'
                USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.from_source, NEW.from_reference, NEW.to_source, NEW.to_reference,
            NEW.currency, NEW.sent_minor, NEW.received_minor, NEW.value_date,
            NEW.provider_reference, NEW.proposed_by, NEW.proposed_at, NEW.reason)
           IS DISTINCT FROM
           (OLD.id, OLD.from_source, OLD.from_reference, OLD.to_source, OLD.to_reference,
            OLD.currency, OLD.sent_minor, OLD.received_minor, OLD.value_date,
            OLD.provider_reference, OLD.proposed_by, OLD.proposed_at, OLD.reason) THEN
            RAISE EXCEPTION 'what was proposed does not change' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() <> 'staff'
           OR NEW.decided_by IS DISTINCT FROM public.finvest_actor_id() THEN
            RAISE EXCEPTION 'a transfer is decided by the signed-in member of staff'
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

STAFF = "finvest_actor_kind() = 'staff'"
T = "reconciliation_pot_transfers"

RULES = [
    "REVOKE ALL ON FUNCTION finvest_reconciliation_pot_transfers_guard() FROM PUBLIC",
    f"CREATE TRIGGER {T}_guard BEFORE INSERT OR UPDATE OR DELETE ON {T} "
    "FOR EACH ROW EXECUTE FUNCTION finvest_reconciliation_pot_transfers_guard()",
    f"CREATE TRIGGER {T}_no_truncate BEFORE TRUNCATE ON {T} "
    "FOR EACH STATEMENT EXECUTE FUNCTION finvest_reconciliation_never_changed()",
    f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY",
    f"REVOKE ALL ON {T} FROM PUBLIC",
    f"GRANT SELECT, INSERT ON {T} TO {APP_ROLE}",
    f"GRANT UPDATE (status, decided_by, decided_at, decision_reason, ledger_entry_id) ON {T} "
    f"TO {APP_ROLE}",
    f"GRANT SELECT ON {T} TO {WORKER_ROLE}",
    f"CREATE POLICY {T}_app_read ON {T} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
    f"CREATE POLICY {T}_worker_read ON {T} FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY {T}_app_insert ON {T} FOR INSERT TO {APP_ROLE} "
    f"WITH CHECK ({STAFF} AND proposed_by = finvest_actor_id())",
    f"CREATE POLICY {T}_app_update ON {T} FOR UPDATE TO {APP_ROLE} "
    f"USING ({STAFF}) WITH CHECK ({STAFF})",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


STAFF_SOURCE = (
    "INSERT INTO ledger_staff_source_types (source_type, purpose) VALUES "
    "('pot_transfer', 'reconciliation: a transfer between pots a second staff member approved')"
)


def upgrade() -> None:
    for statement in [*split(TABLE), GUARD_FUNCTION, *RULES, STAFF_SOURCE]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM reconciliation_pot_transfers) THEN
        RAISE EXCEPTION 'refusing to drop pot transfers';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DELETE FROM ledger_staff_source_types WHERE source_type = 'pot_transfer'")
    op.execute(f"DROP TABLE {T}")
    op.execute("DROP FUNCTION finvest_reconciliation_pot_transfers_guard()")
