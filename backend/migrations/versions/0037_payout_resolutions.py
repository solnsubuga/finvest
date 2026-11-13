"""Settle by hand, with two people, a payout the provider never answered for.

Revision ID: 0037_payout_resolutions
Revises: 0086_dealing_sale_funding

A sent or uncertain payout is read from the provider for three days. When the reads end with no
answer, the ioTec statement is the only evidence left, and a person must say how it ended. From
the owner's standing rule, that takes two people.

* One member of staff proposes an outcome with a reason and the evidence: for `paid`, the
  ioTec statement line that carries the payout's ID, going out, for its amount; for `failed`, an
  ioTec statement for a day at least three days after the send, with the payout's ID on no ioTec
  line at all. The database checks the evidence itself, and only once three days have passed
  since the send.
* A different member of staff approves it, in their own name, and in the same transaction the
  payout moves to that outcome and its ledger entry is posted. The evidence is checked again.
  They may instead decline it with a reason, and the proposer may withdraw it.
* The payouts guard now admits a staff step out of sent or uncertain only with an approved
  resolution for that outcome by the same person, and an approved resolution whose payout has
  not moved to its outcome cannot commit. The guard also pins its search path to `pg_catalog,
  pg_temp` and names what it calls in full.
* Nothing is updated except a decision, nothing is ever deleted, and a decided resolution never
  changes. Only staff see resolutions; the worker reads them.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0037_payout_resolutions"
down_revision: str | None = "0086_dealing_sale_funding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "finvest_app"
WORKER_ROLE = "finvest_worker"
T = "payout_resolutions"

TABLE = """
CREATE TABLE payout_resolutions (
    id push_id NOT NULL,
    payout_id push_id NOT NULL,
    outcome varchar(8) NOT NULL,
    statement_line_id push_id,
    statement_id push_id,
    reason varchar(500) NOT NULL,
    status varchar(16) NOT NULL,
    proposed_by push_id NOT NULL,
    proposed_at timestamptz NOT NULL,
    decided_by push_id,
    decided_at timestamptz,
    decision_reason varchar(500),
    CONSTRAINT pk_payout_resolutions PRIMARY KEY (id),
    CONSTRAINT fk_payout_resolutions_payout FOREIGN KEY (payout_id) REFERENCES payouts (id),
    CONSTRAINT fk_payout_resolutions_line
        FOREIGN KEY (statement_line_id) REFERENCES reconciliation_statement_lines (id),
    CONSTRAINT fk_payout_resolutions_statement
        FOREIGN KEY (statement_id) REFERENCES reconciliation_statements (id),
    CONSTRAINT ck_payout_resolutions_outcome CHECK (outcome IN ('paid', 'failed')),
    CONSTRAINT ck_payout_resolutions_evidence CHECK (
        (outcome = 'paid') = (statement_line_id IS NOT NULL)
        AND (outcome = 'failed') = (statement_id IS NOT NULL)
    ),
    CONSTRAINT ck_payout_resolutions_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_payout_resolutions_status
        CHECK (status IN ('proposed', 'approved', 'declined', 'withdrawn')),
    CONSTRAINT ck_payout_resolutions_decided CHECK (
        (status = 'proposed') = (decided_by IS NULL)
        AND (decided_by IS NULL) = (decided_at IS NULL)
    ),
    CONSTRAINT ck_payout_resolutions_two_people CHECK (
        status NOT IN ('approved', 'declined') OR decided_by <> proposed_by
    ),
    CONSTRAINT ck_payout_resolutions_withdrawn_by_proposer
        CHECK (status <> 'withdrawn' OR decided_by = proposed_by),
    CONSTRAINT ck_payout_resolutions_decline_reason
        CHECK (status <> 'declined' OR btrim(COALESCE(decision_reason, '')) <> '')
);
CREATE UNIQUE INDEX uq_payout_resolutions_open ON payout_resolutions (payout_id)
    WHERE status = 'proposed';
CREATE UNIQUE INDEX uq_payout_resolutions_approved ON payout_resolutions (payout_id)
    WHERE status = 'approved';
