"""Take back a paid payout that came back, with two people, and let the client send it again.

Revision ID: 0044_payout_returns
Revises: 0039_payout_bank_batches

A payout the provider or bank confirmed can still come back: a bank account closed after the
transfer left, or a mobile money reversal. The money is in the pot again, and Finvest owes it to
the client again. A paid payout never changes, so the return is a record of its own:

* One member of staff proposes it, naming the statement line that brought the money back: on the
  pot the payout was paid from, coming in, for the payout's amount and currency, dated on or after
  the day it was paid, and named by no other open or approved return.
* A different member of staff approves it. In the same transaction the ledger posts
  `payout.returned`: debit the pot, credit the client's "payout pending" account for the
  payout's source, so the client can send it again from there. The approval carries that entry,
  and the database checks it is the right one.
* One return per payout, ever: one open, and once approved, no other.
* Nothing is deleted, and a decided return never changes. Clients see their own approved returns.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0044_payout_returns"
down_revision: str | None = "0039_payout_bank_batches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
T = "payout_returns"

TABLE = """
CREATE TABLE payout_returns (
    id push_id NOT NULL,
    payout_id push_id NOT NULL,
    client_id push_id NOT NULL,
    statement_line_id push_id NOT NULL,
    reason varchar(500) NOT NULL,
    status varchar(16) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    -- No foreign key into the ledger's tables: the guard checks the entry, as other modules do.
    ledger_entry_id push_id,
    CONSTRAINT pk_payout_returns PRIMARY KEY (id),
    CONSTRAINT fk_payout_returns_payout FOREIGN KEY (payout_id) REFERENCES payouts (id),
    CONSTRAINT fk_payout_returns_line
        FOREIGN KEY (statement_line_id) REFERENCES reconciliation_statement_lines (id),
    CONSTRAINT ck_payout_returns_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_payout_returns_status
        CHECK (status IN ('proposed', 'approved', 'declined', 'withdrawn')),
    CONSTRAINT ck_payout_returns_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
    ),
    CONSTRAINT ck_payout_returns_posted CHECK ((status = 'approved') = (ledger_entry_id IS NOT NULL)),
    CONSTRAINT ck_payout_returns_two_people CHECK (
        status NOT IN ('approved', 'declined') OR decided_by <> proposed_by
    ),
    CONSTRAINT ck_payout_returns_withdrawn_by_proposer
        CHECK (status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_payout_returns_decision_reason
        CHECK (status = 'proposed' OR btrim(COALESCE(decision_reason, '')) <> '')
);
CREATE UNIQUE INDEX uq_payout_returns_live ON payout_returns (payout_id)
    WHERE status IN ('proposed', 'approved');
CREATE UNIQUE INDEX uq_payout_returns_line ON payout_returns (statement_line_id)
    WHERE status IN ('proposed', 'approved');
CREATE INDEX ix_payout_returns_client ON payout_returns (client_id, status)
"""

EVIDENCE_FUNCTION = """
CREATE FUNCTION public.finvest_payout_return_evidence(returned public.payout_returns)
    RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        payout public.payouts;
        pot_source text;
        pot_reference text;
    BEGIN
        SELECT * INTO payout FROM public.payouts WHERE id = returned.payout_id;
        IF NOT FOUND OR payout.status <> 'paid' OR payout.client_id <> returned.client_id THEN
            RETURN false;
        END IF;
        IF payout.kind = 'bank' THEN
            pot_source := 'bank';
            pot_reference := 'trust';
        ELSE
            pot_source := 'iotec';
            SELECT network INTO pot_reference FROM public.payout_destinations
             WHERE id = payout.destination_id;
        END IF;
        RETURN EXISTS (
            SELECT FROM public.reconciliation_statement_lines AS line
              JOIN public.reconciliation_statements AS statement
                ON statement.id = line.statement_id
             WHERE line.id = returned.statement_line_id AND statement.source = pot_source
               AND statement.account_reference = pot_reference
               AND statement.currency = payout.currency AND line.direction = 'in'
               AND line.amount_minor = payout.amount_minor
               AND line.value_date >= (payout.updated_at AT TIME ZONE 'Africa/Kampala')::date
        );
    END;
    $$