CREATE INDEX ix_payout_resolutions_status ON payout_resolutions (status, proposed_at)
"""

# Whether the statement still shows what the resolution claims. Read at proposal and approval.
EVIDENCE_FUNCTION = """
CREATE FUNCTION public.finvest_payout_resolution_evidence(
    resolution public.payout_resolutions
) RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        payout public.payouts;
        first_send timestamptz;
    BEGIN
        SELECT * INTO payout FROM public.payouts WHERE id = resolution.payout_id;
        IF NOT FOUND OR payout.status NOT IN ('sent', 'uncertain') THEN
            RETURN false;
        END IF;
        SELECT min(started_at) INTO first_send FROM public.payout_send_attempts
         WHERE payout_id = payout.id AND (outcome IS NULL OR outcome <> 'deferred');
        -- The provider is read for three days. Until then its answer is the one that counts.
        IF first_send IS NULL OR first_send > resolution.proposed_at - interval '3 days' THEN
            RETURN false;
        END IF;
        IF resolution.outcome = 'paid' THEN
            RETURN EXISTS (
                SELECT FROM public.reconciliation_statement_lines AS line
                  JOIN public.reconciliation_statements AS statement
                    ON statement.id = line.statement_id
                 WHERE line.id = resolution.statement_line_id AND statement.source = 'iotec'
                   AND line.reference = payout.id AND line.direction = 'out'
                   AND line.amount_minor = payout.amount_minor
                   AND statement.currency = payout.currency
            );
        END IF;
        RETURN EXISTS (
                SELECT FROM public.reconciliation_statements
                 WHERE id = resolution.statement_id AND source = 'iotec'
                   AND business_date
                       >= (first_send AT TIME ZONE 'Africa/Kampala')::date + 3
            )
            AND NOT EXISTS (
                SELECT FROM public.reconciliation_statement_lines AS line
                  JOIN public.reconciliation_statements AS statement
                    ON statement.id = line.statement_id
                 WHERE statement.source = 'iotec' AND line.reference = payout.id
            );
    END;
    $$
"""

GUARD_FUNCTION = """
CREATE FUNCTION public.finvest_payout_resolutions_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'a payout resolution is never deleted' USING ERRCODE = '42501';
        END IF;
        IF public.finvest_actor_kind() IS DISTINCT FROM 'staff' THEN
            RAISE EXCEPTION 'only staff resolve a payout' USING ERRCODE = '42501';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'proposed' OR NEW.proposed_by IS DISTINCT FROM public.finvest_actor_id()
               OR abs(extract(epoch FROM NEW.proposed_at - clock_timestamp())) > 300 THEN
                RAISE EXCEPTION 'a resolution starts proposed, by the person signed in, now'
                    USING ERRCODE = '42501';
            END IF;
            IF NOT public.finvest_payout_resolution_evidence(NEW) THEN
                RAISE EXCEPTION 'the statement does not show this outcome for this payout'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'proposed' OR NEW.status = 'proposed' THEN
            RAISE EXCEPTION 'a proposed resolution is decided once' USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.payout_id, NEW.outcome, NEW.statement_line_id, NEW.statement_id,
            NEW.reason, NEW.proposed_by, NEW.proposed_at)
           IS DISTINCT FROM
           (OLD.id, OLD.payout_id, OLD.outcome, OLD.statement_line_id, OLD.statement_id,
            OLD.reason, OLD.proposed_by, OLD.proposed_at) THEN
            RAISE EXCEPTION 'what was proposed does not change' USING ERRCODE = '42501';
        END IF;
        IF NEW.decided_by IS DISTINCT FROM public.finvest_actor_id()
           OR abs(extract(epoch FROM NEW.decided_at - clock_timestamp())) > 300 THEN
            RAISE EXCEPTION 'a resolution is decided by the person signed in, now'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.status = 'approved' AND NOT public.finvest_payout_resolution_evidence(NEW) THEN
            RAISE EXCEPTION 'the statement no longer shows this outcome for this payout'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# At commit, an approved resolution's payout must have reached that outcome.