"""

GUARD_FUNCTION = """
CREATE FUNCTION public.finvest_payout_returns_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        payout public.payouts;
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'a payout return is never deleted' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff take back a payout' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by IS DISTINCT FROM public.finvest_actor_id()
               OR NEW.ledger_entry_id IS NOT NULL
               OR abs(extract(epoch FROM NEW.proposed_at - clock_timestamp())) > 300 THEN
                RAISE EXCEPTION 'a return starts proposed, by the person signed in, now'
                    USING ERRCODE = '42501';
            END IF;
            IF NOT public.finvest_payout_return_evidence(NEW) THEN
                RAISE EXCEPTION 'the statement does not show this payout coming back'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' OR NEW.status = 'proposed' THEN
            RAISE EXCEPTION 'a proposed return is decided once' USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.payout_id, NEW.client_id, NEW.statement_line_id, NEW.reason,
            NEW.proposed_by, NEW.proposed_at)
           IS DISTINCT FROM
           (OLD.id, OLD.payout_id, OLD.client_id, OLD.statement_line_id, OLD.reason,
            OLD.proposed_by, OLD.proposed_at) THEN
            RAISE EXCEPTION 'what was proposed does not change' USING ERRCODE = '42501';
        END IF;
        IF NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
           OR abs(extract(epoch FROM NEW.decided_at - clock_timestamp())) > 300 THEN
            RAISE EXCEPTION 'a return is decided by the person signed in, now'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'approved' THEN
            IF NOT public.finvest_payout_return_evidence(NEW) THEN
                RAISE EXCEPTION 'the statement no longer shows this payout coming back'
                    USING ERRCODE = '23514';
            END IF;
            SELECT * INTO payout FROM public.payouts WHERE id = NEW.payout_id;
            -- The entry posted with the approval is this payout's return, and nothing else.
            IF NOT EXISTS (
                SELECT FROM public.ledger_journal_entries AS entry
                 WHERE entry.id = NEW.ledger_entry_id AND entry.entry_type = 'payout.returned'
                   AND entry.source_type = 'payout' AND entry.source_id = NEW.payout_id
                   AND entry.idempotency_key = 'payout:' || NEW.payout_id || ':' || 'returned'
            ) OR NOT EXISTS (
                SELECT FROM public.ledger_lines AS line
                  JOIN public.ledger_accounts AS account ON account.id = line.account_id
                 WHERE line.entry_id = NEW.ledger_entry_id
                   AND account.code = 'client.payout_pending'
                   AND account.owner_client_id = payout.client_id
                   AND account.reference = payout.source_reference
                   AND line.amount_minor = -payout.amount_minor
            ) THEN
                RAISE EXCEPTION 'an approved return carries the entry that took it back'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.ledger_entry_id IS NOT NULL THEN
            RAISE EXCEPTION 'only an approved return posts' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

STAFF = "public.finvest_actor_kind() = 'staff'"
OWN = (
    "public.finvest_actor_kind() = 'client' AND client_id = public.finvest_actor_id() "
    "AND status = 'approved'"
)

RULES = [
    "REVOKE ALL ON FUNCTION public.finvest_payout_returns_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.finvest_payout_return_evidence(public.payout_returns) "
    "FROM PUBLIC",
    # The routes ask it first, so a refusal can be named. It answers yes or no.
    f"GRANT EXECUTE ON FUNCTION public.finvest_payout_return_evidence(public.payout_returns) "
    f"TO {APP_ROLE}",
    f"CREATE TRIGGER {T}_guard BEFORE INSERT OR UPDATE OR DELETE ON {T} "
    "FOR EACH ROW EXECUTE FUNCTION public.finvest_payout_returns_guard()",
    f"CREATE TRIGGER {T}_no_truncate BEFORE TRUNCATE ON {T} "
    "FOR EACH STATEMENT EXECUTE FUNCTION public.finvest_payout_returns_guard()",
    f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY",
    f"REVOKE ALL ON {T} FROM PUBLIC",
    f"GRANT SELECT, INSERT ON {T} TO {APP_ROLE}",
    f"GRANT UPDATE (status, decided_by, decided_at, decision_reason, ledger_entry_id) ON {T} "
    f"TO {APP_ROLE}",
    f"GRANT SELECT ON {T} TO {WORKER_ROLE}",
    f"CREATE POLICY {T}_app_read ON {T} FOR SELECT TO {APP_ROLE} USING ({STAFF} OR ({OWN}))",
    f"CREATE POLICY {T}_worker_read ON {T} FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY {T}_app_insert ON {T} FOR INSERT TO {APP_ROLE} "
    f"WITH CHECK ({STAFF} AND proposed_by = public.finvest_actor_id())",
    f"CREATE POLICY {T}_app_update ON {T} FOR UPDATE TO {APP_ROLE} "
    f"USING ({STAFF}) WITH CHECK ({STAFF})",
]


def split(sql: str) -> list[str]:
    # asyncpg prepares each statement, so revisions execute one statement at a time.
    return [statement.strip() for statement in sql.split(";\n") if statement.strip()]


def upgrade() -> None:
    for statement in [*split(TABLE), EVIDENCE_FUNCTION, GUARD_FUNCTION, *RULES]:
        op.execute(statement)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payout_returns) THEN
        RAISE EXCEPTION 'refusing to drop the returns of payouts';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute("DROP FUNCTION public.finvest_payout_return_evidence(public.payout_returns)")
    op.execute(f"DROP TABLE {T}")
    op.execute("DROP FUNCTION public.finvest_payout_returns_guard()")