SETTLED_FUNCTION = """
CREATE FUNCTION public.finvest_payout_resolutions_settled() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
    AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT FROM public.payouts WHERE id = NEW.payout_id AND status = NEW.outcome
        ) THEN
            RAISE EXCEPTION 'an approved resolution moves its payout in the same transaction'
                USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END;
    $$
"""

PAYOUTS_GUARD = """
CREATE OR REPLACE FUNCTION public.finvest_payouts_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        actor text := public.finvest_actor_kind();
        step text := OLD.status || '>' || NEW.status;
    BEGIN
        IF TG_OP <> 'UPDATE' THEN
            RAISE EXCEPTION 'payouts are never deleted' USING ERRCODE = '42501';
        END IF;
        IF OLD.status IN ('paid', 'failed', 'cancelled') THEN
            RAISE EXCEPTION 'a finished payout never changes' USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.client_id, NEW.destination_id, NEW.kind, NEW.amount_minor, NEW.currency,
            NEW.business_date, NEW.source_account_id, NEW.in_flight_account_id, NEW.request_key,
            NEW.source_reference, NEW.created_at)
               IS DISTINCT FROM
           (OLD.id, OLD.client_id, OLD.destination_id, OLD.kind, OLD.amount_minor, OLD.currency,
            OLD.business_date, OLD.source_account_id, OLD.in_flight_account_id, OLD.request_key,
            OLD.source_reference, OLD.created_at)
        THEN
            RAISE EXCEPTION 'the terms of a payout never change' USING ERRCODE = '42501';
        END IF;

        IF actor = 'client' AND step = 'held>cancelled' THEN
            IF NEW.reviewed_by IS NOT NULL THEN
                RAISE EXCEPTION 'a client signs no review' USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'staff' AND step IN ('held>queued', 'held>cancelled') THEN
            IF NEW.reviewed_by IS DISTINCT FROM public.finvest_actor_id() THEN
                RAISE EXCEPTION 'a review is recorded in the name of who made it'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'staff' AND step IN (
            'sent>paid', 'sent>failed', 'uncertain>paid', 'uncertain>failed'
        ) THEN
            -- Settled by hand from the statement: only by the second person, in the transaction
            -- where they approve someone else's proposal for this very outcome.
            IF NOT EXISTS (
                SELECT FROM public.payout_resolutions
                 WHERE payout_id = NEW.id AND status = 'approved' AND outcome = NEW.status
                   AND decided_by = public.finvest_actor_id()
            ) THEN
                RAISE EXCEPTION 'a payout is settled by hand only with an approved resolution'
                    USING ERRCODE = '42501';
            END IF;
            IF NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by THEN
                RAISE EXCEPTION 'a resolution signs the resolution, not the review'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'system' AND step IN (
            'queued>queued', 'queued>sent', 'queued>paid', 'queued>failed', 'queued>uncertain',
            'sent>sent', 'sent>paid', 'sent>failed',
            'uncertain>uncertain', 'uncertain>sent', 'uncertain>paid', 'uncertain>failed'
        ) THEN
            IF NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by THEN
                RAISE EXCEPTION 'the worker signs nothing in a person''s name'
                    USING ERRCODE = '42501';
            END IF;
        ELSE
            RAISE EXCEPTION 'this step is not allowed for a payout' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

# The guard as 0031 created it, restored on the way down.
OLD_PAYOUTS_GUARD = """
CREATE OR REPLACE FUNCTION finvest_payouts_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp
    AS $$
    DECLARE
        actor text := finvest_actor_kind();
        step text := OLD.status || '>' || NEW.status;
    BEGIN
        IF TG_OP <> 'UPDATE' THEN
            RAISE EXCEPTION 'payouts are never deleted' USING ERRCODE = '42501';
        END IF;
        IF OLD.status IN ('paid', 'failed', 'cancelled') THEN
            RAISE EXCEPTION 'a finished payout never changes' USING ERRCODE = '42501';
        END IF;
        IF (NEW.id, NEW.client_id, NEW.destination_id, NEW.kind, NEW.amount_minor, NEW.currency,
            NEW.business_date, NEW.source_account_id, NEW.in_flight_account_id, NEW.request_key,
            NEW.source_reference, NEW.created_at)
               IS DISTINCT FROM
           (OLD.id, OLD.client_id, OLD.destination_id, OLD.kind, OLD.amount_minor, OLD.currency,
            OLD.business_date, OLD.source_account_id, OLD.in_flight_account_id, OLD.request_key,
            OLD.source_reference, OLD.created_at)
        THEN
            RAISE EXCEPTION 'the terms of a payout never change' USING ERRCODE = '42501';
        END IF;

        IF actor = 'client' AND step = 'held>cancelled' THEN
            IF NEW.reviewed_by IS NOT NULL THEN
                RAISE EXCEPTION 'a client signs no review' USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'staff' AND step IN ('held>queued', 'held>cancelled') THEN
            IF NEW.reviewed_by IS DISTINCT FROM finvest_actor_id() THEN
                RAISE EXCEPTION 'a review is recorded in the name of who made it'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF actor = 'system' AND step IN (
            'queued>queued', 'queued>sent', 'queued>paid', 'queued>failed', 'queued>uncertain',
            'sent>sent', 'sent>paid', 'sent>failed',
            'uncertain>uncertain', 'uncertain>sent', 'uncertain>paid', 'uncertain>failed'
        ) THEN
            IF NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by THEN
                RAISE EXCEPTION 'the worker signs nothing in a person''s name'
                    USING ERRCODE = '42501';
            END IF;
        ELSE
            RAISE EXCEPTION 'this step is not allowed for a payout' USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END;
    $$
"""

STAFF = "public.finvest_actor_kind() = 'staff'"

RULES = [
    "REVOKE ALL ON FUNCTION public.finvest_payout_resolution_evidence(public.payout_resolutions) "
    "FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.finvest_payout_resolutions_guard() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION public.finvest_payout_resolutions_settled() FROM PUBLIC",
    f"CREATE TRIGGER {T}_guard BEFORE INSERT OR UPDATE OR DELETE ON {T} "
    "FOR EACH ROW EXECUTE FUNCTION public.finvest_payout_resolutions_guard()",
    f"CREATE TRIGGER {T}_no_truncate BEFORE TRUNCATE ON {T} "
    "FOR EACH STATEMENT EXECUTE FUNCTION public.finvest_payout_resolutions_guard()",
    f"CREATE CONSTRAINT TRIGGER {T}_settled AFTER UPDATE ON {T} DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW WHEN (NEW.status = 'approved') "
    "EXECUTE FUNCTION public.finvest_payout_resolutions_settled()",
    f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY",
    f"REVOKE ALL ON {T} FROM PUBLIC",
    f"GRANT SELECT, INSERT ON {T} TO {APP_ROLE}",
    f"GRANT UPDATE (status, decided_by, decided_at, decision_reason) ON {T} TO {APP_ROLE}",
    f"GRANT SELECT ON {T} TO {WORKER_ROLE}",
    f"CREATE POLICY {T}_app_read ON {T} FOR SELECT TO {APP_ROLE} USING ({STAFF})",
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
    for statement in [*split(TABLE), EVIDENCE_FUNCTION, GUARD_FUNCTION, SETTLED_FUNCTION]:
        op.execute(statement)
    for statement in RULES:
        op.execute(statement)
    op.execute(PAYOUTS_GUARD)


EMPTY_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM payout_resolutions) THEN
        RAISE EXCEPTION 'refusing to drop the resolutions of payouts';
    END IF;
END
$$
"""


def downgrade() -> None:
    op.execute(EMPTY_GUARD)
    op.execute(OLD_PAYOUTS_GUARD)
    op.execute("DROP FUNCTION public.finvest_payout_resolution_evidence(public.payout_resolutions)")
    op.execute(f"DROP TABLE {T}")
    op.execute("DROP FUNCTION public.finvest_payout_resolutions_settled()")
    op.execute("DROP FUNCTION public.finvest_payout_resolutions_guard()")
